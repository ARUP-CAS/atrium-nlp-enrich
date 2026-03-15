#!/usr/bin/env bash
# api_3_nt.sh – NameTag processing + paradata
set -euo pipefail
source config_api.txt

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

CONLLU_FILES=("${OUTPUT_DIR}/UDP/"*.conllu)
TOTAL=${#CONLLU_FILES[@]}

for conllu in "${CONLLU_FILES[@]}"; do
    doc=$(basename "$conllu" .conllu)
    out_dir="${OUTPUT_DIR}/NE/${doc}"
    [ -d "$out_dir" ] && continue

    if python3 api_util/call_nametag.py \
            --input "$conllu" \
            --model "$MODEL_NAMETAG" \
            --output-dir "$out_dir" \
            --timeout "$TIMEOUT" --retries "$MAX_RETRIES"; then
        n_pages=$(ls "$out_dir"/*.tsv 2>/dev/null | wc -l)
        python3 atrium_paradata.py success \
            --state "$PARA_STATE" --type tsv --count "$n_pages"
    else
        python3 atrium_paradata.py skip \
            --state "$PARA_STATE" \
            --file  "$doc" \
            --reason "NameTag API call failed"
    fi
done

python3 atrium_paradata.py finish --state "$PARA_STATE" --input-total "$TOTAL"