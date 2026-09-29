"""Repo-local declarations for tests/test_env_contract.py (atrium-project#60).

Never vendored, never in para-drift, never in docs/templates/ruff.toml's [format]
exclude — unlike test_env_contract.py itself, this file's SHAPE is per-repo by
design. See the canonical test's module docstring for the full rationale.
"""

from __future__ import annotations

# Read by shipped code but deliberately absent from .env.example, each with a
# reason. This repo's NOT_PUBLISHED is the largest in the fleet: it is the only one
# whose batch pipeline is driven by a subprocess stage (api_N_*.sh / config_api.txt)
# with its own ~15-variable surface, distinct from the api image's HTTP entrypoint.
# The api entrypoint (service/api.py -> .enrichment/.jobs/.rescale) never imports
# any of these modules.
NOT_PUBLISHED: dict[str, str] = {
    "ALTO_DIR": "batch-pipeline knob read by api_util/summarize_nt_udp.py; belongs to config_api.txt, not a deployment",
    "ALTO_DPI": "batch-pipeline knob read by api_util/summarize_nt_udp.py; belongs to config_api.txt, not a deployment",
    "BBOX_ORIGIN": "batch-pipeline knob read by api_util/summarize_nt_udp.py; belongs to config_api.txt, not a deployment",
    "CONLLU_INPUT_DIR": "batch-pipeline knob read by api_util/summarize_nt_udp.py; belongs to config_api.txt, not a deployment",
    "IMAGE_DPI": "batch-pipeline knob read by api_util/summarize_nt_udp.py; belongs to config_api.txt, not a deployment",
    "INPUT_PAGES_DIR": "batch-pipeline knob read by api_util/summarize_nt_udp.py; belongs to config_api.txt, not a deployment",
    "MODEL_NAMETAG": "batch-pipeline knob read by api_util/summarize_nt_udp.py; belongs to config_api.txt, not a deployment",
    "MODEL_UDPIPE": "batch-pipeline knob read by api_util/summarize_nt_udp.py; belongs to config_api.txt, not a deployment",
    "SAVE_CONLLU_NE": "batch-pipeline knob read by api_util/summarize_nt_udp.py; belongs to config_api.txt, not a deployment",
    "SAVE_CSV": "batch-pipeline knob read by api_util/summarize_nt_udp.py; belongs to config_api.txt, not a deployment",
    "SAVE_TEITOK": "batch-pipeline knob read by api_util/summarize_nt_udp.py; belongs to config_api.txt, not a deployment",
    "SUMMARY_CSV": "batch-pipeline knob read by api_util/summarize_nt_udp.py; belongs to config_api.txt, not a deployment",
    "SUMMARY_OUTPUT_DIR": "batch-pipeline knob read by api_util/summarize_nt_udp.py; belongs to config_api.txt, not a deployment",
    "TEITOK_OUTPUT_DIR": "batch-pipeline knob read by api_util/summarize_nt_udp.py; belongs to config_api.txt, not a deployment",
    "TSV_INPUT_DIR": "batch-pipeline knob read by api_util/summarize_nt_udp.py; belongs to config_api.txt, not a deployment",
    "TEMP_TXT_DIR": "batch-pipeline knob read by api_util/build_manifest_row.py (the batch CLI's manifest builder) and api_util/summarize_nt_udp.py (where rows files are looked for); not reachable from service/api.py",
    "INPUT_ALTO_DIR": "batch-pipeline knob read by api_util/summarize_nt_udp.py; belongs to config_api.txt, not a deployment",
    "FLEXICONV_ANNOTATE": "batch-pipeline knob read by api_util/summarize_nt_udp.py; belongs to config_api.txt, not a deployment",
    "TEITOK_FLEXICONV_DIR": "batch-pipeline knob read by api_util/summarize_nt_udp.py; belongs to config_api.txt, not a deployment",
    "HF_TOKEN": "read only by llm_run.py, the batch keyword-extraction CLI entrypoint; service/api.py never imports it",
    "PARADATA_DIR": "read only by keywords.py, the batch CLI; not reachable from the service entrypoint",
    "PROMPT_TEMPLATE": "read only by prompt_template.py via llm_run.py, the batch CLI; not reachable from the service entrypoint",
    "PROMPT_GEO_GUARDRAIL": "read only by prompt_template.py via llm_run.py, the batch CLI; not reachable from the service entrypoint",
    "PROMPT_VOCAB_GROUPING": "read only by prompt_template.py via llm_run.py, the batch CLI; not reachable from the service entrypoint",
}

# In .env.example but read by no Python in this repo — each with a reason.
CONSUMED_ELSEWHERE: dict[str, str] = {
    "ATRIUM_VERSION": "read only by docker-compose.yaml to pick the image tag; no Python here reads it",
    "ATRIUM_UID": "read only by docker-compose.yaml (`user: ${ATRIUM_UID:-10001}:0`); no Python here reads it",
    "HF_HOME": "read by huggingface_hub itself, set by the Dockerfile and docker-compose.yaml",
}

# service/README.md or .env.example cells whose value is prose rather than a literal
# the code-default resolver can compare against.
PROSE_DEFAULTS: dict[str, str] = {
    "API_JOBS_ROOT": "computed from the repo root at runtime; the .env.example comment gives it in prose, not as a literal",
    "API_KEEP_WORKSPACES": "README describes the default in prose ('unset') rather than repeating the blank literal",
}
