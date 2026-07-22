#!/usr/bin/env python3
"""
doccano_to_iob2.py  –  Convert a doccano NER export (JSONL) into NameTag 3
IOB2 training data.

Reads doccano's "JSONL (Text-Label)" export, where each line is
``{"text": ..., "label": [[start, end, "TYPE"], ...]}``. The reader also tolerates
the ``entities`` / ``annotations`` / ``labels`` keys and object-form spans
``{"start_offset", "end_offset", "label"}``.

Character spans are re-aligned to whitespace tokens and written as a vertical
file: one ``token<TAB>label`` line per token, ``-DOCSTART-<TAB>O`` + blank line at
each document start, a blank line between sentences (newlines in the ``text``),
and ``|``-joined labels wherever spans overlap (e.g.
``bronze -> B-MATERIAL|B-ARTEFACT``). This matches the format consumed by
``api_util/call_nametag.py`` (see ``_get_ne_suffix``) and NameTag 3 training.

Run the result through NameTag 3's ``preprocessing/iob_to_iob2.py`` to normalise
and validate before training.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any, TextIO

_TOKEN_RE = re.compile(r"\S+")

# (start_char, end_char, form, sentence_index)
Token = tuple[int, int, str, int]


def extract_spans(obj: dict) -> list[tuple[int, int, str]]:
    """Pull ``(start, end, label)`` character spans out of a doccano example."""
    raw = None
    for key in ("label", "entities", "annotations", "labels"):
        if obj.get(key) is not None:
            raw = obj[key]
            break
    spans: list[tuple[int, int, str]] = []
    if not raw:
        return spans
    for item in raw:
        if isinstance(item, dict):
            start = item.get("start_offset", item.get("start"))
            end = item.get("end_offset", item.get("end"))
            label = item.get("label")
        elif isinstance(item, (list, tuple)) and len(item) >= 3:
            start, end, label = item[0], item[1], item[2]
        else:
            continue
        if start is None or end is None or label is None:
            continue
        spans.append((int(start), int(end), str(label)))
    return spans


def tokenize(text: str) -> list[Token]:
    """Tokenise ``text`` on whitespace, tracking char offsets and sentence index.

    Sentences are delimited by newlines; tokens by any other whitespace. Offsets
    are code-point positions into ``text`` (matching doccano span offsets).
    """
    tokens: list[Token] = []
    line_start = 0
    for sent_idx, line in enumerate(text.split("\n")):
        for m in _TOKEN_RE.finditer(line):
            tokens.append((line_start + m.start(), line_start + m.end(), m.group(), sent_idx))
        line_start += len(line) + 1  # +1 for the '\n' consumed by split
    return tokens


def assign_tags(tokens: list[Token], spans: list[tuple[int, int, str]]) -> list[str]:
    """Assign a (possibly pipe-joined) IOB2 tag to every token."""
    parts_per_token: list[list[str]] = [[] for _ in tokens]
    for es, ee, label in spans:
        first = True
        for ti, (ts, te, _form, _si) in enumerate(tokens):
            if ts < ee and te > es:  # token intersects the entity span
                parts_per_token[ti].append(("B-" if first else "I-") + label)
                first = False
    return ["|".join(sorted(parts)) if parts else "O" for parts in parts_per_token]


def emit_document(tokens: list[Token], tags: list[str], out: TextIO) -> None:
    """Write one document as an IOB2 block (DOCSTART, sentences, trailing blank)."""
    out.write("-DOCSTART-\tO\n\n")
    prev_sent: int | None = None
    for (_ts, _te, form, si), tag in zip(tokens, tags, strict=True):
        if prev_sent is not None and si != prev_sent:
            out.write("\n")
        out.write(f"{form}\t{tag}\n")
        prev_sent = si
    out.write("\n")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--input", "-i", required=True, help="doccano export JSONL.")
    ap.add_argument("--output", "-o", help="Output IOB2 file (default: stdout).")
    args = ap.parse_args()

    out: TextIO = open(args.output, "w", encoding="utf-8") if args.output else sys.stdout
    n_docs = 0
    try:
        with open(args.input, encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                obj: dict[str, Any] = json.loads(raw)
                text = obj.get("text", "")
                if not text.strip():
                    continue
                tokens = tokenize(text)
                if not tokens:
                    continue
                tags = assign_tags(tokens, extract_spans(obj))
                emit_document(tokens, tags, out)
                n_docs += 1
    finally:
        if args.output:
            out.close()
    print(
        f"[doccano_to_iob2] wrote {n_docs} document(s) to {args.output or 'stdout'}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
