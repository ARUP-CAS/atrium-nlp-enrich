#!/bin/bash
# api_util/api_common.sh

# 1. Load Configuration
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
PROJECT_ROOT="$SCRIPT_DIR/.."

if [ -f "$PROJECT_ROOT/config_api.txt" ]; then
    source "$PROJECT_ROOT/config_api.txt"
else
    echo "Error: config_api.txt not found in $PROJECT_ROOT"
    exit 1
fi

# 2. Validation
if [ ! -d "$INPUT_TABLES_DIR" ]; then
    echo "Error: Input directory '$INPUT_TABLES_DIR' does not exist."
    echo "Please update INPUT_TABLES_DIR in config_api.txt"
    exit 1
fi

# Check for required Python scripts
for script in manifest.py chunk.py analyze.py; do
    if [ ! -f "$SCRIPT_DIR/$script" ]; then
        echo "Error: Helper script '$script' not found in $SCRIPT_DIR"
        exit 1
    fi
done

# 3. Setup Output
mkdir -p "$OUTPUT_DIR"

# 4. Helper Functions
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

rate_limit() {
    # Adjust sleep based on API limits. 0.2s = 5 req/s.
    sleep 0.2
}

parse_json_result() {
    local json_file="$1"
    # Improved: Pass filename as argument to avoid quoting issues
    python3 -c "import sys, json;
try:
    with open(sys.argv[1], 'r', encoding='utf-8') as f:
        data = json.load(f)
        result = data.get('result', '')
        if not result: sys.exit(2)
        sys.stdout.write(result)
except Exception: sys.exit(1)" "$json_file"
}

api_call_with_retry() {
    local api_name="$1"
    local url="$2"
    local response_file="$3"
    shift 3

    local attempt=1
    local delay=1

    while [ $attempt -le $MAX_RETRIES ]; do
        local http_code_file="${response_file}.code"
        # Pass remaining arguments ("$@") to curl (flags like -F)
        curl -s -S -w "%{http_code}" "$@" "$url" -o "$response_file" > "$http_code_file"
        local http_code=$(cat "$http_code_file")
        rm -f "$http_code_file"

        if [ "$http_code" = "200" ]; then
            return 0
        fi

        log "[WARN] $api_name failed (HTTP $http_code). Retrying in ${delay}s..."
        sleep "$delay"
        # Calculate backoff using python
        delay=$(python3 -c "print(int($delay * $BACKOFF_FACTOR + 1))")
        attempt=$((attempt + 1))
    done

    log "[ERR] $api_name failed permanently after $MAX_RETRIES attempts."
    return 1
}