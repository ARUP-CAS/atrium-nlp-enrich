"""tool_limits.py — every limit atrium-nlp-enrich has (atrium-project#53, factor III).

One declaration, read by the service (``service/api.py``, ``service/enrichment.py``) and by
the pipeline stages it runs (``keywords.py``, ``api_util/summarize_nt_udp.py``), and
reported by ``GET /info`` (``limits`` and ``limits_meta``). Each limit is an environment
setting; a malformed value stops the process at startup, naming the variable
(``atrium_limits.LimitConfigError``). ``.env.example`` and ``service/README.md``'s
``## Limits`` table list the same set; ``tests/test_limits_contract.py`` checks that they
agree.

**Three limits are also keys of ``config_api.txt``** — ``WORD_CHUNK_LIMIT``, ``TIMEOUT`` and
``MAX_RETRIES``, which the stage scripts read after ``source``-ing the config. For an API job
the environment wins, then the template ``config_api.txt``, then the default below:
``service/enrichment.py``'s ``_derive_config`` writes the effective value into the job's
own config. The batch CLI reads its config file as before.

What happens over each limit — refused (with the HTTP status), or processed in full with a
``limits_applied`` note (with the effect) — is said beside it.

Standard library only (``atrium_limits`` is the hub's canonical module at the repo root):
the stage scripts and the CLI import this too.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, Optional

from atrium_limits import LimitSet, limit, upload_limit

_REPO_ROOT = Path(__file__).resolve().parent
_CONFIG_TEMPLATE = _REPO_ROOT / "config_api.txt"

# ── the request ─────────────────────────────────────────────────────────────────────────
#: §4.5 upload limit, per uploaded part (the file, `document_json`, `alto`) and for the
#: whole `/enrich_text` body. Over it → 413 ``limit_exceeded``.
MAX_UPLOAD = upload_limit(5)
#: Words one document may have (`/enrich`, `/enrich_text`, `/jobs`). Over it → 413.
MAX_WORDS = limit("MAX_WORDS", 30000, unit="words", key="max_words", minimum=1)
#: Pipelines running at once. Each is a subprocess tree that calls the shared LINDAT
#: services. A synchronous request with every slot taken → 429 ``busy`` with Retry-After.
MAX_CONCURRENT_JOBS = limit(
    "MAX_CONCURRENT_JOBS", 2, unit="jobs", key="max_concurrent_jobs", minimum=1
)
#: `/jobs` submissions that may wait for a slot. One more → 429 ``busy``. 0 accepts a job
#: only when a slot is free.
MAX_QUEUED_JOBS = limit("MAX_QUEUED_JOBS", 8, unit="jobs")
#: Seconds one pipeline run may take. Over it the run and every process it started are
#: stopped and its workspace removed → 504 ``limit_exceeded`` (a `/jobs` job: failed).
API_JOB_TIMEOUT = limit(
    "API_JOB_TIMEOUT", 600, unit="s", key="api_job_timeout", minimum=1, status=504
)
#: Seconds a finished `/jobs` job (and its result) is kept; after that → 404.
JOB_TTL_S = limit("JOB_TTL_S", 3600, unit="s", minimum=1)
#: Largest page-image width or height `/rescale` produces, given as `width`/`height` or
#: reached through `scale`. Over it → 422 ``limit_exceeded``.
MAX_RESCALE_DIM = limit(
    "MAX_RESCALE_DIM", 100000, unit="px", key="max_rescale_dim", minimum=1, status=422
)

# ── the stages (config_api.txt keys) ────────────────────────────────────────────────────
#: Words per UDPipe request; a longer text is sent in pieces cut at a line end and
#: annotated in full (config_api.txt ``WORD_CHUNK_LIMIT``).
WORD_CHUNK_LIMIT = limit("WORD_CHUNK_LIMIT", 900, unit="words", minimum=1)
#: Per-request timeout of one UDPipe or NameTag call (config_api.txt ``TIMEOUT``); a
#: timeout is retried.
LINDAT_TIMEOUT_S = limit("LINDAT_TIMEOUT_S", 60, unit="s", minimum=1)
#: Attempts of one UDPipe or NameTag call (config_api.txt ``MAX_RETRIES``). Once they run
#: out the stage fails → 502.
LINDAT_MAX_RETRIES = limit("LINDAT_MAX_RETRIES", 5, unit="retries", minimum=1)

# ── keywords and the entity summary ─────────────────────────────────────────────────────
#: Words per KeyBERT chunk: a longer document is embedded in overlapping chunks and its
#: keywords merged → ``split`` note.
KEYBERT_CHUNK_WORDS = limit("KEYBERT_CHUNK_WORDS", 400, unit="words", minimum=1)
#: Words two consecutive KeyBERT chunks share.
KEYBERT_CHUNK_OVERLAP = limit("KEYBERT_CHUNK_OVERLAP", 50, unit="words")
#: Entities per page in the entity summary (``ne_summary``, ``summary_ne_counts.csv``);
#: a page with more distinct entities keeps its N most frequent → ``trimmed`` note. The
#: TEITOK and the document record carry every entity.
NE_SUMMARY_TOP_N = limit("NE_SUMMARY_TOP_N", 20, unit="entities", minimum=1)

#: config_api.txt keys, by the environment variable that overrides each.
CONFIG_KEYS: Dict[str, str] = {
    WORD_CHUNK_LIMIT.env: "WORD_CHUNK_LIMIT",
    LINDAT_TIMEOUT_S.env: "TIMEOUT",
    LINDAT_MAX_RETRIES.env: "MAX_RETRIES",
}

_ASSIGN = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
_SHELL_DEFAULT = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*:-(.*)\}$")


def config_values() -> Dict[str, str]:
    """``{environment variable: raw value}`` for the keys the template config_api.txt sets."""
    wanted = {key: env for env, key in CONFIG_KEYS.items()}
    out: Dict[str, str] = {}
    try:
        lines = _CONFIG_TEMPLATE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        m = _ASSIGN.match(line)
        if not m or m.group(1) not in wanted:
            continue
        raw = m.group(2).split("#", 1)[0].strip().strip('"').strip("'")
        shell = _SHELL_DEFAULT.match(raw)
        if shell:
            raw = shell.group(1)
        if raw:
            out[wanted[m.group(1)]] = raw
    return out


def keybert_max_seq_tokens() -> Optional[int]:
    """Tokens of one chunk the KeyBERT encoder reads (its ``max_seq_length``); a longer
    chunk is embedded from its start → ``trimmed`` note. ``None`` until the model is loaded
    in this process (the service loads it at startup when ``DEFAULT_KW_METHOD=keybert``)."""
    keywords = sys.modules.get("keywords")
    if keywords is None:
        return None
    return keywords.keybert_window(getattr(keywords, "_keybert_model_instance", None))[1]


LIMITS = LimitSet(
    MAX_UPLOAD,
    MAX_WORDS,
    MAX_CONCURRENT_JOBS,
    MAX_QUEUED_JOBS,
    API_JOB_TIMEOUT,
    JOB_TTL_S,
    MAX_RESCALE_DIM,
    WORD_CHUNK_LIMIT,
    LINDAT_TIMEOUT_S,
    LINDAT_MAX_RETRIES,
    KEYBERT_CHUNK_WORDS,
    KEYBERT_CHUNK_OVERLAP,
    NE_SUMMARY_TOP_N,
    config=config_values,
)
LIMITS.derived(
    "keybert_max_seq_tokens",
    keybert_max_seq_tokens,
    unit="tokens",
    derived_from=["KEYBERT_MODEL (kw_config.txt)"],
)
