#!/usr/bin/env python3
"""
call_udpipe.py  –  Send pre-chunked text files to the UDPipe 2 API and write
a single merged CoNLL-U file.

Chunk files must be named  chunk_0.txt, chunk_1.txt, …  (produced by chunk.py).
The sent_id comment sequence is renumbered across chunks so the merged file is
consistent.

Page-boundary signal
--------------------
The original design used ``# sent_id = 1`` resets to mark the start of a new
page.  After global renumbering those resets are gone, so a ``# page_break =
true`` comment is injected immediately *before* every renumbered sentence that
had ``sent_id = 1`` in its source chunk (except the very first sentence of the
entire document, which always remains ``sent_id = 1``).

Downstream consumers (call_nametag.py, summarize_nt_udp.py, teitok_alto.py)
detect **either** ``# page_break = true`` (merged files) **or** ``sent_id = 1``
(original single-chunk / legacy files), so both conventions work transparently.

Usage
-----
    python3 call_udpipe.py \\
        --chunk-dir TEMP/CHUNKS/doc_id \\
        --model     czech-pdt-ud-2.15-241121 \\
        --output    OUTPUT/UDP/doc_id.conllu \\
        [--url URL] [--timeout 60] [--retries 5]
"""

import argparse
import glob
import os
import sys
import time

try:
    import requests
except ImportError:
    print("[Error] 'requests' library is required. Run: pip install requests", file=sys.stderr)
    sys.exit(1)

UDPIPE_URL = "https://lindat.mff.cuni.cz/services/udpipe/api/process"


# ── API call ──────────────────────────────────────────────────────────────────

def call_udpipe(text: str, model: str, url: str, timeout: int, retries: int) -> str | None:
    """POST *text* to UDPipe and return the CoNLL-U string, or None on failure."""
    delay = 1.0
    for attempt in range(1, retries + 1):
        try:
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
            if resp.status_code == 200:
                data = resp.json()
                result = data.get("result", "")
                if result:
                    return result
                print(
                    f"  [WARN] UDPipe returned empty result (attempt {attempt})",
                    file=sys.stderr,
                )
            else:
                print(
                    f"  [WARN] UDPipe HTTP {resp.status_code} (attempt {attempt})",
                    file=sys.stderr,
                )
        except requests.exceptions.Timeout:
            print(f"  [WARN] UDPipe timed out (attempt {attempt})", file=sys.stderr)
        except Exception as exc:
            print(f"  [WARN] UDPipe error: {exc} (attempt {attempt})", file=sys.stderr)

        if attempt < retries:
            time.sleep(delay)
            delay = delay * 1.5 + 1

    return None


# ── CoNLL-U merge ─────────────────────────────────────────────────────────────

def merge_conllu_chunks(chunks: list[str]) -> str:
    """
    Concatenate CoNLL-U chunks and renumber sent_id globally so the merged
    file is valid (no duplicate sent_id values across chunks).

    FIX #3: Page-boundary preservation.
    When a source chunk contains a ``sent_id = 1`` reset (signalling that a
    new page starts there), a ``# page_break = true`` comment is injected
    immediately before the renumbered sent_id line — *except* for the very
    first sentence of the document, which retains ``sent_id = 1`` so that
    legacy parsers that only check ``sent_id = 1`` still see the first page
    boundary correctly.

    Downstream parsers should detect EITHER signal:
        • ``# page_break = true``   → new page in merged/renumbered files
        • ``# sent_id = 1``         → new page in original / legacy files
    """
    out_lines: list[str] = []
    global_sent_offset = 0

    for chunk_text in chunks:
        lines = chunk_text.splitlines(keepends=True)
        chunk_sent_ids: list[int] = []

        # First pass: collect the sent_id values present in this chunk.
        for line in lines:
            if line.startswith("# sent_id"):
                try:
                    val = int(line.split("=", 1)[1].strip())
                    chunk_sent_ids.append(val)
                except ValueError:
                    pass

        chunk_max = max(chunk_sent_ids, default=0)

        # Second pass: rewrite sent_id values and inject page_break markers.
        for line in lines:
            if line.startswith("# sent_id"):
                try:
                    val = int(line.split("=", 1)[1].strip())
                    # Inject a page_break marker when this sentence had
                    # sent_id = 1 in the source chunk AND it is not the very
                    # first sentence of the whole document (global_sent_offset
                    # is 0 only for the first chunk).
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


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Send chunked text to UDPipe API and write merged CoNLL-U."
    )
    parser.add_argument(
        "--chunk-dir", required=True,
        help="Directory containing chunk_0.txt, chunk_1.txt, … produced by chunk.py.",
    )
    parser.add_argument("--model", required=True, help="UDPipe model identifier.")
    parser.add_argument("--output", required=True, help="Output CoNLL-U file path.")
    parser.add_argument(
        "--url", default=os.environ.get("UDPIPE_URL", UDPIPE_URL),
        help="UDPipe API endpoint URL.",
    )
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
            print(
                f"[Error] UDPipe failed permanently on {cf}. Aborting.", file=sys.stderr
            )
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