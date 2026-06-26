#!/usr/bin/env python3
"""
call_udpipe.py  –  Send pre-chunked text files to the UDPipe 2 API,
automatically retry on network failures, and write a single merged CoNLL-U file.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from functools import lru_cache
from typing import Any

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ImportError:
    print(
        "[Error] 'requests' library is required. Run: pip install requests urllib3", file=sys.stderr
    )
    sys.exit(1)

UDPIPE_URL = "https://lindat.mff.cuni.cz/services/udpipe/api/process"


@lru_cache(maxsize=1)
def _lazy_load_transformers():
    from transformers import pipeline  # type: ignore

    return pipeline


def run_udpipe_like_workflow(text: str) -> Any:
    """
    Load heavy dependencies only when this function is called.
    """
    pipeline = _lazy_load_transformers()

    # Replace with the repository's actual model/task name.
    nlp = pipeline("text-classification")
    return nlp(text)


def get_robust_session(retries: int) -> requests.Session:
    """Configures a requests session with exponential backoff for 429/5xx errors."""
    session = requests.Session()
    retry_strategy = Retry(
        total=retries,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["POST"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def process_chunk(session: requests.Session, text: str, model: str, timeout: int) -> str:
    """Send a single text chunk to UDPipe."""
    data = {
        "model": model,
        "tokenizer": "",
        "tagger": "",
        "parser": "",
        "data": text,
    }
    # If the session exhausts retries or hits a timeout, this will raise a RequestException
    response = session.post(UDPIPE_URL, data=data, timeout=timeout)
    response.raise_for_status()

    result = response.json().get("result", "")
    if not result:
        print("  [WARN] UDPipe returned empty result for chunk.", file=sys.stderr)
    return result


def merge_conllu_chunks(chunks: list[str]) -> str:
    """Merges multiple UDPipe CoNLL-U outputs, recalculating sent_id."""
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


def main():
    parser = argparse.ArgumentParser(description="Call UDPipe API with retries.")
    parser.add_argument(
        "--chunk-dir", required=True, help="Directory containing chunk_X.txt files."
    )
    parser.add_argument("--model", required=True, help="UDPipe model identifier.")
    parser.add_argument("--output", required=True, help="Output merged CoNLL-U file.")
    parser.add_argument("--timeout", type=int, default=60, help="Request timeout in seconds.")
    parser.add_argument("--retries", type=int, default=5, help="Max retry attempts for 5xx errors.")
    args = parser.parse_args()

    # Find and sort chunks numerically (chunk_0.txt, chunk_1.txt...)
    chunk_files = glob.glob(os.path.join(args.chunk_dir, "chunk_*.txt"))
    chunk_files.sort(key=lambda x: int(re.search(r"chunk_(\d+)", x).group(1)))

    if not chunk_files:
        print(f"[Error] No chunk files found in {args.chunk_dir}", file=sys.stderr)
        sys.exit(1)

    session = get_robust_session(args.retries)
    processed_chunks = []

    for idx, cfile in enumerate(chunk_files):
        with open(cfile, "r", encoding="utf-8") as f:
            text = f.read().strip()

        if not text:
            continue

        try:
            print(f"  [UDPipe] Processing chunk {idx + 1}/{len(chunk_files)}...")
            result = process_chunk(session, text, args.model, args.timeout)
            if result:
                processed_chunks.append(result)
        except requests.exceptions.RequestException as e:
            print(
                f"[CRITICAL] UDPipe API failed permanently on chunk {idx + 1}. Error: {e}",
                file=sys.stderr,
            )
            sys.exit(1)

    if not processed_chunks:
        print("[Error] All UDPipe chunks returned empty.", file=sys.stderr)
        sys.exit(1)

    merged_conllu = merge_conllu_chunks(processed_chunks)

    # Ensure output directory exists
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as out_f:
        out_f.write(merged_conllu)

    print(f"  [UDPipe] Successfully merged {len(processed_chunks)} chunks into {args.output}")


if __name__ == "__main__":
    main()
