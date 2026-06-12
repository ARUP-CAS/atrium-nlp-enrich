# nlp-enrich API service

Single-file entry point for the ATRIUM NLP enrichment pipeline (issue
[#8](https://github.com/ufal/atrium-nlp-enrich/issues/8)): upload ordered text
lines, get back enriched **TEITOK XML + keywords + paradata**. The four core
stages (`manifest → udp → nt → stats`) always run; **LLM enrichment is excluded
from every API entry point**.

## Quick start

```bash
./setup_api_service.sh                 # venv + deps + KeyBERT prefetch + serve
# or, manually:
pip install -r requirements.txt -r service/requirements.txt
uvicorn service.api:app --host 0.0.0.0 --port 8000
```

Two-terminal smoke test:

```bash
# terminal 2
python service/test_api.py -f data_samples/DOC_LINE_CATEG/CTX000000001.csv
```

## Endpoints

| Method | Path           | Purpose                                                      |
|--------|----------------|--------------------------------------------------------------|
| GET    | `/`            | minimal landing page (see `/docs` for OpenAPI UI)            |
| GET    | `/info`        | stage plan, pinned models, keyword methods + default, limits |
| GET    | `/health`      | config validity via `run_pipeline.py --dry-run`              |
| POST   | `/enrich`      | **single-file entry point** — upload CSV/XLSX/TXT            |
| POST   | `/enrich_text` | same pipeline for inline JSON                                |

### `POST /enrich` (multipart form)

| Field          | Default    | Notes                                                   |
|----------------|------------|---------------------------------------------------------|
| `file`         | *required* | `.csv` (needs a `text` column), `.xlsx`, or `.txt`      |
| `kw_method`    | `keybert`  | `keybert` \| `yake` \| `legacy` \| `none`               |
| `num_keywords` | `20`       | 1–100                                                   |
| `lang`         | `cs`       | Czech-pinned in v1                                      |
| `format`       | `json`     | `json` envelope, or `zip` of the workspace `OUTPUT_DIR` |

`keybert` is the best/default backend. If its preflight fails at runtime the
service **degrades once to `yake`** and reports `method_requested` vs
`method_used` rather than failing the enrichment.

### `POST /enrich_text` (JSON)

```json
{ "doc_id": "CTX1", "lines": ["Výzkum odhalil základy kostela.", "..."],
  "kw_method": "keybert", "num_keywords": 20, "format": "json" }
```

### JSON envelope

```json
{
  "doc_id": "...", "pages": 2,
  "stages": [ {"script": "api_4_stats", "successfully_processed": 1, ...} ],
  "teitok_xml": "<?xml ...>",
  "keywords": [ {"keyword": "...", "score": 0.91} ],
  "ne_summary": [ {"file": "...", "page": "1", "entities": [...] } ],
  "paradata": { ...merged pipeline-run record incl. license union... },
  "method_requested": "keybert", "method_used": "keybert",
  "llm": null
}
```

`format=zip` instead streams the full workspace `OUTPUT_DIR`
(`TEITOK/`, `UDP_NE/`, `KW_PER_DOC_*/`, summary CSVs, `paradata/`).

## How it works

`PipelineManager` treats `run_pipeline.py` as the **only** execution interface —
it never calls the stage scripts directly. Each request gets a fresh workspace
under `TEMP/api_jobs/<job_id>/` with every pipeline directory relocated inside
it, so resume / paradata-collision / `FAIL_ON_EMPTY` semantics behave as a clean
first run and concurrent requests can't collide. Every input form is normalized
to a canonical `text[,page_num,line_num]` CSV so stage 1 always runs on the same
path and no input type bypasses a mandatory step.

The runner's exit codes map to HTTP: `0`→200; `3` (keyword preflight)→retry with
`yake`, else 503; `1` (empty run)/`2` (missing stage)/other→502; oversize→413;
queue full→429.

UDPipe/NameTag models stay operator-pinned in `config_api.txt` and are surfaced
read-only via `/info`. ALTO/page-image inputs are unusable for text-only API
input, so TEITOK is produced **without** bounding boxes (a warning, not an
error).

## Configuration (environment)

| Variable                       | Default   | Meaning                                        |
|--------------------------------|-----------|------------------------------------------------|
| `MAX_CONCURRENT_JOBS`          | `2`       | concurrent pipeline runs (also shields LINDAT) |
| `MAX_UPLOAD_MB`                | `5`       | upload size guard                              |
| `MAX_WORDS`                    | `30000`   | sync request word cap                          |
| `DEFAULT_KW_METHOD`            | `keybert` | default keyword backend                        |
| `ALLOWED_ORIGINS`              | `*`       | CORS origins                                   |
| `API_KEEP_WORKSPACES`          | unset     | keep per-request workspaces for debugging      |
| `ATRIUM_RUNNER_IMAGE/REPO/REF` | —         | forwarded to the runner for provenance         |

## Tests

`tests/test_api_service.py` is fully hermetic (no LINDAT, no models): it
monkeypatches the pipeline subprocess to drop fixture outputs into the
workspace, then exercises the full HTTP contract via FastAPI `TestClient`,
plus input normalization, `doc_id` sanitization, and exit-code→HTTP mapping.

```bash
pytest -m "not slow" tests/test_api_service.py
```
