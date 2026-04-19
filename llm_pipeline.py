import csv
import json
import os
from pathlib import Path
from typing import List, Dict, Any

import ollama
from pydantic import BaseModel, Field

# Import the official ATRIUM paradata module (must be in the same directory)
from atrium_paradata import ParadataLogger
from vocab_manager import VocabularyManager


# ---------------------------------------------------------------------------
# 1. Configuration Loader
# ---------------------------------------------------------------------------
def load_config(config_path: str = "llm_config.txt") -> Dict[str, str]:
    """Reads the simple KEY=VALUE configuration file."""
    config = {}
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                config[key.strip()] = value.strip()
    return config


# ---------------------------------------------------------------------------
# 2. Schema Definition
# ---------------------------------------------------------------------------
class SemanticEnrichment(BaseModel):
    """Rigid Pydantic schema enforcing the LLM's output structure."""
    extracted_keywords_cs: List[str] = Field(..., description="Keywords identified in the original Czech text.")
    extracted_keywords_en: List[str] = Field(..., description="English translations of the extracted keywords.")
    teater_broad_category: str = Field(...,
                                       description="The broad TEATER category matched from the allowed vocabulary.")
    teater_narrow_category: str = Field(...,
                                        description="The narrow TEATER category matched from the allowed vocabulary.")
    confidence_score: float = Field(..., ge=0.0, le=1.0, description="Confidence score between 0.0 and 1.0.")


# ---------------------------------------------------------------------------
# 3. Core Processing Logic
# ---------------------------------------------------------------------------
def process_document(
        csv_path: Path,
        model_name: str,
        ollama_host: str,
        vocab_str: str,
        temperature: float,
        logger: ParadataLogger
) -> List[dict]:
    """
    Reads a CSV, runs LLM enrichment on each text line, and returns the merged results.
    Line-level failures are recorded in the ParadataLogger as skipped sub-entities.
    """
    file_id = csv_path.stem
    enriched_document_lines = []

    system_instruction = (
        "You are an expert archaeological data extractor. Map the input text strictly "
        "to the provided nested vocabulary. Output only the requested JSON schema.\n\n"
        f"PERMITTED NESTED VOCABULARY:\n{vocab_str}"
    )

    client = ollama.Client(host=ollama_host) if ollama_host else ollama

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:
            try:
                page_num = int(row.get("page", 0))
                line_num = int(row.get("line", 0))
                text_chunk = row.get("text", "").strip()
            except ValueError:
                logger.log_skip(f"{file_id}:line_unknown", "Invalid integer conversion for page/line.")
                continue

            if not text_chunk:
                continue

            try:
                # Execute Constrained Inference
                response = client.chat(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": system_instruction},
                        {"role": "user", "content": f"Text to analyze: {text_chunk}"}
                    ],
                    format=SemanticEnrichment.model_json_schema(),
                    options={"temperature": temperature, "num_predict": 256}
                )

                # Validate LLM generation against the strict Pydantic model
                llm_output_raw = response['message']['content']
                semantic_data = SemanticEnrichment.model_validate_json(llm_output_raw)

                # Merge deterministic CSV metadata with the semantic LLM extraction
                final_record = {
                    "file_id": file_id,
                    "page": page_num,
                    "line": line_num,
                    "original_text": text_chunk,
                    "enrichment": semantic_data.model_dump()
                }

                enriched_document_lines.append(final_record)

            except Exception as e:
                # Log line-level failures as a "skip" in the paradata to maintain deep traceability
                print(f"[{file_id}] LLM Error on P{page_num} L{line_num}: {e}")
                logger.log_skip(f"{file_id}:P{page_num}_L{line_num}", f"LLM inference/validation failed: {str(e)}")

    return enriched_document_lines


# ---------------------------------------------------------------------------
# 4. Pipeline Execution
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # 1. Load Configuration
    config = load_config("llm_config.txt")

    MODEL_CHOICE = config.get("MODEL_NAME", "qwen2.5:14b-instruct")
    OLLAMA_HOST = config.get("OLLAMA_HOST", "")
    INPUT_DIR = Path(config.get("INPUT_DIR", "data_samples/DOC_LINE_LANG_CLASS"))
    OUTPUT_DIR = Path(config.get("OUTPUT_DIR", "data_samples/KW_PER_DOC"))
    VOCAB_PATH = config.get("VOCAB_PATH", "data_samples/teater_nested_vocab.json")
    PARADATA_DIR = config.get("PARADATA_DIR", "paradata")
    TEMPERATURE = float(config.get("LLM_TEMPERATURE", "0.0"))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 2. Initialize the official ATRIUM Paradata Logger
    # We pass the entire parsed config dictionary to take a snapshot of the runtime settings
    logger = ParadataLogger(
        program="nlp-enrich",
        config=config,
        paradata_dir=PARADATA_DIR,
        output_types=["json"]
    )

    # 3. Load Vocabulary
    vocab_mgr = VocabularyManager(vocab_path=VOCAB_PATH)
    vocab_json_string = vocab_mgr.get_prompt_string()

    print(f"=== Starting ATRIUM Enrichment Pipeline ({MODEL_CHOICE}) ===")

    csv_files = list(INPUT_DIR.glob("*.csv"))
    total_inputs = len(csv_files)

    # 4. Process Loop
    for csv_file in csv_files:
        print(f"Processing: {csv_file.name}...")
        try:
            enriched_results = process_document(
                csv_file, MODEL_CHOICE, OLLAMA_HOST, vocab_json_string, TEMPERATURE, logger
            )

            # Save enriched document
            if enriched_results:
                out_file = OUTPUT_DIR / f"{csv_file.stem}_enriched.json"
                with open(out_file, "w", encoding="utf-8") as out_f:
                    json.dump(enriched_results, out_f, indent=4, ensure_ascii=False)

                print(f" -> Saved {len(enriched_results)} enriched lines to {out_file.name}")

                # Update ATRIUM Paradata counters for success
                logger.log_success("json", count=1)
                logger.log_document_success()
            else:
                logger.log_skip(csv_file.name, "No valid text lines extracted.")

        except Exception as e:
            print(f"Critical error processing {csv_file.name}: {e}")
            logger.log_skip(csv_file.name, str(e))

    # 5. Finalize Log
    # Using a try/finally block is standard practice, but we can call it explicitly here
    paradata_path = logger.finalize(input_total=total_inputs)
    print(f"\nPipeline Complete. Official paradata logged to: {paradata_path}")