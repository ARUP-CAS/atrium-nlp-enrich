#!/usr/bin/env bash
# api_2_udp.sh – UDPipe processing + paradata
set -euo pipefail
source config_api.txt

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

TOTAL=$(wc -l < "${OUTPUT_DIR}/manifest.tsv")
TOTAL=$((TOTAL - 1))  # subtract header

while IFS=$'\t' read -r file page path; do
    [ "$file" = "file" ] && continue   # skip header
    out="${OUTPUT_DIR}/UDP/${file}.conllu"
    [ -f "$out" ] && continue          # resume-capable: already done

    if python3 api_util/chunk.py "$path" | \
       python3 api_util/call_udpipe.py --model "$MODEL_UDPIPE" \
                --timeout "$TIMEOUT" --retries "$MAX_RETRIES" \
                --output "$out"; then
        python3 atrium_paradata.py success --state "$PARA_STATE" --type conllu
    else
        python3 atrium_paradata.py skip \
            --state "$PARA_STATE" \
            --file  "${file}:${page}" \
            --reason "UDPipe API call failed after ${MAX_RETRIES} retries"
    fi
done < "${OUTPUT_DIR}/manifest.tsv"

python3 atrium_paradata.py finish --state "$PARA_STATE" --input-total "$TOTAL"