#!/bin/bash
source ./api_util/api_common.sh

echo "=========================================="
echo " STEP 4: SUMMARIZATION & STATISTICS"
echo "=========================================="

INPUT_UDP_DIR="$OUTPUT_DIR/UDP"
INPUT_TSV_DIR="$OUTPUT_DIR/NE"
SUMMARY_OUT_DIR="$OUTPUT_DIR/UDP_NE"
STATS_FILE="$OUTPUT_DIR/summary_ne_counts.csv"

mkdir -p "$SUMMARY_OUT_DIR"
mkdir -p "$(dirname "$STATS_FILE")"

log "Starting consolidation..."
log "Input CoNLL-U: $INPUT_UDP_DIR"
log "Input TSV Root: $INPUT_TSV_DIR"

python3 api_util/summarize_nt_udp.py \
    --conllu-dir "$INPUT_UDP_DIR" \
    --tsv-dir "$INPUT_TSV_DIR" \
    --out-dir "$SUMMARY_OUT_DIR"

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
