#!/usr/bin/env bash
# api_4_stats.sh – statistics + TEITOK generation + paradata
set -euo pipefail
source "${ATRIUM_CONFIG:-config_api.txt}"

OUTPUT_TYPES=""
[ "${SAVE_CSV:-true}"        = "true" ] && OUTPUT_TYPES="$OUTPUT_TYPES csv"
[ "${SAVE_CONLLU_NE:-true}"  = "true" ] && OUTPUT_TYPES="$OUTPUT_TYPES conllu"
[ "${SAVE_TEITOK:-true}"     = "true" ] && OUTPUT_TYPES="$OUTPUT_TYPES xml"
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
        "alto_dir=${INPUT_ALTO_DIR:-}" \
        "pages_dir=${INPUT_PAGES_DIR:-}" \
        "dpi=${IMAGE_DPI:-}" \
        "alto_dpi=${ALTO_DPI:-}")

TOTAL=$(find "${OUTPUT_DIR}/UDP" -name '*.conllu' -type f | wc -l)
rm -f "${OUTPUT_DIR}/summary_ne_counts.csv"

while IFS= read -r -d '' conllu; do
    rel_path="${conllu#${OUTPUT_DIR}/UDP/}"
    doc="${rel_path%.conllu}"
    doc_name=$(basename "$doc")

    ne_dir="${OUTPUT_DIR}/NE/${doc}"
    doc_out_dir="${OUTPUT_DIR}/UDP_NE/${doc}"
    tt_out_dir="${OUTPUT_DIR}/TEITOK/$(dirname "$doc")"

    mkdir -p "$doc_out_dir"
    mkdir -p "$tt_out_dir"

    csv_done=true;    conllu_done=true;    teitok_done=true
    [ "${SAVE_CSV:-true}"       = "true" ] && [ ! -f "${doc_out_dir}/${doc_name}.csv"         ] && csv_done=false
    [ "${SAVE_CONLLU_NE:-true}" = "true" ] && [ ! -f "${doc_out_dir}/${doc_name}.conllu"      ] && conllu_done=false
    [ "${SAVE_TEITOK:-true}"    = "true" ] && [ ! -f "${tt_out_dir}/${doc_name}.teitok.xml" ] && teitok_done=false

    if $csv_done && $conllu_done && $teitok_done; then
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
            --tt-dir         "$tt_out_dir" \
            --alto-dir       "${INPUT_ALTO_DIR:-}" \
            --pages-dir      "${INPUT_PAGES_DIR:-}" \
            --dpi            "${IMAGE_DPI:-}" \
            --alto-dpi       "${ALTO_DPI:-}" \
            --summary-csv    "${OUTPUT_DIR}/summary_ne_counts.csv"; then

        if $csv_done && $conllu_done && $teitok_done; then
            python3 atrium_paradata.py skip \
                --state "$PARA_STATE" \
                --file  "$doc" \
                --reason "all requested outputs already exist (resumed run)"
        else
            [ "${SAVE_CSV:-true}"       = "true" ] && \
                python3 atrium_paradata.py success --state "$PARA_STATE" --type csv
            [ "${SAVE_CONLLU_NE:-true}" = "true" ] && \
                python3 atrium_paradata.py success --state "$PARA_STATE" --type conllu
            [ "${SAVE_TEITOK:-true}"    = "true" ] && \
                python3 atrium_paradata.py success --state "$PARA_STATE" --type xml
        fi
    else
        # P1 FIX: Log the failure and exit immediately to halt the pipeline
        python3 atrium_paradata.py skip \
            --state "$PARA_STATE" \
            --file  "$doc" \
            --reason "summarize_nt_udp failed"

        echo "[CRITICAL ERROR] summarize_nt_udp aggregation failed for ${doc}. Halting pipeline." >&2
        exit 1
    fi
done < <(find "${OUTPUT_DIR}/UDP" -name '*.conllu' -type f -print0)

python3 atrium_paradata.py finish --state "$PARA_STATE" --input-total "$TOTAL"