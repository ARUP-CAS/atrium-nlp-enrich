#!/usr/bin/env python3
"""
call_nametag.py  –  Send a CoNLL-U file to the NameTag 3 API, receive NER
annotations, and write per-page TSV files. Retries automatically on network errors.
"""

import argparse
import os
import sys
from collections import defaultdict

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ImportError:
    print("[Error] 'requests' library is required. Run: pip install requests urllib3", file=sys.stderr)
    sys.exit(1)

NAMETAG_URL = "https://lindat.mff.cuni.cz/services/nametag/api/recognize"


# ── API call ──────────────────────────────────────────────────────────────────

def get_robust_session(retries: int) -> requests.Session:
    """Configures a requests session with exponential backoff for 429/5xx errors."""
    session = requests.Session()
    retry_strategy = Retry(
        total=retries,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["POST"]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def call_nametag(session: requests.Session, conllu_text: str, model: str, url: str, timeout: int) -> dict | None:
    """POST CoNLL-U text to NameTag and return the parsed JSON dict."""
    try:
        resp = session.post(
            url,
            data={
                "model": model,
                "input": "conllu",
                "output": "conll",
                "data": conllu_text,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as exc:
        print(f"  [WARN] NameTag API failed permanently: {exc}", file=sys.stderr)
        return None


# ── sent_id → page mapping ────────────────────────────────────────────────────

def build_sent_page_map(conllu_path: str) -> list[int]:
    """Return a list mapping sentence index (0-based) → page number (1-based)."""
    sent_to_page: list[int] = []
    current_page = 0
    pending_page_break = False

    try:
        with open(conllu_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line_stripped = line.strip()

                if line_stripped == "# page_break = true":
                    pending_page_break = True
                    continue

                if not line_stripped.startswith("# sent_id"):
                    continue

                if "=" in line_stripped:
                    val = line_stripped.split("=", 1)[1].strip()
                    if val == "1" or pending_page_break:
                        current_page += 1
                        pending_page_break = False

                if current_page == 0:
                    current_page = 1

                sent_to_page.append(current_page)

    except Exception as exc:
        print(f"[Error] reading CoNLL-U {conllu_path}: {exc}", file=sys.stderr)

    if not sent_to_page:
        print(
            f"[Warn] No sent_id markers found in {conllu_path}; "
            "treating entire document as a single page.",
            file=sys.stderr,
        )
        sent_to_page = [1]

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
    """Parse NameTag JSON result and write per-page TSV files."""
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

    sent_to_page = build_sent_page_map(args.input)

    with open(args.input, "r", encoding="utf-8") as fh:
        conllu_text = fh.read()

    print(f"  [NameTag] Sending {doc_id} ({len(sent_to_page)} sentences)...")

    session = get_robust_session(args.retries)
    response_json = call_nametag(session, conllu_text, args.model, args.url, args.timeout)

    if response_json is None:
        print(f"[Error] NameTag failed permanently for {doc_id}.", file=sys.stderr)
        sys.exit(1)

    n_pages = write_tsv_files(response_json, sent_to_page, args.output_dir, doc_id)
    print(f"  [NameTag] Written {n_pages} page TSV file(s) → {args.output_dir}")


if __name__ == "__main__":
    main()