#!/bin/bash
source ./api_util/api_common.sh

echo "=========================================="
echo " STEP 1: MANIFEST GENERATION (CSV INGEST)"
echo " Source: ../../ARUB/DOC_LINE_LANG_CLASS"
echo "=========================================="

# 1. Configuration
# We hardcode the input location relative to where the script runs
#INPUT_DIR="../../ARUB/DOC_LINE_LANG_CLASS"

# We create a cache directory for the extracted text
TEXT_CACHE_DIR="$WORK_DIR/TEXT_CACHE"
MANIFEST="$OUTPUT_DIR/manifest.tsv"

mkdir -p "$WORK_DIR" "$TEXT_CACHE_DIR"

# 2. Python Helper: Extract & Sort Text
# This function reads a CSV, sorts by page/line, and prints the full text.
extract_csv_text() {
    python3 -c "
import sys, csv

try:
    # Open CSV with utf-8 encoding
    with open('$1', 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        data = []
        for row in reader:
            # Parse page/line integers safely (default to 0 if missing)
            try: p = int(row.get('page_num', 0))
            except: p = 0
            try: l = int(row.get('line_num', 0))
            except: l = 0

            # Only keep rows with actual text
            if row.get('text') and str(row.get('text')).strip():
                data.append({'p': p, 'l': l, 'text': row['text']})

    # SORTING: Primary = Page, Secondary = Line
    data.sort(key=lambda x: (x['p'], x['l']))

    # Output text content
    for item in data:
        print(item['text'])

except Exception as e:
    # Print error to stderr so it doesn't break the pipe, but warn user
    sys.stderr.write(f'[Error parsing $1]: {e}\n')
"
}

# 3. Main Loop
echo "Generating manifest at $MANIFEST..."
# Initialize/Clear manifest
: > "$MANIFEST"

count=0

# Check if directory exists
if [ ! -d "$INPUT_DIR" ]; then
    echo "Error: Directory $INPUT_DIR does not exist."
    exit 1
fi

# Find all .csv files and process them
find "$INPUT_DIR" -name "*.csv" | sort | while read -r csv_file; do

    # Extract Doc ID (filename without extension)
    filename=$(basename "$csv_file")
    doc_id="${filename%.*}"

    # Define where the clean text version will live
    target_txt="$TEXT_CACHE_DIR/${doc_id}.txt"

    # Run extraction
    extract_csv_text "$csv_file" > "$target_txt"

    # Only add to manifest if file is not empty
    if [ -s "$target_txt" ]; then
        # Format: DOC_ID [TAB] PAGE_NUM [TAB] FILE_PATH
        # We use '1' for page_num since we merged the whole doc
        echo -e "${doc_id}\t1\t${target_txt}" >> "$MANIFEST"
        ((count++))

        # Optional: Progress indicator
        echo -ne "Processed: $count docs\r"
    else
        # Cleanup empty files
        rm -f "$target_txt"
        echo "Warning: Skipped empty/invalid file: $doc_id"
    fi
done

echo -e "\n------------------------------------------"
echo "Done. Added $count documents to manifest."
echo "Clean text cached in: $TEXT_CACHE_DIR"
echo "Please run ./api_2_udp.sh next."


##!/bin/bash
#source ./api_util/api_common.sh
#
#echo "=========================================="
#echo " STEP 1: MANIFEST GENERATION"
#echo " Input: $INPUT_DIR"
#echo "=========================================="
#
#mkdir -p "$WORK_DIR"
#log "Generating sorted file manifest..."
#
## Calls the python script using the config variables
#python3 api_util/manifest.py "$INPUT_DIR" "$WORK_DIR/manifest.tsv"
#
#log "Manifest created at $WORK_DIR/manifest.tsv"
#echo "------------------------------------------"
#echo "Done. Please run ./api_2_udp.sh next."
#
#

