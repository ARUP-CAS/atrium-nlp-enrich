# syntax=docker/dockerfile:1.7
FROM python:3.11-slim AS base

ARG ATRIUM_RUNNER_IMAGE=""
ARG ATRIUM_RUNNER_REPO="https://github.com/ufal/atrium-nlp-enrich"
ARG ATRIUM_RUNNER_REF=""

ENV ATRIUM_RUNNER_IMAGE=${ATRIUM_RUNNER_IMAGE} \
    ATRIUM_RUNNER_REPO=${ATRIUM_RUNNER_REPO} \
    ATRIUM_RUNNER_REF=${ATRIUM_RUNNER_REF} \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/cache/huggingface

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        bash \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt requirements-test.txt ./
RUN pip install -r requirements.txt -r requirements-test.txt

COPY . .

RUN chmod +x api_1_manifest.sh api_2_udp.sh api_3_nt.sh api_4_stats.sh \
    && useradd --create-home --uid 10001 atrium \
    && mkdir -p /cache/huggingface /data \
    && chown -R atrium:atrium /app /cache /data

USER atrium

ENTRYPOINT ["python", "run_pipeline.py"]
CMD []


# ---------------------------------------------------------------------------
# API surface — published as :<version>-api
# ---------------------------------------------------------------------------
FROM base AS api

USER root
COPY service/requirements.txt ./service_requirements.txt
RUN pip install -r service_requirements.txt
RUN chown -R atrium:atrium /app
USER atrium

EXPOSE 8000
ENTRYPOINT ["uvicorn", "service.api:app", "--host", "0.0.0.0", "--port", "8000"]


# ---------------------------------------------------------------------------
# Optional LLM/GPU variant — published as :<version>-llm
# ---------------------------------------------------------------------------
FROM base AS llm

USER root
COPY requirements_llm.txt ./
RUN pip install \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        -r requirements_llm.txt

RUN chown -R atrium:atrium /app
USER atrium

ENTRYPOINT ["python", "llm_run.py"]
CMD ["llm_config.txt"]
