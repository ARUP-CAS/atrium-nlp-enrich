#!/usr/bin/env python3
"""
call_nametag.py  –  Send a CoNLL-U file to the NameTag 3 API, receive NER
annotations, and write per-page TSV files.

The per-page split follows the same convention as nametag.py: a new page begins
whenever sent_id resets to 1.  Output files are named
    <doc_id>-<page_num>.tsv

Usage
-----
    python3 call_nametag.py \\
        --input     OUTPUT/UDP/doc_id.conllu \\
        --model     nametag3-czech-cnec2.0-240830 \\
        --output-dir OUTPUT/NE/doc_id \\
        [--url URL] [--timeout 60] [--retries 5]
"""

import argparse
import json
import os
import sys
import time
from collections import defaultdict

try:
    import requests
except ImportError:
    print("[Error] 'requests' is required. Run: pip install requests", file=sys.stderr)
    sys.exit(1)

NAMETAG_URL = "https://lindat.mff.cuni.cz/services/nametag/api/recognize"


# ── API call ──────────────────────────────────────────────────────────────────

def call_nametag(conllu_text: str, model: str, url: str, timeout: int, retries: int) -> dict | None:
    """POST CoNLL-U text to NameTag and return the parsed JSON dict."""
    delay = 1.0
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(
                url,
                data={
                    "model": model,
                    "input": "conllu",
                    "output": "conll",
                    "data": conllu_text,
                },
                timeout=timeout,
            )
            if resp.status_code == 200:
                return resp.json()
            print(
                f"  [WARN] NameTag HTTP {resp.status_code} (attempt {attempt})",
                file=sys.stderr,
            )
        except requests.exceptions.Timeout:
            print(f"  [WARN] NameTag timed out (attempt {attempt})", file=sys.stderr)
        except Exception as exc:
            print(f"  [WARN] NameTag error: {exc} (attempt {attempt})", file=sys.stderr)

        if attempt < retries:
            time.sleep(delay)
            delay = delay * 1.5 + 1

    return None


# ── sent_id → page mapping ────────────────────────────────────────────────────

def build_sent_page_map(conllu_path: str) -> list[int]:
    """Return a list mapping sentence index (0-based) → page number (1-based).

    A new page starts whenever '# sent_id = 1' is encountered.
    """
    sent_to_page: list[int] = []
    current_page = 0
    try:
        with open(conllu_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line.startswith("# sent_id"):
                    continue
                if "=" in line:
                    val = line.split("=", 1)[1].strip()
                    if val == "1":
                        current_page += 1
                if current_page == 0:
                    current_page = 1
                sent_to_page.append(current_page)
    except Exception as exc:
        print(f"[Error] reading CoNLL-U {conllu_path}: {exc}", file=sys.stderr)
    return sent_to_page


# ── NE suffix helper ──────────────────────────────────────────────────────────

def _get_ne_suffix(tag: str) -> str:
    if not tag:
        return ""
    parts = tag.split("|")
    suffixes = []
    for t in parts:
        if t.startswith("B-") or t.startswith("I-"):
            suffixes.append(t.split("-", 1)[1])
        else:
            suffixes.append("")
    return "|".join(suffixes)


# ── response → per-page TSV ───────────────────────────────────────────────────

def write_tsv_files(response_json: dict, sent_to_page: list[int], out_dir: str, doc_id: str) -> int:
    """Parse NameTag JSON result and write per-page TSV files.

    Returns the number of TSV files written.
    """
    tagged = response_json.get("result", "")
    sentences = [s for s in tagged.strip().split("\n\n") if s.strip()]

    tokens_by_page: defaultdict[int, list[tuple[str, str]]] = defaultdict(list)

    for idx, sent_block in enumerate(sentences):
        page_num = sent_to_page[idx] if idx < len(sent_to_page) else (max(sent_to_page, default=1))
        for line in sent_block.split("\n"):
            if line.startswith("#"):
                continue
            cols = line.split("\t")
            if len(cols) < 2:
                continue
            word, tag = cols[0], cols[1]
            tokens_by_page[page_num].append((word, tag))

    os.makedirs(out_dir, exist_ok=True)
    for page_num, token_list in tokens_by_page.items():
        out_path = os.path.join(out_dir, f"{doc_id}-{page_num}.tsv")
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write("Word\tTag\tNE\n")
            for word, tag in token_list:
                fh.write(f"{word}\t{tag}\t{_get_ne_suffix(tag)}\n")

    return len(tokens_by_page)


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Send CoNLL-U to NameTag API and write per-page TSV files."
    )
    parser.add_argument("--input", required=True, help="Input CoNLL-U file.")
    parser.add_argument("--model", required=True, help="NameTag model identifier.")
    parser.add_argument("--output-dir", required=True, help="Directory for per-page TSV output.")
    parser.add_argument(
        "--url", default=os.environ.get("NAMETAG_URL", NAMETAG_URL),
        help="NameTag API endpoint URL.",
    )
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--retries", type=int, default=5)
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        print(f"[Error] CoNLL-U file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    doc_id = os.path.splitext(os.path.basename(args.input))[0]

    # Build sent_id → page mapping from the original CoNLL-U
    sent_to_page = build_sent_page_map(args.input)

    # Read full CoNLL-U text for the API call
    with open(args.input, "r", encoding="utf-8") as fh:
        conllu_text = fh.read()

    print(f"  [NameTag] Sending {doc_id} ({len(sent_to_page)} sentences)…")
    response_json = call_nametag(conllu_text, args.model, args.url, args.timeout, args.retries)

    if response_json is None:
        print(f"[Error] NameTag failed permanently for {doc_id}.", file=sys.stderr)
        sys.exit(1)

    n_pages = write_tsv_files(response_json, sent_to_page, args.output_dir, doc_id)
    print(f"  [NameTag] Written {n_pages} page TSV file(s) → {args.output_dir}")


if __name__ == "__main__":
    main()