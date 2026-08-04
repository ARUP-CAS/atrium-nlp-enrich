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

# TEITOK output gate for this second emitter (issue #28). flexiconv writes
# {stem}.teitok.xml into the same $TEITOK_OUTPUT_DIR that api_4_stats.sh later
# validates in full, so unchecked output here surfaces as an unexplained
# failure there instead of at its source.
#
# --wellformed-only is deliberate: this output comes from a third-party
# converter across 13 input formats, and schemas/teitok/teitok.xsd describes
# api_util/teitok_alto.py's profile specifically. Gating flexiconv against
# that schema would reject documents for being a different — not a broken —
# TEITOK profile. Well-formedness plus a <TEI> root is what we can assert
# honestly today; see schemas/teitok/README.md for promoting this to the full
# XSD once a genuine flexiconv sample exists to model the profile from.
if ! python3 api_util/validate_teitok_xml.py "$TEITOK_OUTPUT_DIR" --allow-empty --wellformed-only; then
    log "[CRITICAL ERROR] flexiconv TEITOK output is not well-formed. Halting."
    exit 1
fi
