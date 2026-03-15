#!/usr/bin/env bash
# api_4_stats.sh – statistics + TEITOK generation + paradata
set -euo pipefail
source config_api.txt

# Determine which output types are active
OUTPUT_TYPES="csv"
[ "${SAVE_CONLLU_NE:-true}"  = "true" ] && OUTPUT_TYPES="$OUTPUT_TYPES conllu"
[ "${SAVE_TEITOK:-true}"     = "true" ] && OUTPUT_TYPES="$OUTPUT_TYPES xml"

PARA_STATE=$(python3 atrium_paradata.py start \
    --program nlp-enrich \
    --paradata-dir "${OUTPUT_DIR}/paradata" \
    --output-types $OUTPUT_TYPES \
    --config \
        "script=api_4_stats" \
        "conllu_input_dir=${OUTPUT_DIR}/UDP" \
        "tsv_input_dir=${OUTPUT_DIR}/NE" \
        "output_dir=${OUTPUT_DIR}/UDP_NE" \
        "save_conllu_ne=${SAVE_CONLLU_NE:-true}" \
        "save_csv=${SAVE_CSV:-true}" \
        "save_teitok=${SAVE_TEITOK:-true}" \
        "alto_dir=${ALTO_DIR:-}")

CONLLU_FILES=("${OUTPUT_DIR}/UDP/"*.conllu)
TOTAL=${#CONLLU_FILES[@]}

for conllu in "${CONLLU_FILES[@]}"; do
    doc=$(basename "$conllu" .conllu)
    ne_dir="${OUTPUT_DIR}/NE/${doc}"

    if python3 api_util/summarize_nt_udp.py \
            --conllu     "$conllu" \
            --ne-dir     "$ne_dir" \
            --output-dir "${OUTPUT_DIR}/UDP_NE/${doc}" \
            --save-conllu-ne "${SAVE_CONLLU_NE:-true}" \
            --save-csv       "${SAVE_CSV:-true}" \
            --save-teitok    "${SAVE_TEITOK:-true}" \
            --alto-dir       "${ALTO_DIR:-}" \
            --summary-csv    "${OUTPUT_DIR}/summary_ne_counts.csv"; then

        python3 atrium_paradata.py success --state "$PARA_STATE" --type csv
        [ "${SAVE_CONLLU_NE:-true}" = "true" ] && \
            python3 atrium_paradata.py success --state "$PARA_STATE" --type conllu
        [ "${SAVE_TEITOK:-true}" = "true" ] && \
            python3 atrium_paradata.py success --state "$PARA_STATE" --type xml
    else
        python3 atrium_paradata.py skip \
            --state "$PARA_STATE" \
            --file  "$doc" \
            --reason "summarize_nt_udp failed"
    fi
done

python3 atrium_paradata.py finish --state "$PARA_STATE" --input-total "$TOTAL"