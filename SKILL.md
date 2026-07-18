---
name: atrium-nlp-enrich
description: Enriches OCR/HTR text lines (CSV/XLSX/TXT or inline JSON) with Czech NLP annotations - UDPipe morphology, NameTag named entities, KeyBERT/YAKE keywords - producing TEITOK XML plus a keywords-and-entities envelope. Use this skill to linguistically enrich digitized archival documents after OCR and quality filtering, or to rescale TEITOK page coordinates to a new image size.
---

# ATRIUM NLP Enrichment Skill 📖

This skill provides agent access to the **ATRIUM NLP Enrichment** service - a
UDPipe + NameTag + keyword-extraction pipeline that turns plain text lines from
digitized documents into enriched **TEITOK XML** with morphology, named
entities, and document keywords. It follows a **server-client** design: a
FastAPI server (in `service/`) runs the heavy pipeline, and a zero-dependency
client script (`scripts/atrium_enrich.py`) is the only thing the agent calls
directly.

## Operational Requirements ⚙️

- **Server**: a running instance is required. Default `http://localhost:8000`;
  override with `--base-url` or the `ATRIUM_NE_URL` environment variable.
- **Client dependencies**: none - `scripts/atrium_enrich.py` uses only the
  Python 3 standard library.
- **Server dependencies**: Docker (recommended, compose `api` profile) or a
  Python venv provisioned by `setup_api_service.sh` (installs repo +
  `service/requirements.txt`, prefetches the KeyBERT embedding model).
- **First launch**: the KeyBERT sentence-transformer
  (`paraphrase-multilingual-MiniLM-L12-v2`, ~500 MB) is downloaded into the HF
  cache; UDPipe/NameTag run against LINDAT web services. Warmup takes minutes,
  not seconds - do **not** treat a slow first start as failure.
- **Limits**: 5 MB per upload, 30 000 words per synchronous request, 2
  concurrent pipeline runs (HTTP 429 when busy - use `--jobs` or retry later).

## Pipeline & keyword methods 📖

Stage plan (always runs in full; LLM enrichment is excluded from the API):

| Stage      | What it does                                                   |
|------------|----------------------------------------------------------------|
| `manifest` | normalize input lines into per-document processing manifests   |
| `udp`      | UDPipe tokenization, lemmatization, morphology (LINDAT)        |
| `nt`       | NameTag named-entity recognition (LINDAT)                      |
| `stats`    | keyword extraction + summary statistics + TEITOK XML assembly  |

Keyword backends (`--kw-method`):

| Method    | Character                                                       |
|-----------|------------------------------------------------------------------|
| `keybert` | best quality; embedding-based; GPU-capable (default)            |
| `yake`    | fast CPU statistical extraction                                 |
| `legacy`  | stdlib KER lemma-frequency baseline                             |
| `none`    | skip keyword extraction (TEITOK + entities only)                |

If the KeyBERT preflight fails at runtime the service degrades once to `yake`
and reports `method_requested` vs `method_used` instead of failing the run.
Input language is Czech-pinned (`lang=cs`) in v1.

## Workflows 🪄

### 1. Ensure the server is running

```bash
bash scripts/server.sh          # Docker Compose api profile (or local fallback)
bash scripts/server.sh --local  # force local uvicorn (no Docker)
```

Idempotent: exits immediately if `GET /info` already answers; waits for
first-run warmup.

### 2. Enrich

```bash
# CSV/XLSX with a `text` column, or plain TXT lines - synchronous
python3 scripts/atrium_enrich.py small_data_samples/CTX000000001.csv

# Different keyword backend, fewer keywords, machine-readable CSV
python3 scripts/atrium_enrich.py notes.txt --kw-method yake --num-keywords 10 --format csv

# Long-running input: async jobs API (submit → poll → result; 429-safe)
python3 scripts/atrium_enrich.py lines.csv --jobs

# Full workspace ZIP (TEITOK/, UDP_NE/, keyword CSVs, paradata/)
python3 scripts/atrium_enrich.py lines.csv --zip enriched.zip

# Inline lines from stdin (no file needed)
printf 'Výzkum odhalil základy kostela.\n' | python3 scripts/atrium_enrich.py - --doc-id CTX1

# Discover capabilities and limits
python3 scripts/atrium_enrich.py --info
```

### 3. Interpret output

- `table` (default): `DOC, RANK, KEYWORD, SCORE` rows plus a one-line summary
  (`doc_id`, `pages`, `kw_method` used, entity count) on stderr.
- `csv`: same rows for downstream tabular processing.
- `json`: the full envelope - `doc_id`, `pages`, `stages`, `teitok_xml`,
  `keywords`, `ne_summary`, `paradata`, `method_requested`/`method_used`.
- `--zip` saves the complete pipeline workspace instead (TEITOK XML files,
  UDPipe/NameTag outputs, keyword CSVs, paradata records).

## Agent Guidelines 🤖

1. **Method selection**: keep the default `keybert` unless the user asks for
   speed (`yake`) or a no-model baseline (`legacy`). Report `method_used` when
   it differs from the requested method (automatic degradation).
2. Prefer `--format json` when the result feeds further processing; the
   TEITOK XML is in the envelope's `teitok_xml` field, and `--zip` materializes
   the whole workspace as files.
3. For full request/response schemas, fetch `GET /openapi.json` from the
   running server (Swagger UI at `/docs`).
4. Exit code `2` (unreachable): start the server (`bash scripts/server.sh`)
   and retry once. Exit code `3` (server error): the client already retried
   502/503/504 three times - check `GET /health?deep=true` (verifies the
   LINDAT UDPipe/NameTag upstreams) and server logs; do not loop.
5. **Busy handling**: HTTP 429 means the concurrent-run limit is reached -
   switch to `--jobs` (async submit/poll) or retry later; do not hammer the
   sync endpoint.
6. **Size limits**: 5 MB per file, 30 000 words per sync request. Split larger
   documents into parts and tell the user you did so.
7. Do not bypass the API by importing the pipeline code directly; server-side
   runs produce the paradata provenance records shipped in every envelope.

## Acknowledgements & Citations 🙏

Developed within the [ATRIUM](https://atrium-research.eu/) project at ÚFAL,
Charles University; UDPipe and NameTag are
[LINDAT/CLARIAH-CZ](https://lindat.cz) services. If you use this service for
research, cite the repository's `CITATION.cff`.
