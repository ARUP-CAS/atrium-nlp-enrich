#!/usr/bin/env python3
"""
call_udpipe.py  –  Send pre-chunked text files to the UDPipe 2 API and write
a single merged CoNLL-U file.
"""

import argparse
import glob
import os
import sys
import time
from tenacity import Retrying, stop_after_attempt, wait_exponential, retry_if_exception_type

try:
    import requests
except ImportError:
    print("[Error] 'requests' library is required. Run: pip install requests", file=sys.stderr)
    sys.exit(1)

UDPIPE_URL = "https://lindat.mff.cuni.cz/services/udpipe/api/process"

def call_udpipe(text: str, model: str, url: str, timeout: int, retries: int) -> str | None:
    try:
        for attempt in Retrying(
            retry=retry_if_exception_type((requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.HTTPError)),
            stop=stop_after_attempt(retries),
            wait=wait_exponential(multiplier=1, min=1, max=30),
            reraise=True
        ):
            with attempt:
                resp = requests.post(
                    url,
                    data={
                        "model": model,
                        "tokenizer": "",
                        "tagger": "",
                        "parser": "",
                        "data": text,
                    },
                    timeout=timeout,
                )
                resp.raise_for_status()
                data = resp.json()
                result = data.get("result", "")
                if result:
                    return result
                print("  [WARN] UDPipe returned empty result", file=sys.stderr)
    except Exception as exc:
        print(f"  [WARN] UDPipe error: {exc}", file=sys.stderr)

    return None

def merge_conllu_chunks(chunks: list[str]) -> str:
    out_lines: list[str] = []
    global_sent_offset = 0

    for chunk_text in chunks:
        lines = chunk_text.splitlines(keepends=True)
        chunk_sent_ids: list[int] = []

        for line in lines:
            if line.startswith("# sent_id"):
                try:
                    val = int(line.split("=", 1)[1].strip())
                    chunk_sent_ids.append(val)
                except ValueError:
                    pass

        chunk_max = max(chunk_sent_ids, default=0)

        for line in lines:
            if line.startswith("# sent_id"):
                try:
                    val = int(line.split("=", 1)[1].strip())
                    if val == 1 and global_sent_offset > 0:
                        out_lines.append("# page_break = true\n")
                    new_val = val + global_sent_offset
                    out_lines.append(f"# sent_id = {new_val}\n")
                except ValueError:
                    out_lines.append(line)
            else:
                out_lines.append(line)

        global_sent_offset += chunk_max

    return "".join(out_lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Send chunked text to UDPipe API and write merged CoNLL-U."
    )
    parser.add_argument("--chunk-dir", required=True, help="Directory containing chunk_*.txt")
    parser.add_argument("--model", required=True, help="UDPipe model identifier.")
    parser.add_argument("--output", required=True, help="Output CoNLL-U file path.")
    parser.add_argument("--url", default=os.environ.get("UDPIPE_URL", UDPIPE_URL), help="API endpoint URL.")
    parser.add_argument("--timeout", type=int, default=60, help="Request timeout in seconds.")
    parser.add_argument("--retries", type=int, default=5, help="Max retry attempts.")
    args = parser.parse_args()

    chunk_files = sorted(glob.glob(os.path.join(args.chunk_dir, "chunk_*.txt")))
    if not chunk_files:
        print(f"[Error] No chunk files found in {args.chunk_dir}", file=sys.stderr)
        sys.exit(1)

    conllu_chunks: list[str] = []
    for cf in chunk_files:
        with open(cf, "r", encoding="utf-8") as fh:
            text = fh.read().strip()
        if not text:
            continue

        print(f"  [UDPipe] Processing {os.path.basename(cf)} ({len(text.split())} words)…")
        result = call_udpipe(text, args.model, args.url, args.timeout, args.retries)
        if result is None:
            print(f"[Error] UDPipe failed permanently on {cf}. Aborting.", file=sys.stderr)
            sys.exit(1)
        conllu_chunks.append(result)

    if not conllu_chunks:
        print("[Error] All chunks were empty; nothing to write.", file=sys.stderr)
        sys.exit(1)

    merged = merge_conllu_chunks(conllu_chunks)
    out_dir = os.path.dirname(args.output)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    with open(args.output, "w", encoding="utf-8") as fh:
        fh.write(merged)

    n_sents = merged.count("# sent_id")
    print(f"[UDPipe] Written {args.output}  ({n_sents} sentences from {len(chunk_files)} chunks)")

if __name__ == "__main__":
    main()