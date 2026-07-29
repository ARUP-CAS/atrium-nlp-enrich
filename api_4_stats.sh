#!/usr/bin/env bash
# api_4_stats.sh – statistics + TEITOK generation + paradata
set -euo pipefail

DOC_JSON_DIR=""
INCLUDE_LINES=""

while [[ "$#" -gt 0 ]]; do
    case $1 in
        --document-json-dir) DOC_JSON_DIR="$2"; shift ;;
        --include-lines) INCLUDE_LINES="--include-lines" ;;
    esac
    shift
done

# shellcheck disable=SC1090  # config path is dynamic (ATRIUM_CONFIG); not followed at lint time
source "${ATRIUM_CONFIG:-config_api.txt}"

OUTPUT_TYPES=""
[ "${SAVE_CSV:-true}"        = "true" ] && OUTPUT_TYPES="$OUTPUT_TYPES csv"
[ "${SAVE_CONLLU_NE:-true}"  = "true" ] && OUTPUT_TYPES="$OUTPUT_TYPES conllu"
[ "${SAVE_TEITOK:-true}"     = "true" ] && OUTPUT_TYPES="$OUTPUT_TYPES xml"
OUTPUT_TYPES="${OUTPUT_TYPES# }"

# shellcheck disable=SC2086  # OUTPUT_TYPES is intentionally word-split into multiple --output-types args
PARA_STATE=$(python3 atrium_paradata.py start \
    --program nlp-enrich \
    --paradata-dir "${PARADATA_DIR}" \
    --output-types $OUTPUT_TYPES \
    --config \
        "script=api_4_stats" \
        "conllu_input_dir=${CONLLU_INPUT_DIR}" \
        "tsv_input_dir=${TSV_INPUT_DIR}" \
        "output_dir=${SUMMARY_OUTPUT_DIR}" \
        "save_conllu_ne=${SAVE_CONLLU_NE:-true}" \
        "save_csv=${SAVE_CSV:-true}" \
        "save_teitok=${SAVE_TEITOK:-true}" \
        "alto_dir=${INPUT_ALTO_DIR:-}" \
        "pages_dir=${INPUT_PAGES_DIR:-}" \
        "dpi=${IMAGE_DPI:-}" \
        "alto_dpi=${ALTO_DPI:-}")

TOTAL=$(find "${CONLLU_INPUT_DIR}" -name '*.conllu' -type f | wc -l)
rm -f "${OUTPUT_DIR}/summary_ne_counts.csv"

DOC_JSON_FLAGS=""
if [ -n "$DOC_JSON_DIR" ]; then
    DOC_JSON_FLAGS="--document-json-dir $DOC_JSON_DIR $INCLUDE_LINES"
fi

while IFS= read -r -d '' conllu; do
    rel_path="${conllu#"${OUTPUT_DIR}"/UDP/}"
    doc="${rel_path%.conllu}"
    doc_name=$(basename "$doc")

    ne_dir="${TSV_INPUT_DIR}/${doc}"
    doc_out_dir="${SUMMARY_OUTPUT_DIR}/${doc}"
    tt_out_dir="${TEITOK_OUTPUT_DIR}/$(dirname "$doc")"

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

    # shellcheck disable=SC2086 # Allow word-splitting for DOC_JSON_FLAGS
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
            --summary-csv    "${OUTPUT_DIR}/summary_ne_counts.csv" \
            --state-dir      "${PARADATA_DIR}" \
            $DOC_JSON_FLAGS; then

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
done < <(find "${CONLLU_INPUT_DIR}" -name '*.conllu' -type f -print0)

python3 atrium_paradata.py finish --state "$PARA_STATE" --input-total "$TOTAL"