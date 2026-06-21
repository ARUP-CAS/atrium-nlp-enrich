#!/usr/bin/env bash
# api_2_udp.sh – UDPipe processing + paradata
set -euo pipefail
# shellcheck disable=SC1090  # config path is dynamic (ATRIUM_CONFIG); not followed at lint time
source "${ATRIUM_CONFIG:-config_api.txt}"

PARA_STATE=$(python3 atrium_paradata.py start \
    --program nlp-enrich \
    --paradata-dir "${OUTPUT_DIR}/paradata" \
    --output-types conllu \
    --config \
        "script=api_2_udp" \
        "model_udpipe=${MODEL_UDPIPE}" \
        "word_chunk_limit=${WORD_CHUNK_LIMIT}" \
        "timeout=${TIMEOUT}" \
        "max_retries=${MAX_RETRIES}" \
        "manifest=${OUTPUT_DIR}/manifest.tsv" \
        "output_dir=${OUTPUT_DIR}/UDP")

TOTAL=$(tail -n +2 "${OUTPUT_DIR}/manifest.tsv" | wc -l)
mkdir -p "${OUTPUT_DIR}/UDP"

while IFS=$'\t' read -r file page path; do
    [ "$file" = "file" ] && continue
    out="${OUTPUT_DIR}/UDP/${file}.conllu"
    [ -f "$out" ] && continue

    mkdir -p "$(dirname "$out")"
    # shellcheck disable=SC2153  # CHUNK_DIR is provided by the sourced config file
    chunk_dir="${CHUNK_DIR}/${file}"
    mkdir -p "$chunk_dir"

    if python3 api_util/chunk.py "$path" "$chunk_dir" "$WORD_CHUNK_LIMIT" && \
       python3 api_util/call_udpipe.py \
           --chunk-dir "$chunk_dir" \
           --model     "$MODEL_UDPIPE" \
           --output    "$out" \
           --timeout   "$TIMEOUT" \
           --retries   "$MAX_RETRIES"; then
        python3 atrium_paradata.py success --state "$PARA_STATE" --type conllu
    else
        # P1 FIX: Log the failure and exit immediately to halt the pipeline
        python3 atrium_paradata.py skip \
            --state "$PARA_STATE" \
            --file  "${file}:${page}" \
            --reason "UDPipe API call failed after ${MAX_RETRIES} retries"

        echo "[CRITICAL ERROR] UDPipe processing failed for ${file}. Halting pipeline." >&2
        exit 1
    fi
done < "${OUTPUT_DIR}/manifest.tsv"

python3 atrium_paradata.py finish --state "$PARA_STATE" --input-total "$TOTAL"
