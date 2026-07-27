#!/usr/bin/env python3
"""
labelstudio_to_iob2.py  –  Convert a Label Studio NER export (JSON) into NameTag 3
IOB2 training data.

Reads Label Studio's JSON export, where each item contains
``{"data": {"text": ...}, "annotations": [{"result": [{"value": {"start": 0, "end": 5, "labels": ["TYPE"]}}]}]}``.

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
from typing import TextIO

_TOKEN_RE = re.compile(r"\S+")

# (start_char, end_char, form, sentence_index)
Token = tuple[int, int, str, int]


def extract_spans(obj: dict) -> list[tuple[int, int, str]]:
    """Pull ``(start, end, label)`` character spans out of a Label Studio example."""
    spans: list[tuple[int, int, str]] = []
    annotations = obj.get("annotations", [])
    if not annotations:
        return spans

    # Use the most recent annotation
    latest_annotation = annotations[-1]
    for result in latest_annotation.get("result", []):
        if result.get("type") == "labels":
            val = result.get("value", {})
            start = val.get("start")
            end = val.get("end")
            labels = val.get("labels", [])
            if start is not None and end is not None and labels:
                spans.append((int(start), int(end), str(labels[0])))
    return spans


def tokenize(text: str) -> list[Token]:
    """Tokenise ``text`` on whitespace, tracking char offsets and sentence index.

    Sentences are delimited by newlines; tokens by any other whitespace. Offsets
    are code-point positions into ``text`` (matching span offsets).
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
    ap.add_argument("--input", "-i", required=True, help="Label Studio export JSON.")
    ap.add_argument("--output", "-o", help="Output IOB2 file (default: stdout).")
    args = ap.parse_args()

    out: TextIO = open(args.output, "w", encoding="utf-8") if args.output else sys.stdout
    n_docs = 0
    try:
        with open(args.input, encoding="utf-8") as fh:
            data = json.load(fh)
            # Label Studio exports as a single JSON array
            if not isinstance(data, list):
                data = [data]

            for obj in data:
                text = obj.get("data", {}).get("text", "")
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
        f"[labelstudio_to_iob2] wrote {n_docs} document(s) to {args.output or 'stdout'}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
