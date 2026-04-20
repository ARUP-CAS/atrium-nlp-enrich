import csv
import json
from pathlib import Path
from typing import List, Dict, Optional
import torch

from transformers import AutoModelForCausalLM, AutoTokenizer
from pydantic import BaseModel, Field
from lmformatenforcer import JsonSchemaParser
from lmformatenforcer.integrations.transformers import build_transformers_prefix_allowed_tokens_fn

from atrium_paradata import ParadataLogger
from vocab_manager import VocabularyManager


# ---------------------------------------------------------------------------
# 1. Model Registry
# ---------------------------------------------------------------------------
MODEL_REGISTRY: Dict[str, Dict] = {
    "qwen2.5-14b-awq": {
        "hf_id": "Qwen/Qwen2.5-14B-Instruct-AWQ",
        "context_window": 32768,
        "trust_remote_code": False,
        "torch_dtype": torch.float16,
        "hf_token_required": False,
    },
    "qwen2.5-7b": {
        "hf_id": "Qwen/Qwen2.5-7B-Instruct",
        "context_window": 32768,
        "trust_remote_code": False,
        "torch_dtype": torch.bfloat16,
        "hf_token_required": False,
    },
    "mistral-nemo-12b": {
        "hf_id": "mistralai/Mistral-Nemo-Instruct-2407",
        "context_window": 128000,
        "trust_remote_code": False,
        "torch_dtype": torch.bfloat16,
        "hf_token_required": False,
    },
    "aya-expanse-8b": {
        "hf_id": "CohereForAI/aya-expanse-8b",
        "context_window": 8192,
        "trust_remote_code": True,
        "torch_dtype": torch.bfloat16,
        "hf_token_required": False,
    },
    "bielik-11b": {
        "hf_id": "speakleash/Bielik-11B-v2.3-Instruct",
        "context_window": 8192,
        "trust_remote_code": True,
        "torch_dtype": torch.bfloat16,
        "hf_token_required": False,
    },
    "llama3.1-8b": {
        "hf_id": "meta-llama/Meta-Llama-3.1-8B-Instruct",
        "context_window": 128000,
        "trust_remote_code": False,
        "torch_dtype": torch.bfloat16,
        "hf_token_required": True,   # must set HF_TOKEN in env or llm_config.txt
    },
}

MAX_NEW_TOKENS = 256
# Reserve tokens for: output + chat template overhead + safety margin
CONTEXT_RESERVED = MAX_NEW_TOKENS + 256


# ---------------------------------------------------------------------------
# 2. Configuration Loader
# ---------------------------------------------------------------------------
def load_config(config_path: str = "llm_config.txt") -> Dict[str, str]:
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
# 3. Schema Definition
# ---------------------------------------------------------------------------
class SemanticEnrichment(BaseModel):
    extracted_keywords_cs: List[str] = Field(..., description="Keywords identified in the original Czech text.")
    extracted_keywords_en: List[str] = Field(..., description="English translations of the extracted keywords.")
    teater_broad_category: str = Field(..., description="The broad TEATER category matched from the allowed vocabulary.")
    teater_narrow_category: str = Field(..., description="The narrow TEATER category matched from the allowed vocabulary.")
    confidence_score: float = Field(..., ge=0.0, le=1.0, description="Confidence score between 0.0 and 1.0.")


# ---------------------------------------------------------------------------
# 4. Vocabulary truncation for tight context windows
# ---------------------------------------------------------------------------
def build_system_prompt(vocab_str: str, tokenizer, max_tokens: int) -> str:
    """
    Build the system prompt, truncating vocabulary if it won't fit in the
    context budget. Necessary for 8k-context models (Aya, Bielik).
    """
    header = (
        "You are an expert archaeological data extractor. Map the input text strictly "
        "to the provided nested vocabulary. Output only the requested JSON schema.\n\n"
        "PERMITTED NESTED VOCABULARY:\n"
    )
    full_prompt = header + vocab_str

    # Count tokens and truncate vocab if needed
    token_count = len(tokenizer.encode(full_prompt))
    if token_count <= max_tokens:
        return full_prompt

    print(f"[WARN] System prompt is {token_count} tokens — exceeds budget of {max_tokens}. "
          f"Truncating vocabulary string.")

    # Binary search for the longest vocab_str that fits
    lo, hi = 0, len(vocab_str)
    while lo < hi - 1:
        mid = (lo + hi) // 2
        candidate = header + vocab_str[:mid] + "\n... [truncated]"
        if len(tokenizer.encode(candidate)) <= max_tokens:
            lo = mid
        else:
            hi = mid

    truncated = header + vocab_str[:lo] + "\n... [truncated]"
    print(f"[WARN] Vocabulary truncated to {lo} chars to fit context window.")
    return truncated


# ---------------------------------------------------------------------------
# 5. Model + Tokenizer Loader
# ---------------------------------------------------------------------------
def load_model_and_tokenizer(
    model_key: str,
    hf_token: Optional[str] = None
):
    if model_key not in MODEL_REGISTRY:
        available = ", ".join(MODEL_REGISTRY.keys())
        raise ValueError(f"Unknown MODEL_KEY '{model_key}'. Available: {available}")

    spec = MODEL_REGISTRY[model_key]
    hf_id = spec["hf_id"]

    if spec["hf_token_required"] and not hf_token:
        raise EnvironmentError(
            f"Model '{model_key}' requires a HuggingFace token. "
            f"Set HF_TOKEN in llm_config.txt or as an environment variable."
        )

    print(f"=== Loading: {hf_id} ===")

    tokenizer = AutoTokenizer.from_pretrained(
        hf_id,
        trust_remote_code=spec["trust_remote_code"],
        token=hf_token or None,
    )

    # Ensure pad token exists (some models omit it)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        hf_id,
        device_map="auto",
        torch_dtype=spec["torch_dtype"],
        trust_remote_code=spec["trust_remote_code"],
        token=hf_token or None,
    )
    model.eval()

    return model, tokenizer, spec


# ---------------------------------------------------------------------------
# 6. Core Processing Logic
# ---------------------------------------------------------------------------
def process_document(
        csv_path: Path,
        model: AutoModelForCausalLM,
        tokenizer: AutoTokenizer,
        prefix_function,
        system_prompt: str,
        max_input_tokens: int,
        logger: ParadataLogger
) -> List[dict]:
    file_id = csv_path.stem
    enriched_document_lines = []

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:
            try:
                # Support both column name conventions across pipeline versions
                page_num = int(row.get("page_num", row.get("page", 0)))
                line_num = int(row.get("line_num", row.get("line", 0)))
                text_chunk = row.get("text", "").strip()
            except ValueError:
                logger.log_skip(f"{file_id}:line_unknown", "Invalid integer conversion for page/line.")
                continue

            if not text_chunk:
                continue

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text_chunk}
            ]

            prompt = tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )

            try:
                inputs = tokenizer(
                    prompt,
                    return_tensors="pt",
                    truncation=True,
                    max_length=max_input_tokens,
                ).to(model.device)

                output = model.generate(
                    **inputs,
                    max_new_tokens=MAX_NEW_TOKENS,
                    do_sample=False,
                    temperature=None,   # must be None with do_sample=False
                    top_p=None,         # override model's baked-in defaults
                    top_k=None,
                    prefix_allowed_tokens_fn=prefix_function,
                )

                generated_tokens = output[0][inputs["input_ids"].shape[1]:]
                result_json = tokenizer.decode(generated_tokens, skip_special_tokens=True)

                semantic_data = SemanticEnrichment.model_validate_json(result_json)

                enriched_document_lines.append({
                    "file_id": file_id,
                    "page": page_num,
                    "line": line_num,
                    "original_text": text_chunk,
                    "enrichment": semantic_data.model_dump(),
                })

            except Exception as e:
                print(f"[{file_id}] Inference error P{page_num} L{line_num}: {e}")
                logger.log_skip(
                    f"{file_id}:P{page_num}_L{line_num}",
                    f"Inference/validation failed: {str(e)}"
                )

    return enriched_document_lines


# ---------------------------------------------------------------------------
# 7. Pipeline Execution
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import os

    config = load_config("llm_config.txt")

    # MODEL_KEY references the MODEL_REGISTRY, not a raw HF path
    # e.g.: MODEL_KEY=qwen2.5-14b-awq  or  MODEL_KEY=mistral-nemo-12b
    MODEL_KEY    = config.get("MODEL_KEY", "qwen2.5-14b-awq")
    HF_TOKEN     = config.get("HF_TOKEN", os.environ.get("HF_TOKEN", None))
    INPUT_DIR    = Path(config.get("INPUT_DIR",   "data_samples/DOC_LINE_LANG_CLASS"))
    OUTPUT_DIR   = Path(config.get("OUTPUT_DIR",  "data_samples/KW_PER_DOC"))
    VOCAB_PATH   = config.get("VOCAB_PATH",  "data_samples/teater_nested_vocab.json")
    PARADATA_DIR = config.get("PARADATA_DIR", "paradata")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    logger = ParadataLogger(
        program="nlp-enrich-hf",
        config=config,
        paradata_dir=PARADATA_DIR,
        output_types=["json"]
    )

    # Load vocabulary
    vocab_mgr = VocabularyManager(vocab_path=VOCAB_PATH)
    vocab_str = vocab_mgr.get_prompt_string()

    # Load model
    model, tokenizer, spec = load_model_and_tokenizer(MODEL_KEY, HF_TOKEN)
    max_input_tokens = spec["context_window"] - CONTEXT_RESERVED

    # Build (possibly truncated) system prompt
    system_prompt = build_system_prompt(vocab_str, tokenizer, max_input_tokens)

    # Compile JSON schema state machine
    print("=== Compiling JSON Schema State Machine ===")
    parser = JsonSchemaParser(SemanticEnrichment.model_json_schema())
    prefix_function = build_transformers_prefix_allowed_tokens_fn(tokenizer, parser)

    print(f"=== Model ready. Context budget: {max_input_tokens} input tokens ===")

    csv_files = list(INPUT_DIR.glob("*.csv"))
    total_inputs = len(csv_files)

    for csv_file in csv_files:
        print(f"Processing: {csv_file.name}...")
        try:
            enriched_results = process_document(
                csv_file, model, tokenizer, prefix_function,
                system_prompt, max_input_tokens, logger
            )

            if enriched_results:
                out_file = OUTPUT_DIR / f"{csv_file.stem}_enriched.json"
                with open(out_file, "w", encoding="utf-8") as out_f:
                    json.dump(enriched_results, out_f, indent=4, ensure_ascii=False)
                print(f" -> Saved {len(enriched_results)} lines to {out_file.name}")
                logger.log_success("json", count=1)
                logger.log_document_success()
            else:
                logger.log_skip(csv_file.name, "No valid text lines extracted.")

        except Exception as e:
            print(f"Critical error on {csv_file.name}: {e}")
            logger.log_skip(csv_file.name, str(e))

    paradata_path = logger.finalize(input_total=total_inputs)
    print(f"\nPipeline complete. Paradata: {paradata_path}")