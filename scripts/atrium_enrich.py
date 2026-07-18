#!/usr/bin/env python3
"""Zero-dependency client for the ATRIUM NLP Enrichment API.

Uploads ordered text lines (CSV/XLSX/TXT file, or plain lines on stdin) to a
running instance of the FastAPI service in `service/api.py` and returns the
enriched result: keywords, named-entity summary, and TEITOK XML
(local server by default, remote via --base-url or the ATRIUM_NE_URL env
variable).

Only the Python 3 standard library is used - no pip installs required.

Usage:
    python3 scripts/atrium_enrich.py lines.csv
    python3 scripts/atrium_enrich.py notes.txt --kw-method yake --num-keywords 10
    python3 scripts/atrium_enrich.py lines.csv --jobs           # async jobs API
    python3 scripts/atrium_enrich.py lines.csv --zip out.zip    # full workspace
    python3 scripts/atrium_enrich.py - --doc-id CTX1 < lines.txt
    python3 scripts/atrium_enrich.py --info

Exit codes:
    0 - success
    1 - client-side error (bad arguments, unreadable file)
    2 - server unreachable (connection refused / timeout)
    3 - server-side error (HTTP 4xx/5xx)
"""

import argparse
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

DEFAULT_BASE_URL = os.environ.get("ATRIUM_NE_URL", "http://localhost:8000")
INPUT_SUFFIXES = {".csv", ".txt", ".xlsx"}
MAX_UPLOAD_MB = 5  # mirrors the server's MAX_UPLOAD_MB default
KW_METHODS = ("keybert", "yake", "legacy", "none")
RETRY_STATUS = {502, 503, 504}
RETRY_ATTEMPTS = 3
RETRY_WAIT_S = 10
JOB_POLL_INTERVAL_S = 5
JOB_POLL_TIMEOUT_S = 900


def build_multipart(fields: dict, file_field: str, file_path: Path) -> tuple[bytes, str]:
    """Encode form fields and one file as multipart/form-data using only the stdlib."""
    boundary = uuid.uuid4().hex
    lines = []
    for name, value in fields.items():
        lines.append(f"--{boundary}".encode())
        lines.append(f'Content-Disposition: form-data; name="{name}"'.encode())
        lines.append(b"")
        lines.append(str(value).encode())

    mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    lines.append(f"--{boundary}".encode())
    lines.append(f'Content-Disposition: form-data; name="{file_field}"; filename="{file_path.name}"'.encode())
    lines.append(f"Content-Type: {mime}".encode())
    lines.append(b"")
    lines.append(file_path.read_bytes())
    lines.append(f"--{boundary}--".encode())
    lines.append(b"")

    body = b"\r\n".join(lines)
    content_type = f"multipart/form-data; boundary={boundary}"
    return body, content_type


def http_raw(url: str, data: bytes = None, content_type: str = None, timeout: int = 900) -> bytes:
    """POST (or GET when data is None) and return raw bytes, with retry on 502/503/504."""
    last_error = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        request = urllib.request.Request(url, data=data, method="POST" if data else "GET")
        if content_type:
            request.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            if e.code in RETRY_STATUS and attempt < RETRY_ATTEMPTS:
                print(
                    f"[retry {attempt}/{RETRY_ATTEMPTS}] HTTP {e.code}, waiting {RETRY_WAIT_S}s...",
                    file=sys.stderr,
                )
                time.sleep(RETRY_WAIT_S)
                last_error = f"HTTP {e.code}: {detail}"
                continue
            if e.code == 429:
                print(
                    "Server busy (HTTP 429): the concurrent-job limit is reached. "
                    "Retry later or submit through the async jobs API with --jobs.",
                    file=sys.stderr,
                )
            else:
                print(f"Server error - HTTP {e.code}: {detail}", file=sys.stderr)
            sys.exit(3)
        except (urllib.error.URLError, TimeoutError) as e:
            print(
                f"Cannot reach the API at {url} ({e}).\nIs the server running? Start it with: bash scripts/server.sh",
                file=sys.stderr,
            )
            sys.exit(2)
    print(f"Server error after {RETRY_ATTEMPTS} attempts - {last_error}", file=sys.stderr)
    sys.exit(3)


def http_json(url: str, data: bytes = None, content_type: str = None, timeout: int = 900) -> dict:
    return json.loads(http_raw(url, data=data, content_type=content_type, timeout=timeout).decode("utf-8"))


def check_input(path: Path) -> None:
    suffix = path.suffix.lower()
    if suffix not in INPUT_SUFFIXES:
        print(f"Unsupported file type '{suffix}' for {path}. Allowed: .csv, .xlsx, .txt", file=sys.stderr)
        sys.exit(1)
    size = path.stat().st_size
    if size > MAX_UPLOAD_MB * 1024 * 1024:
        print(
            f"{path}: {size} bytes exceeds the {MAX_UPLOAD_MB} MB server upload limit - "
            "split the file into smaller documents first",
            file=sys.stderr,
        )
        sys.exit(1)


def enrich_file(base_url: str, path: Path, kw_method: str, num_keywords: int, lang: str, fmt: str) -> dict:
    """Synchronous enrichment of one uploaded file via POST /enrich."""
    fields = {"kw_method": kw_method, "num_keywords": num_keywords, "lang": lang, "format": fmt}
    body, content_type = build_multipart(fields, file_field="file", file_path=path)
    if fmt == "zip":
        return {"_zip_bytes": http_raw(f"{base_url}/enrich", data=body, content_type=content_type)}
    return http_json(f"{base_url}/enrich", data=body, content_type=content_type)


def enrich_stdin(base_url: str, doc_id: str, kw_method: str, num_keywords: int, lang: str) -> dict:
    """Read plain text lines from stdin and enrich them via POST /enrich_text."""
    lines = [line.strip() for line in sys.stdin.read().splitlines() if line.strip()]
    if not lines:
        print("No text lines on stdin.", file=sys.stderr)
        sys.exit(1)
    payload = json.dumps(
        {"doc_id": doc_id, "lines": lines, "kw_method": kw_method, "num_keywords": num_keywords, "lang": lang}
    ).encode("utf-8")
    return http_json(f"{base_url}/enrich_text", data=payload, content_type="application/json")


def enrich_via_jobs(base_url: str, path: Path, kw_method: str, num_keywords: int, lang: str) -> dict:
    """Asynchronous enrichment: submit to POST /jobs, poll, fetch the result."""
    fields = {"kw_method": kw_method, "num_keywords": num_keywords, "lang": lang}
    body, content_type = build_multipart(fields, file_field="file", file_path=path)
    submitted = http_json(f"{base_url}/jobs", data=body, content_type=content_type)
    job_id = submitted.get("job_id")
    if not job_id:
        print(f"Job submission failed: {submitted}", file=sys.stderr)
        sys.exit(3)
    print(f"Job {job_id} queued; polling every {JOB_POLL_INTERVAL_S}s...", file=sys.stderr)

    deadline = time.monotonic() + JOB_POLL_TIMEOUT_S
    while True:
        status = http_json(f"{base_url}/jobs/{job_id}", timeout=60)
        state = status.get("status")
        if state == "done":
            result = http_json(f"{base_url}/jobs/{job_id}/result", timeout=60)
            http_raw_delete(f"{base_url}/jobs/{job_id}")
            return result
        if state == "failed":
            print(f"Job {job_id} failed: {status.get('error')}", file=sys.stderr)
            sys.exit(3)
        if time.monotonic() > deadline:
            print(f"Job {job_id} still '{state}' after {JOB_POLL_TIMEOUT_S}s - giving up.", file=sys.stderr)
            sys.exit(3)
        time.sleep(JOB_POLL_INTERVAL_S)


def http_raw_delete(url: str) -> None:
    """Best-effort DELETE (job cleanup) - failures are not fatal."""
    try:
        request = urllib.request.Request(url, method="DELETE")
        urllib.request.urlopen(request, timeout=30).read()
    except Exception:
        pass


def result_rows(name: str, envelope: dict) -> list[tuple]:
    """Flatten an enrichment envelope into (doc, rank, keyword, score) rows."""
    rows = []
    for rank, kw in enumerate(envelope.get("keywords") or [], start=1):
        rows.append((envelope.get("doc_id", name), rank, kw.get("keyword", ""), kw.get("score")))
    return rows


def print_table(rows: list[tuple], as_csv: bool) -> None:
    header = ("DOC", "RANK", "KEYWORD", "SCORE")
    if as_csv:
        print(",".join(header))
        for row in rows:
            score = "" if row[3] is None else f"{row[3]:.4f}"
            print(f"{row[0]},{row[1]},{row[2]},{score}")
    else:
        print(f"{header[0]:<32} {header[1]:>4} {header[2]:<32} {header[3]:>7}")
        for row in rows:
            score = "      -" if row[3] is None else f"{row[3]:>7.4f}"
            print(f"{row[0]:<32} {row[1]:>4} {row[2]:<32} {score}")


def summarize(envelope: dict) -> None:
    print(
        f"# doc_id={envelope.get('doc_id')} pages={envelope.get('pages')} "
        f"kw_method={envelope.get('method_used')} (requested {envelope.get('method_requested')}) "
        f"entities={sum(len(p.get('entities', [])) for p in envelope.get('ne_summary') or [])}",
        file=sys.stderr,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*", help="CSV/XLSX/TXT line file(s) to enrich, or '-' for stdin lines")
    parser.add_argument(
        "--base-url", default=DEFAULT_BASE_URL, help=f"API base URL (default: {DEFAULT_BASE_URL}, env: ATRIUM_NE_URL)"
    )
    parser.add_argument(
        "--kw-method", choices=KW_METHODS, default="keybert", help="keyword extraction backend (default: keybert)"
    )
    parser.add_argument("--num-keywords", type=int, default=20, help="keywords per document, 1-100 (default: 20)")
    parser.add_argument("--lang", default="cs", help="text language (default: cs; Czech-pinned in v1)")
    parser.add_argument("--doc-id", default="document", help="document id for stdin input (default: document)")
    parser.add_argument(
        "--jobs", action="store_true", help="use the async jobs API (submit, poll, fetch) instead of a sync call"
    )
    parser.add_argument(
        "--zip", metavar="OUT.zip", default=None, help="download the full workspace ZIP to this path (sync only)"
    )
    parser.add_argument(
        "--format", choices=["table", "csv", "json"], default="table", help="output format (default: table)"
    )
    parser.add_argument("--info", action="store_true", help="print service capabilities and limits, then exit")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")

    if args.info:
        print(json.dumps(http_json(f"{base_url}/info", timeout=60), indent=2))
        return

    if not args.files:
        parser.error("no input files given (or use --info)")
    if args.zip and (args.jobs or len(args.files) != 1 or args.files[0] == "-"):
        parser.error("--zip needs exactly one input file and a synchronous call (no --jobs, no stdin)")

    envelopes = {}
    rows = []
    for name in args.files:
        if name == "-":
            envelope = enrich_stdin(base_url, args.doc_id, args.kw_method, args.num_keywords, args.lang)
        else:
            path = Path(name)
            if not path.is_file():
                print(f"File not found: {path}", file=sys.stderr)
                sys.exit(1)
            check_input(path)
            if args.jobs:
                envelope = enrich_via_jobs(base_url, path, args.kw_method, args.num_keywords, args.lang)
            elif args.zip:
                result = enrich_file(base_url, path, args.kw_method, args.num_keywords, args.lang, fmt="zip")
                Path(args.zip).write_bytes(result["_zip_bytes"])
                print(f"Workspace ZIP saved to {args.zip}")
                return
            else:
                envelope = enrich_file(base_url, path, args.kw_method, args.num_keywords, args.lang, fmt="json")
        envelopes[name] = envelope
        summarize(envelope)
        rows.extend(result_rows(name, envelope))

    if args.format == "json":
        print(json.dumps(envelopes if len(envelopes) > 1 else next(iter(envelopes.values())), indent=2))
    else:
        if not rows:
            print("No keywords returned (kw_method=none?). Use --format json for the full envelope.", file=sys.stderr)
            return
        print_table(rows, as_csv=(args.format == "csv"))


if __name__ == "__main__":
    main()
