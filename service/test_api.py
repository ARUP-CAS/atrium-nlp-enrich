#!/usr/bin/env python3
"""
service/test_api.py — Live smoke-test client for a running nlp-enrich API.

Analogue of the page-classification two-terminal quick test:

    # terminal 1
    uvicorn service.api:app --port 8000
    # terminal 2
    python service/test_api.py -f data_samples/DOC_LINE_CATEG/CTX000000001.csv

Hits /info and /enrich, prints a short summary of the JSON envelope.
"""

import argparse
import json
import sys

import requests


def main() -> int:
    ap = argparse.ArgumentParser(description="Smoke-test the nlp-enrich API.")
    ap.add_argument("-u", "--url", default="http://localhost:8000")
    ap.add_argument("-f", "--file", required=True, help="CSV/XLSX/TXT to enrich.")
    ap.add_argument("-m", "--kw-method", default="keybert",
                    choices=["keybert", "yake", "legacy", "none"])
    ap.add_argument("-n", "--num-keywords", type=int, default=20)
    ap.add_argument("--format", default="json", choices=["json", "zip"])
    args = ap.parse_args()

    print(f"GET {args.url}/info")
    info = requests.get(f"{args.url}/info", timeout=30)
    info.raise_for_status()
    print(json.dumps(info.json(), indent=2, ensure_ascii=False)[:800])

    print(f"\nPOST {args.url}/enrich  ({args.file})")
    with open(args.file, "rb") as fh:
        resp = requests.post(
            f"{args.url}/enrich",
            files={"file": (args.file.split("/")[-1], fh)},
            data={"kw_method": args.kw_method,
                  "num_keywords": str(args.num_keywords),
                  "format": args.format},
            timeout=900,
        )

    if resp.status_code != 200:
        print(f"[FAIL] HTTP {resp.status_code}: {resp.text[:1000]}")
        return 1

    if args.format == "zip":
        out = "enriched_output.zip"
        with open(out, "wb") as fh:
            fh.write(resp.content)
        print(f"[OK] zip written → {out} ({len(resp.content)} bytes)")
        return 0

    data = resp.json()
    print("[OK] enrichment complete")
    print(f"  doc_id:         {data.get('doc_id')}")
    print(f"  pages:          {data.get('pages')}")
    print(f"  method req/used:{data.get('method_requested')} / {data.get('method_used')}")
    kws = data.get("keywords") or []
    print(f"  keywords:       {len(kws)}")
    for kw in kws[:5]:
        print(f"     - {kw.get('keyword')}  ({kw.get('score')})")

    tt = data.get("teitok_xml") or ""
    print(f"  teitok bytes:   {len(tt)}")
    ne_rows = data.get("ne_summary") or []
    print(f"  ne_summary rows:{len(ne_rows)}")
    for row in ne_rows:
        print(f"     - Page {row.get('page')}:")
        ents = row.get("entities") or []
        for ent in ents[:5]:  # Print the top 5 entities for this page
            print(f"         * {ent.get('text')} ({ent.get('type')} - count: {ent.get('count')})")
        if len(ents) > 5:
            print(f"         * ... and {len(ents) - 5} more entities")
    # ------------------------

    print(f"  llm field:      {data.get('llm')!r} (must be null)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
