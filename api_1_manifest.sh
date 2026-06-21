#!/usr/bin/env bash
# api_1_manifest.sh – generate manifest + paradata
set -euo pipefail
# shellcheck disable=SC1090  # config path is dynamic (ATRIUM_CONFIG); not followed at lint time
source "${ATRIUM_CONFIG:-config_api.txt}"

# P1 FIX: Validate input directory exists before starting
if [[ ! -d "${INPUT_TABLES_DIR}" ]]; then
    echo "[ERROR] Input directory '${INPUT_TABLES_DIR}' does not exist. Aborting." >&2
    exit 1
fi

# ── paradata: start ───────────────────────────────────────────────────────────
PARA_STATE=$(python3 atrium_paradata.py start \
    --program nlp-enrich \
    --paradata-dir "${OUTPUT_DIR}/paradata" \
    --output-types tsv \
    --config \
        "script=api_1_manifest" \
        "input_dir=${INPUT_TABLES_DIR}" \
        "output_manifest=${OUTPUT_DIR}/manifest.tsv")
# ── end paradata start ────────────────────────────────────────────────────────

mkdir -p "${OUTPUT_DIR}"

# Write header if manifest does not exist yet
if [ ! -f "${OUTPUT_DIR}/manifest.tsv" ]; then
    echo -e "file\tpage\tpath" > "${OUTPUT_DIR}/manifest.tsv"
fi

TOTAL=0

for csv_file in "${INPUT_TABLES_DIR}"/*.csv "${INPUT_TABLES_DIR}"/*.xlsx; do
    [ -f "$csv_file" ] || continue
    TOTAL=$((TOTAL + 1))

    # build_manifest_row.py reads one CSV/XLSX, writes a temp .txt, and prints
    # a single TSV row:  doc_id <TAB> page_count <TAB> /path/to/text_file
    if NEW_ROW=$(python3 api_util/build_manifest_row.py "$csv_file" --text-dir "${TEMP_TXT_DIR:-./TEMP/TXT_EXTRACT}"); then

        DOC_ID=$(echo "$NEW_ROW" | cut -f1)

        if grep -q "^${DOC_ID}[[:space:]]" "${OUTPUT_DIR}/manifest.tsv"; then
            grep -v "^${DOC_ID}[[:space:]]" "${OUTPUT_DIR}/manifest.tsv" > "${OUTPUT_DIR}/manifest.tmp"
            mv "${OUTPUT_DIR}/manifest.tmp" "${OUTPUT_DIR}/manifest.tsv"
        fi

        echo "$NEW_ROW" >> "${OUTPUT_DIR}/manifest.tsv"
        python3 atrium_paradata.py success --state "$PARA_STATE" --type tsv
    else
        # P1 FIX: Log the failure and exit immediately to halt the pipeline
        python3 atrium_paradata.py skip \
            --state "$PARA_STATE" \
            --file  "$csv_file" \
            --reason "manifest row generation failed"

        echo "[CRITICAL ERROR] Manifest generation failed for ${csv_file}. Halting pipeline." >&2
        exit 1
    fi
done

python3 atrium_paradata.py finish --state "$PARA_STATE" --input-total "$TOTAL"
echo "[manifest] done: $TOTAL total, 0 errors"
