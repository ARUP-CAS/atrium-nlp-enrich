#!/usr/bin/env python3
"""
conllu_to_doccano.py  –  Build a doccano import file (JSONL) for archaeo NER
annotation from tokenised input.

Accepts either
  * a CoNLL-U file (UDPipe output; optional pre-annotations read from the MISC
    column's ``NER=`` feature), or
  * a two/three-column IOB2 TSV as written by ``api_util/call_nametag.py``
    (``Word<TAB>Tag[<TAB>NE]``).

One doccano example is emitted per document: ``text`` is the document's sentences
joined by newlines and its tokens joined by single spaces, and ``label`` holds the
character spans ``[start, end, TYPE]`` reconstructed from the IOB2 pre-annotations
(empty when the input carries no tags). Because the text is a deterministic
space/newline join of the tokens, ``doccano_to_iob2.py`` recovers exactly the same
tokens on export, so the round-trip stays token-aligned.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import TextIO

# A document is a list of sentences; a sentence is a list of (form, tag) pairs,
# where tag is an IOB2 label (possibly pipe-joined, e.g. "B-A|B-B") or "O".
Sentence = list[tuple[str, str]]
Document = list[Sentence]


def _tag_to_pairs(tag: str) -> dict[str, str]:
    """Parse an IOB2 tag (possibly ``B-A|I-B``) into ``{type: prefix}``."""
    pairs: dict[str, str] = {}
    if not tag or tag in ("O", "_"):
        return pairs
    for part in tag.split("|"):
        part = part.strip()
        if not part or part == "O":
            continue
        if "-" in part:
            prefix, typ = part.split("-", 1)
        else:
            prefix, typ = "B", part
        pairs[typ] = prefix
    return pairs


def _sentence_spans(sent: Sentence, base: int) -> tuple[str, list[list]]:
    """Render one sentence to text and IOB2-derived char spans (global offsets).

    ``base`` is the global character offset at which this sentence starts.
    Returns the sentence text (tokens space-joined) and a list of
    ``[start, end, TYPE]`` spans in global coordinates. Entities never cross a
    sentence boundary.
    """
    text_parts: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    pos = base
    for i, (form, _tag) in enumerate(sent):
        if i > 0:
            text_parts.append(" ")
            pos += 1
        starts.append(pos)
        text_parts.append(form)
        pos += len(form)
        ends.append(pos)

    spans: list[list] = []
    open_start: dict[str, int] = {}  # type -> entity start char
    open_end: dict[str, int] = {}  # type -> entity end char so far
    for i, (_form, tag) in enumerate(sent):
        pairs = _tag_to_pairs(tag)
        # close entities not continued by an I- of the same type
        for typ in list(open_start):
            if pairs.get(typ) != "I":
                spans.append([open_start.pop(typ), open_end.pop(typ), typ])
        # open (B- or bare type) or extend (I-) entities
        for typ, prefix in pairs.items():
            if prefix == "B" or typ not in open_start:
                open_start[typ] = starts[i]
                open_end[typ] = ends[i]
            else:  # continuing I-
                open_end[typ] = ends[i]
    for typ in list(open_start):
        spans.append([open_start[typ], open_end[typ], typ])

    return "".join(text_parts), spans


def document_to_example(doc: Document, doc_id: str | None) -> dict:
    """Turn one document into a doccano JSONL example object."""
    texts: list[str] = []
    labels: list[list] = []
    pos = 0
    for si, sent in enumerate(doc):
        if si > 0:
            texts.append("\n")
            pos += 1
        stext, sspans = _sentence_spans(sent, pos)
        texts.append(stext)
        labels.extend(sspans)
        pos += len(stext)
    example: dict = {"text": "".join(texts), "label": labels}
    if doc_id:
        example["meta"] = {"doc_id": doc_id}
    return example


def parse_conllu(fh: TextIO, ner_from_misc: bool) -> list[tuple[str | None, Document]]:
    """Parse a CoNLL-U file into (doc_id, document) pairs."""
    docs: list[tuple[str | None, Document]] = []
    cur_doc: Document = []
    cur_sent: Sentence = []
    cur_id: str | None = None

    def end_sentence() -> None:
        nonlocal cur_sent
        if cur_sent:
            cur_doc.append(cur_sent)
            cur_sent = []

    def end_document() -> None:
        nonlocal cur_doc, cur_id
        end_sentence()
        if cur_doc:
            docs.append((cur_id, cur_doc))
            cur_doc = []
            cur_id = None

    for raw in fh:
        line = raw.rstrip("\n")
        stripped = line.strip()
        if stripped.startswith("# newdoc"):
            end_document()
            cur_id = stripped.split("=", 1)[1].strip() if "=" in stripped else None
            continue
        if stripped.startswith("#"):
            continue
        if not stripped:
            end_sentence()
            continue
        cols = line.split("\t")
        if len(cols) < 2:
            continue
        tok_id = cols[0]
        if "-" in tok_id or "." in tok_id:  # skip multiword ranges / empty nodes
            continue
        form = cols[1]
        tag = "O"
        if ner_from_misc and len(cols) >= 10:
            for feat in cols[9].split("|"):
                if feat.startswith("NER="):
                    tag = feat[len("NER=") :] or "O"
                    break
        cur_sent.append((form, tag))
    end_document()
    return docs


def parse_tsv(fh: TextIO) -> list[tuple[str | None, Document]]:
    """Parse an IOB2 TSV (``Word<TAB>Tag[<TAB>NE]``) into (doc_id, document) pairs.

    Blank lines separate sentences; a ``-DOCSTART-`` line separates documents; a
    leading ``Word<TAB>Tag`` header line (as written by call_nametag.py) is skipped.
    """
    docs: list[tuple[str | None, Document]] = []
    cur_doc: Document = []
    cur_sent: Sentence = []
    doc_idx = 0

    def end_sentence() -> None:
        nonlocal cur_sent
        if cur_sent:
            cur_doc.append(cur_sent)
            cur_sent = []

    def end_document() -> None:
        nonlocal cur_doc, doc_idx
        end_sentence()
        if cur_doc:
            docs.append((f"doc{doc_idx}", cur_doc))
            cur_doc = []
            doc_idx += 1

    for raw in fh:
        line = raw.rstrip("\n")
        if not line.strip():
            end_sentence()
            continue
        cols = line.split("\t")
        word = cols[0]
        if word == "-DOCSTART-":
            end_document()
            continue
        if word.lower() == "word" and len(cols) >= 2 and cols[1].lower() == "tag":
            continue  # header row
        tag = cols[1] if len(cols) >= 2 and cols[1] else "O"
        cur_sent.append((word, tag))
    end_document()
    return docs


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--conllu", help="Input CoNLL-U file (UDPipe output).")
    src.add_argument("--tsv", help="Input IOB2 TSV (Word<TAB>Tag[<TAB>NE]).")
    ap.add_argument("--output", "-o", help="Output JSONL file (default: stdout).")
    ap.add_argument(
        "--ner-from-misc",
        action="store_true",
        help="For --conllu: seed pre-annotations from the MISC 'NER=' feature.",
    )
    args = ap.parse_args()

    path = args.conllu or args.tsv
    with open(path, encoding="utf-8") as fh:
        docs = parse_conllu(fh, args.ner_from_misc) if args.conllu else parse_tsv(fh)

    out: TextIO = open(args.output, "w", encoding="utf-8") if args.output else sys.stdout
    n = 0
    try:
        for doc_id, doc in docs:
            out.write(json.dumps(document_to_example(doc, doc_id), ensure_ascii=False) + "\n")
            n += 1
    finally:
        if args.output:
            out.close()
    print(
        f"[conllu_to_doccano] wrote {n} document(s) to {args.output or 'stdout'}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
