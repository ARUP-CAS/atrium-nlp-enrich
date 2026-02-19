#!/bin/bash
source ./api_util/api_common.sh

echo "=========================================="
echo " STEP 3: NAMETAG PROCESSING (Chunked)"
echo " Model: $MODEL_NAMETAG"
echo "=========================================="

mkdir -p "$OUTPUT_DIR/NE"
log "Starting NameTag processing..."

count=0

# Define chunk size (lines). 3000 lines is usually safe (~500KB - 1MB depending on density)
CHUNK_SIZE=3000

for conllu_file in "$OUTPUT_DIR/UDP"/*.conllu; do
    [ -e "$conllu_file" ] || continue

    filename=$(basename "$conllu_file")
    basename_no_ext="${filename%.conllu}"

    # Create a specific directory for this document's pages
    doc_output_dir="$OUTPUT_DIR/NE/$basename_no_ext"

    # Skip if directory exists and is not empty
    if [ -d "$doc_output_dir" ] && [ "$(ls -A "$doc_output_dir")" ]; then
        continue
    fi

    mkdir -p "$doc_output_dir"
    log " -> Tagging: $filename (Output: $doc_output_dir)"

    # 1. PREPARE CLEAN INPUT
    clean_input="${WORK_DIR}/temp_clean_${filename}"
    sed '/^# generator/d; /^# udpipe_model/d' "$conllu_file" > "$clean_input"

    # Temp dir for chunks
    chunk_dir="${WORK_DIR}/chunks_${basename_no_ext}"
    mkdir -p "$chunk_dir"

    # 2. SPLIT INPUT INTO CHUNKS (Python helper to respect sentence boundaries)
    # We use python to ensure we don't split in the middle of a sentence block (between empty lines)
    log "    ...Splitting $filename into chunks (max $CHUNK_SIZE lines)..."
    python3 -c "
import sys
infile = sys.argv[1]
out_prefix = sys.argv[2]
max_lines = int(sys.argv[3])

with open(infile, 'r', encoding='utf-8') as f:
    lines = f.readlines()

chunk_idx = 0
current_chunk = []

for line in lines:
    current_chunk.append(line)
    # Only split if we are over limit AND at an empty line (end of sentence)
    if len(current_chunk) >= max_lines and not line.strip():
        with open(f'{out_prefix}_{chunk_idx:03d}.tmp', 'w', encoding='utf-8') as out:
            out.writelines(current_chunk)
        current_chunk = []
        chunk_idx += 1

# Write remainder
if current_chunk:
    with open(f'{out_prefix}_{chunk_idx:03d}.tmp', 'w', encoding='utf-8') as out:
        out.writelines(current_chunk)
" "$clean_input" "$chunk_dir/chunk" "$CHUNK_SIZE"


    # 3. PROCESS EACH CHUNK
    chunk_files=("$chunk_dir"/*.tmp)
    all_chunks_ok=true

    for chunk_file in "${chunk_files[@]}"; do
        chunk_name=$(basename "$chunk_file")
        chunk_resp="$chunk_dir/${chunk_name}.json"

        # Call API for this chunk
        if ! api_call_with_retry "NameTag-Chunk" "$NAMETAG_URL" "$chunk_resp" \
            -F "data=@${chunk_file}" -F "input=conllu" -F "output=conll" \
            -F "model=${MODEL_NAMETAG}"; then

            log " [ERR] Failed to process chunk $chunk_name. Aborting document."
            all_chunks_ok=false
            break
        fi

        # Rate limit between chunks slightly
        sleep 0.2
    done

    # 4. MERGE RESULTS & GENERATE FINAL OUTPUT
    if [ "$all_chunks_ok" = true ]; then
        final_resp_file="$WORK_DIR/nametag_response_${filename}.json"

        # Merge JSONs using Python
        python3 -c "
import sys, json, glob, os

outfile = sys.argv[1]
search_pattern = sys.argv[2]

files = sorted(glob.glob(search_pattern))
full_text_parts = []

for fpath in files:
    try:
        with open(fpath, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # Extract result text
            txt = data.get('result', '').strip()
            if txt:
                full_text_parts.append(txt)
    except Exception as e:
        sys.stderr.write(f'Error reading {fpath}: {e}\n')

# Join with double newline to ensure sentence separation is maintained
merged_text = '\n\n'.join(full_text_parts)

with open(outfile, 'w', encoding='utf-8') as f:
    json.dump({'result': merged_text}, f, ensure_ascii=False)
" "$final_resp_file" "$chunk_dir/*.json"

        # 5. PARSE (Using existing nametag.py)
        # We pass the MERGED json, but the ORIGINAL conllu (for page mapping)
        python3 api_util/nametag.py \
            "$conllu_file" \
            "$final_resp_file" \
            "$doc_output_dir" \
            "$basename_no_ext"

        ((count++))

        # Cleanup response
        rm -f "$final_resp_file"
    else
        log " [WARN] Skipping $filename due to chunk errors."
    fi

    # Cleanup Chunks
    rm -rf "$chunk_dir"
    rm -f "$clean_input"

    rate_limit
done

log "Finished. Processed $count documents."
echo "------------------------------------------"
echo "Done. Please run ./api_4_stats.sh next."
