#!/bin/bash
source ./api_util/api_common.sh

echo "=========================================="
echo " STEP 4: SUMMARIZATION & STATISTICS"
echo "=========================================="

INPUT_UDP_DIR="$CONLLU_INPUT_DIR"
INPUT_TSV_DIR="$TSV_INPUT_DIR"
SUMMARY_OUT_DIR="$SUMMARY_OUTPUT_DIR"
TEITOK_OUT_DIR="$TEITOK_OUTPUT_DIR"
STATS_FILE="$OUTPUT_DIR/summary_ne_counts.csv"
INPUT_ALTO_DIR="$ALTO_DIR"

mkdir -p "$SUMMARY_OUT_DIR"
mkdir -p "$(dirname "$STATS_FILE")"

log "Starting consolidation..."
log "Input CoNLL-U: $INPUT_UDP_DIR"
log "Input TSV Root: $INPUT_TSV_DIR"

python3 api_util/summarize_nt_udp.py \
    --conllu-dir "$INPUT_UDP_DIR" \
    --tsv-dir    "$INPUT_TSV_DIR" \
    --out-dir    "$SUMMARY_OUT_DIR" \
    --alto-dir "$INPUT_ALTO_DIR" \
    --tt-dir "$TEITOK_OUT_DIR" \
    --save-conllu-ne "${SAVE_CONLLU_NE:-1}" \
    --save-csv       "${SAVE_CSV:-1}" \
    --save-teitok    "${SAVE_TEITOK:-0}"

if [ $? -eq 0 ]; then
    log "Summarization process finished (checked new/existing files)."
else
    log "Error: Summarization script failed."
    exit 1
fi

log "Generating entity statistics from NameTag TSVs..."

python3 api_util/analyze.py "$INPUT_TSV_DIR" "$STATS_FILE"

if [ $? -eq 0 ]; then
    log "Statistics saved to: $STATS_FILE"
else
    log "Warning: Statistics generation encountered an issue."
fi

echo "------------------------------------------"
echo "PIPELINE COMPLETE."
echo "Main Output: $SUMMARY_OUT_DIR"
echo "Stats Output: $STATS_FILE"
