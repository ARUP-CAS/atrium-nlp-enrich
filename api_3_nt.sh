#!/usr/bin/env bash
# api_3_nt.sh – NameTag processing + paradata
set -euo pipefail
# shellcheck disable=SC1090  # config path is dynamic (ATRIUM_CONFIG); not followed at lint time
source "${ATRIUM_CONFIG:-config_api.txt}"

PARA_STATE=$(python3 atrium_paradata.py start \
    --program nlp-enrich \
    --paradata-dir "${OUTPUT_DIR}/paradata" \
    --output-types tsv \
    --config \
        "script=api_3_nt" \
        "model_nametag=${MODEL_NAMETAG}" \
        "timeout=${TIMEOUT}" \
        "max_retries=${MAX_RETRIES}" \
        "conllu_input_dir=${OUTPUT_DIR}/UDP" \
        "output_dir=${OUTPUT_DIR}/NE")

TOTAL=$(find "${OUTPUT_DIR}/UDP" -name '*.conllu' -type f | wc -l)
mkdir -p "${OUTPUT_DIR}/NE"

while IFS= read -r -d '' conllu; do
    rel_path="${conllu#"${OUTPUT_DIR}"/UDP/}"
    doc="${rel_path%.conllu}"
    out_dir="${OUTPUT_DIR}/NE/${doc}"

    if [ -d "$out_dir" ]; then
        python3 atrium_paradata.py skip \
            --state "$PARA_STATE" \
            --file  "$doc" \
            --reason "output already exists"
        continue
    fi

    mkdir -p "$out_dir"

    if python3 api_util/call_nametag.py \
            --input      "$conllu" \
            --model      "$MODEL_NAMETAG" \
            --output-dir "$out_dir" \
            --timeout    "$TIMEOUT" \
            --retries    "$MAX_RETRIES"; then
        n_pages=$(find "$out_dir" -maxdepth 1 -name '*.tsv' 2>/dev/null | wc -l)
        python3 atrium_paradata.py success \
            --state "$PARA_STATE" --type tsv --count "$n_pages"
    else
        # P1 FIX: Log the failure and exit immediately to halt the pipeline
        python3 atrium_paradata.py skip \
            --state "$PARA_STATE" \
            --file  "$doc" \
            --reason "NameTag API call failed"

        echo "[CRITICAL ERROR] NameTag processing failed for ${doc}. Halting pipeline." >&2
        exit 1
    fi
done < <(find "${OUTPUT_DIR}/UDP" -name '*.conllu' -type f -print0)

python3 atrium_paradata.py finish --state "$PARA_STATE" --input-total "$TOTAL"
