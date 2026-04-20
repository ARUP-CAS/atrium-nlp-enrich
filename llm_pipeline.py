import csv
import json
import enum
import os
from pathlib import Path
from typing import List, Dict, Optional, Tuple
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
        "hf_token_required": True,
    },
}

MAX_NEW_TOKENS = 256
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
# 3. Dynamic Schema Definition
# ---------------------------------------------------------------------------
def build_schema(term_names: List[str]) -> type:
    """
    Build Pydantic model with enum constrained to the exact terms
    that appear in the prompt.
    """
    if not term_names:
        raise ValueError("term_names is empty — vocabulary failed to load or was fully truncated.")

    TermEnum = enum.Enum(
        "TermEnum",
        {f"term_{i}": name for i, name in enumerate(term_names)}
    )

    class ConstrainedEnrichment(BaseModel):
        extracted_keywords_cs: List[str] = Field(
            ..., description="Key Czech terms found in the text."
        )
        extracted_keywords_en: List[str] = Field(
            ..., description="English translations of the Czech keywords."
        )
        teater_category: TermEnum = Field(
            ..., description="The single most relevant TEATER term from the vocabulary."
        )
        confidence_score: float = Field(
            ..., ge=0.0, le=1.0,
            description="How confident you are in this categorization."
        )

        def category_name(self) -> str:
            return self.teater_category.value

    return ConstrainedEnrichment


# ---------------------------------------------------------------------------
# 4. Vocabulary truncation for tight context windows
# ---------------------------------------------------------------------------
def build_system_prompt(vocab_data: dict, tokenizer, max_tokens: int) -> Tuple[str, List[str]]:
    """
    Build system prompt with a flat term list instead of raw JSON.
    Returns (prompt_string, all_term_names) for schema building.
    """
    all_terms = []
    for broad_key, terms in vocab_data.items():
        if isinstance(terms, dict):
            for cs_key, pair in terms.items():
                en = pair.get("en", cs_key) if isinstance(pair, dict) else cs_key
                all_terms.append((cs_key, en))

    term_lines = [f"{cs} ({en})" for cs, en in all_terms]

    header = (
        "You are an expert archaeological data extractor. "
        "Analyze the input text and select the SINGLE most relevant category "
        "from the permitted vocabulary list below. "
        "You MUST use the exact Czech term as written.\n\n"
        "PERMITTED VOCABULARY TERMS (Czech | English):\n"
    )

    full_term_block = "\n".join(f"- {line}" for line in term_lines)
    full_prompt = header + full_term_block

    token_count = len(tokenizer.encode(full_prompt))
    print(f"[vocab] {len(all_terms)} terms, {token_count} tokens")

    if token_count <= max_tokens:
        print("[vocab] Full vocabulary fits in context window — no truncation needed.")
        return full_prompt, [t[0] for t in all_terms]

    print(f"[WARN] Vocabulary ({token_count} tokens) exceeds budget ({max_tokens}). "
          f"Truncating term list.")

    lo, hi = 0, len(term_lines)
    while lo < hi - 1:
        mid = (lo + hi) // 2
        candidate = header + "\n".join(f"- {line}" for line in term_lines[:mid])
        if len(tokenizer.encode(candidate)) <= max_tokens:
            lo = mid
        else:
            hi = mid

    truncated_terms = term_lines[:lo]
    truncated_prompt = header + "\n".join(f"- {line}" for line in truncated_terms)
    surviving_cs = [all_terms[i][0] for i in range(lo)]
    print(f"[WARN] Only {lo}/{len(all_terms)} terms fit. "
          f"Terms from position {lo} onward will not be selectable.")
    return truncated_prompt, surviving_cs


# ---------------------------------------------------------------------------
# 5. Model + Tokenizer Loader
# ---------------------------------------------------------------------------
def load_model_and_tokenizer(model_key: str, hf_token: Optional[str] = None):
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
        EnrichmentModel: type,
        max_input_tokens: int,
        logger: ParadataLogger
) -> List[dict]:
    file_id = csv_path.stem
    enriched_document_lines = []

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:
            try:
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
                    temperature=None,
                    top_p=None,
                    top_k=None,
                    prefix_allowed_tokens_fn=prefix_function,
                )

                generated_tokens = output[0][inputs["input_ids"].shape[1]:]
                result_json = tokenizer.decode(generated_tokens, skip_special_tokens=True)

                semantic_data = EnrichmentModel.model_validate_json(result_json)

                # Dump with resolved enum values
                dump_data = semantic_data.model_dump()
                dump_data['teater_category'] = semantic_data.category_name()

                enriched_document_lines.append({
                    "file_id": file_id,
                    "page": page_num,
                    "line": line_num,
                    "original_text": text_chunk,
                    "enrichment": dump_data,
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
    config = load_config("llm_config.txt")
    MODEL_KEY = config.get("MODEL_KEY", "qwen2.5-14b-awq")
    HF_TOKEN = config.get("HF_TOKEN", os.environ.get("HF_TOKEN", None))
    INPUT_DIR = Path(config.get("INPUT_DIR", "data_samples/DOC_LINE_LANG_CLASS"))
    OUTPUT_DIR = Path(config.get("OUTPUT_DIR", "data_samples/KW_PER_DOC_LLM"))
    VOCAB_PATH = config.get("VOCAB_PATH", "data_samples/teater_nested_vocab.json")
    PARADATA_DIR = config.get("PARADATA_DIR", "paradata")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    logger = ParadataLogger(
        program="nlp-enrich-hf",
        config=config,
        paradata_dir=PARADATA_DIR,
        output_types=["json"]
    )

    # 1. Load vocab and fail loudly if empty
    vocab_mgr = VocabularyManager(vocab_path=VOCAB_PATH)
    vocab_data = vocab_mgr.load()
    total_terms = sum(
        len(v) for v in vocab_data.values() if isinstance(v, dict)
    )
    if total_terms == 0:
        raise RuntimeError(
            f"Vocabulary at {VOCAB_PATH} is empty. "
            "Run vocab_manager.py on a node with internet access first."
        )
    print(f"=== Vocabulary: {total_terms} terms in {len(vocab_data)} broad categories ===")

    # 2. Load model
    model, tokenizer, spec = load_model_and_tokenizer(MODEL_KEY, HF_TOKEN)
    max_input_tokens = spec["context_window"] - CONTEXT_RESERVED

    # 3. Build prompt and get surviving term list
    system_prompt, surviving_terms = build_system_prompt(
        vocab_data, tokenizer, max_input_tokens
    )
    print(f"=== System prompt: {len(tokenizer.encode(system_prompt))} tokens, "
          f"{len(surviving_terms)}/{total_terms} terms survive ===")

    # 4. Build schema constrained to exactly the surviving terms
    EnrichmentModel = build_schema(surviving_terms)

    # 5. Compile constrained decoder
    print("=== Compiling JSON Schema State Machine ===")
    parser = JsonSchemaParser(EnrichmentModel.model_json_schema())
    prefix_function = build_transformers_prefix_allowed_tokens_fn(tokenizer, parser)

    print("=== Pipeline ready ===")

    csv_files = list(INPUT_DIR.glob("*.csv"))
    for csv_file in csv_files:
        print(f"Processing: {csv_file.name}...")
        try:
            enriched_results = process_document(
                csv_file, model, tokenizer, prefix_function,
                system_prompt, EnrichmentModel, max_input_tokens, logger
            )
            if enriched_results:
                out_file = OUTPUT_DIR / f"{csv_file.stem}_enriched.json"
                with open(out_file, "w", encoding="utf-8") as out_f:
                    json.dump(enriched_results, out_f, indent=4, ensure_ascii=False)
                print(f" -> {len(enriched_results)} lines to {out_file.name}")
                logger.log_success("json", count=1)
                logger.log_document_success()
            else:
                logger.log_skip(csv_file.name, "No meaningful lines after filtering.")
        except Exception as e:
            print(f"Critical error on {csv_file.name}: {e}")
            logger.log_skip(csv_file.name, str(e))

    logger.finalize(input_total=len(csv_files))