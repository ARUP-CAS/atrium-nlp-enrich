#!/usr/bin/env bash
# api_1_manifest.sh – generate manifest + paradata
set -euo pipefail
source config_api.txt   # loads OUTPUT_DIR, INPUT_TABLES_DIR, LOG_FILE, etc.

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

TOTAL=0
ERRORS=0

for csv_file in "${INPUT_TABLES_DIR}"/*.csv "${INPUT_TABLES_DIR}"/*.xlsx; do
    [ -f "$csv_file" ] || continue
    TOTAL=$((TOTAL + 1))
    if python3 api_util/build_manifest_row.py "$csv_file" >> "${OUTPUT_DIR}/manifest.tsv"; then
        python3 atrium_paradata.py success --state "$PARA_STATE" --type tsv
    else
        python3 atrium_paradata.py skip \
            --state "$PARA_STATE" \
            --file  "$csv_file" \
            --reason "manifest row generation failed"
        ERRORS=$((ERRORS + 1))
    fi
done

python3 atrium_paradata.py finish --state "$PARA_STATE" --input-total "$TOTAL"
echo "[manifest] done: $TOTAL total, $ERRORS errors"