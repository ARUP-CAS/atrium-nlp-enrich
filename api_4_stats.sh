#!/usr/bin/env bash
# api_4_stats.sh – statistics + TEITOK generation + paradata
# PATCHED: (1) csv success is now gated on SAVE_CSV flag
#          (2) already-finished documents are logged as skip so paradata totals stay coherent
set -euo pipefail
source config_api.txt

# Determine which output types are active
OUTPUT_TYPES=""
[ "${SAVE_CSV:-true}"        = "true" ] && OUTPUT_TYPES="$OUTPUT_TYPES csv"
[ "${SAVE_CONLLU_NE:-true}"  = "true" ] && OUTPUT_TYPES="$OUTPUT_TYPES conllu"
[ "${SAVE_TEITOK:-true}"     = "true" ] && OUTPUT_TYPES="$OUTPUT_TYPES xml"
# strip leading space
OUTPUT_TYPES="${OUTPUT_TYPES# }"

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
        "alto_dir=${ALTO_DIR:-}" \
        "pages_dir=${INPUT_PAGES_DIR:-}")

CONLLU_FILES=("${OUTPUT_DIR}/UDP/"*.conllu)
TOTAL=${#CONLLU_FILES[@]}

for conllu in "${CONLLU_FILES[@]}"; do
    doc=$(basename "$conllu" .conllu)
    ne_dir="${OUTPUT_DIR}/NE/${doc}"
    doc_out_dir="${OUTPUT_DIR}/UDP_NE/${doc}"

    # --- FIX 1: check per active flag, not a blanket "need_merge" silent skip ---
    csv_done=true;    conllu_done=true;    teitok_done=true
    [ "${SAVE_CSV:-true}"       = "true" ] && [ ! -f "${doc_out_dir}/${doc}.csv"         ] && csv_done=false
    [ "${SAVE_CONLLU_NE:-true}" = "true" ] && [ ! -f "${doc_out_dir}/${doc}.conllu"      ] && conllu_done=false
    [ "${SAVE_TEITOK:-true}"    = "true" ] && [ ! -f "${OUTPUT_DIR}/TEITOK/${doc}.teitok.xml" ] && teitok_done=false

    if $csv_done && $conllu_done && $teitok_done; then
        # FIX 2: record already-finished docs so totals stay coherent on reruns
        python3 atrium_paradata.py skip \
            --state "$PARA_STATE" \
            --file  "$doc" \
            --reason "all requested outputs already exist (resumed run)"
        continue
    fi

    if python3 api_util/summarize_nt_udp.py \
            --conllu     "$conllu" \
            --ne-dir     "$ne_dir" \
            --output-dir "$doc_out_dir" \
            --save-conllu-ne "${SAVE_CONLLU_NE:-true}" \
            --save-csv       "${SAVE_CSV:-true}" \
            --save-teitok    "${SAVE_TEITOK:-true}" \
            --alto-dir       "${ALTO_DIR:-}" \
            --pages-dir      "${INPUT_PAGES_DIR:-}" \
            --summary-csv    "${OUTPUT_DIR}/summary_ne_counts.csv"; then

        # FIX 3: gate each success call on its own flag (csv was previously unconditional)
        [ "${SAVE_CSV:-true}"       = "true" ] && \
            python3 atrium_paradata.py success --state "$PARA_STATE" --type csv
        [ "${SAVE_CONLLU_NE:-true}" = "true" ] && \
            python3 atrium_paradata.py success --state "$PARA_STATE" --type conllu
        [ "${SAVE_TEITOK:-true}"    = "true" ] && \
            python3 atrium_paradata.py success --state "$PARA_STATE" --type xml
    else
        python3 atrium_paradata.py skip \
            --state "$PARA_STATE" \
            --file  "$doc" \
            --reason "summarize_nt_udp failed"
    fi
done

python3 atrium_paradata.py finish --state "$PARA_STATE" --input-total "$TOTAL"