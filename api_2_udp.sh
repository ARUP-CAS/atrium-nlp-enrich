#!/bin/bash

# 1. Load Configuration & Common Functions
source ./api_util/api_common.sh

echo "=========================================="
echo " STEP 2: UDPIPE PROCESSING (CSV SOURCE)"
echo " Model: $MODEL_UDPIPE"
echo "=========================================="

# 2. Setup Directories
mkdir -p "$OUTPUT_DIR/UDP" "$WORK_DIR/CHUNKS"
MANIFEST="$OUTPUT_DIR/manifest.tsv"

# 3. Check Manifest (Dependence Maintained)
if [ ! -f "$MANIFEST" ]; then
    echo "Error: Manifest not found at $MANIFEST"
    echo "Please run the previous step (api_1_manifest.sh) to generate the manifest."
    exit 1
fi

log "Starting UDPipe processing..."

# ------------------------------------------------------------------
# Helper: Python script to parse CSV, sort by Page/Line, and extract text
# ------------------------------------------------------------------
extract_sorted_text() {
    python3 -c "
import sys, csv

input_file = '$1'
output_file = '$2'

try:
    # Use utf-8-sig to handle potential BOM from Excel-saved CSVs
    with open(input_file, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        data = []
        for row in reader:
            # Extract page and line numbers, defaulting to 0 if missing/invalid
            try:
                p = int(row.get('page_num', 0))
            except ValueError:
                p = 0
            try:
                l = int(row.get('line_num', 0))
            except ValueError:
                l = 0

            # Only collect rows that have actual text
            text_content = row.get('text', '')
            if text_content and text_content.strip():
                data.append({'p': p, 'l': l, 'text': text_content.strip()})

    # SORTING: Primary key = Page, Secondary key = Line
    # This reconstructs the document flow from the scattered CSV lines
    data.sort(key=lambda x: (x['p'], x['l']))

    with open(output_file, 'w', encoding='utf-8') as out:
        for item in data:
            out.write(item['text'] + '\n')

except Exception as e:
    sys.stderr.write(f'Error parsing CSV {input_file}: {str(e)}\n')
    sys.exit(1)
"
}

doc_count=0

# Loop through all CSV files in the input directory
# (We filter by extension to ensure we only process the source files)
find "$INPUT_DIR" -name "*.csv" | sort | while read csv_file; do

    # Extract ID (filename without extension, e.g., CTX193202973)
    doc_id=$(basename "$csv_file" .csv)
    final_conllu="$OUTPUT_DIR/UDP/${doc_id}.conllu"

    # Skip if output already exists and is not empty (Resume capability)
    if [ -s "$final_conllu" ]; then
        # log " -> Skipping $doc_id (already exists)"
        continue
    fi

    log " -> Processing Doc: $doc_id"

    # 1. Extract and Sort Text from CSV
    raw_text_file="$TEMP_TXT_DIR/${doc_id}.txt"

    # Ensure temp dir exists
    mkdir -p "$TEMP_TXT_DIR"

    extract_sorted_text "$csv_file" "$raw_text_file"

    if [ ! -s "$raw_text_file" ]; then
        log "   [Warning] No valid text content found in $doc_id. Skipping."
        rm -f "$raw_text_file"
        continue
    fi

    # 2. Prepare Temp Output
    current_temp_file="${final_conllu}.tmp"
    : > "$current_temp_file"

    # 3. Split Text into Chunks
    doc_chunk_dir="$CHUNK_DIR/${doc_id}"
    rm -rf "$doc_chunk_dir" && mkdir -p "$doc_chunk_dir"

    # Call the python chunker
    python3 api_util/chunk.py "$raw_text_file" "$doc_chunk_dir" "$WORD_CHUNK_LIMIT"

    # 4. Process Chunks with UDPipe
    is_first_chunk=true

    # Iterate over chunk files sorted by name (chunk_0.txt, chunk_1.txt...)
    # using sort -V for version sort handles chunk_1 vs chunk_10 correctly
    for chunk_file in $(ls "$doc_chunk_dir"/*.txt | sort -V); do
        [ -e "$chunk_file" ] || continue
        resp_file="${chunk_file}.json"

        # Call API with Retry Logic
        if api_call_with_retry "UDPipe" "$UDPIPE_URL" "$resp_file" \
            -F "data=@${chunk_file}" \
            -F "model=${MODEL_UDPIPE}" \
            -F "tokenizer=" \
            -F "tagger=" \
            -F "parser="; then

            raw_conllu=$(parse_json_result "$resp_file")

            if [ "$is_first_chunk" = true ]; then
                echo "$raw_conllu" >> "$current_temp_file"
                is_first_chunk=false
            else
                # Strip global headers (# newdoc, # generator) from subsequent chunks
                # so the combined file is a valid single CoNLL-U document
                echo "$raw_conllu" | grep -vE "^# (newdoc|newpar|generator|udpipe)" >> "$current_temp_file"
            fi

            # Ensure newline separator between chunks
            if [ -n "$(tail -n 1 "$current_temp_file")" ]; then
                echo "" >> "$current_temp_file"
            fi
        else
            log "   [Error] Failed to process chunk $(basename "$chunk_file") for $doc_id"
        fi

        # Respect API Rate Limits
        rate_limit
    done

    # 5. Finalize
    if [ -s "$current_temp_file" ]; then
        mv "$current_temp_file" "$final_conllu"
        log "   [Saved] $(basename "$final_conllu")"
        ((doc_count++))
    else
        rm -f "$current_temp_file"
        log "   [Error] Output empty or failed for $doc_id"
    fi

    # Cleanup temp files for this doc
    rm -f "$raw_text_file"
    rm -rf "$doc_chunk_dir"

done

echo "------------------------------------------"
echo "Done. Processed $doc_count new documents."
echo "Please run ./api_3_nt.sh next."
