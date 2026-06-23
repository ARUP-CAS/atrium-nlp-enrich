#!/usr/bin/env bash
# Convert non-tabular input formats directly to TEITOK XML using flexiconv.

# shellcheck disable=SC1091  # config path is dynamic (ATRIUM_CONFIG); not followed at lint time
source "config_api.txt"
# shellcheck disable=SC1091  # config path is dynamic (ATRIUM_CONFIG); not followed at lint time
source "api_util/api_common.sh"

mkdir -p "$TEITOK_OUTPUT_DIR"

if [ -z "$FLEXICONV_FORMATS" ]; then
    echo "FLEXICONV_FORMATS is empty. Skipping non-tabular processing."
    exit 0
fi

# Convert allowed formats to grep-friendly pattern
FORMAT_PATTERN=$(echo "$FLEXICONV_FORMATS" | tr ' ' '|' | tr ',' '|')

for f in "$INPUT_DOCS_DIR"/*; do
    [ -e "$f" ] || continue
    filename=$(basename -- "$f")
    ext="${filename##*.}"

    if echo "$ext" | grep -Eqw "$FORMAT_PATTERN"; then
        echo "Converting $filename to TEITOK XML..."

        # Execute the python conversion wrapper
        if ! python3 api_util/flexiconv_convert.py "$f" --out-dir "$TEITOK_OUTPUT_DIR"; then
            echo "Skipping $filename (Conversion failed or flexiconv not installed)."
            # Paradata hook can be added here if needed
        fi
    fi
done
