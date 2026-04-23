"""
llm_pipeline.py — LLM Semantic Enrichment Pipeline for ATRIUM archaeological documents.

Input:  CSV files produced by the ALTO postprocessing pipeline.
Output: Per-document JSON files, one record per processed line, with enriched semantic
        categories mapped to the TEATER/AMCR vocabulary.
"""

import csv
import json
import enum
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import torch

from transformers import AutoModelForCausalLM, AutoTokenizer
from pydantic import BaseModel, Field, ValidationError
from lmformatenforcer import JsonSchemaParser
from lmformatenforcer.integrations.transformers import build_transformers_prefix_allowed_tokens_fn

from atrium_paradata import ParadataLogger
from vocab_manager import VocabularyManager


# ---------------------------------------------------------------------------
# 1. Model Registry
# ---------------------------------------------------------------------------

MODEL_REGISTRY: Dict[str, Dict] = {
    # --- New Qwen 3 Models ---
    "qwen3-14b": {
        "hf_id": "Qwen/Qwen3-14B-Instruct",
        "context_window": 32768,
        "trust_remote_code": False,
        "torch_dtype": torch.bfloat16,
        "hf_token_required": False,
    },
    "qwen3-8b": {
        "hf_id": "Qwen/Qwen3-8B-Instruct",
        "context_window": 32768,
        "trust_remote_code": False,
        "torch_dtype": torch.bfloat16,
        "hf_token_required": False,
    },
    # --- Existing Registry ---
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

MAX_NEW_TOKENS = 2048
CONTEXT_RESERVED = MAX_NEW_TOKENS + 512
_ALWAYS_SKIP_CATEG = {"Empty", "Trash"}


# ---------------------------------------------------------------------------
# 2. Configuration Loader
# ---------------------------------------------------------------------------
def load_config(config_path: str = "llm_config.txt") -> Dict[str, str]:
    config: Dict[str, str] = {}
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                config[key.strip()] = value.strip()
    return config


# ---------------------------------------------------------------------------
# 3. Line Quality Filter
# ---------------------------------------------------------------------------
def _should_process_line(
    text: str, categ: str, include_non_text: bool, min_char_count: int,
    min_char_non_text: int, min_alpha_ratio_non_text: float
) -> Tuple[bool, str]:
    if not text:
        return False, "empty text"

    if categ in _ALWAYS_SKIP_CATEG:
        return False, f"categ={categ}"

    if categ == "Non-text":
        if not include_non_text:
            return False, "Non-text excluded by config"
        char_count = len(text)
        if char_count < min_char_non_text:
            return False, f"Non-text too short ({char_count} < {min_char_non_text} chars)"
        alpha_count = sum(c.isalpha() for c in text)
        alpha_ratio = alpha_count / char_count if char_count else 0.0
        if alpha_ratio < min_alpha_ratio_non_text:
            return False, f"Non-text alpha ratio too low ({alpha_ratio:.2f})"
        return True, ""

    if len(text) < min_char_count:
        return False, f"text too short ({len(text)} < {min_char_count} chars)"
    return True, ""


# ---------------------------------------------------------------------------
# 4. Dynamic Schema Definition
# ---------------------------------------------------------------------------
def build_schema(term_names: List[str]) -> type:
    if not term_names:
        raise ValueError("term_names is empty — vocabulary failed to load or was fully truncated.")

    TermEnum = enum.Enum("TermEnum", {f"term_{i}": name for i, name in enumerate(term_names)})

    class ConstrainedEnrichment(BaseModel):
        extracted_keywords_cs: List[str] = Field(
            ...,
            description=(
                "Key Czech archaeological terms found ONLY in the marked line (>>>). "
                "Do not copy from surrounding context lines."
            ),
        )
        extracted_keywords_en: List[str] = Field(
            ...,
            description=(
                "Accurate English translations of extracted_keywords_cs. "
                "Do not copy the Czech words unchanged."
            ),
        )
        teater_category: TermEnum = Field(
            ...,
            description=(
                "The single most relevant category from the thematic vocabulary. "
                "Use 'Nerelevantní (meta-text)' for headers, footers, table of contents, "
                "or any line lacking direct archaeological significance."
            ),
        )
        confidence_score: float = Field(
            ..., ge=0.0, le=1.0,
            description="Confidence in this categorization (0.0 to 1.0).",
        )

        def category_name(self) -> str:
            return self.teater_category.value

    return ConstrainedEnrichment


# ---------------------------------------------------------------------------
# 5. Vocabulary Helpers
# ---------------------------------------------------------------------------
def _term_priority(cs: str, en: str) -> int:
    if en.rstrip().endswith("(the)"):
        return 2

    # Protect against empty strings to avoid IndexError
    if cs == en and cs and cs[0].isupper() and "/" not in cs:
        return 2

    cs_words = cs.split()
    if (len(cs_words) >= 2 and all(w[0].isupper() for w in cs_words if w)
        and "/" not in cs and not any(c.isdigit() for c in cs)):

        _arch_keywords = {
            "kultura", "doba", "období", "eneolit", "paleolit", "neolit",
            "středověk", "novověk", "pravěk", "mezolit", "bronzová",
            "laténská", "halštatská", "stěhování",
        }
        _arch_prefixes = ("Creative", "HaA", "HaB", "HaC", "HaD")

        if not cs.startswith(_arch_prefixes) and not any(kw in cs.lower() for kw in _arch_keywords):
            en_words = en.split()
            _stop = {"a", "an", "the", "of", "and", "in"}
            if len(en_words) >= 2 and all(w[0].isupper() for w in en_words if w.lower() not in _stop):
                return 1

    return 0


# ---------------------------------------------------------------------------
# 6. Thematic System Prompt Builder
# ---------------------------------------------------------------------------
def build_system_prompt(vocab_data: dict, tokenizer, max_tokens: int) -> Tuple[str, List[str]]:
    """
    Builds a system prompt structured by themes to improve LLM contextual mapping.
    Includes an explicit meta-text escape hatch at position 0 (so it always survives
    token-budget truncation) and caps the noisy 'Other' section at 15 terms to prevent
    country-name / generic-term hallucination.
    """
    header = (
        "You are an expert archaeological data extractor. "
        "Analyze the MARKED LINE (>>>) within its surrounding document context. "
        "Extract keywords specifically from the marked line. "
        "Select the SINGLE most relevant category from the thematic vocabulary list below.\n"
        "CRITICAL: If the marked line is a title, table of contents, administrative "
        "meta-text, running header, page number, or lacks direct archaeological "
        "significance, you MUST select 'Nerelevantní (meta-text)'.\n"
        "You MUST use the exact Czech term as written.\n"
        "You MUST respond ONLY with a valid JSON object matching the requested schema.\n\n"
        "THEMATIC VOCABULARY:\n"
    )

    raw_terms: List[dict] = []

    # Position 0: explicit meta-text fallback — always survives token-budget truncation
    raw_terms.append({
        "theme": "Administrative / Meta",
        "cs": "Nerelevantní (meta-text)",
        "en": "Irrelevant / Meta-text",
    })

    for theme, terms in vocab_data.items():
        if isinstance(terms, dict):
            for cs_key, pair in terms.items():
                en = pair.get("en", cs_key) if isinstance(pair, dict) else cs_key
                raw_terms.append({"theme": theme, "cs": cs_key, "en": en})

    # Sort by priority: lower _term_priority value = higher relevance = listed first.
    # The meta-text term at index 0 is deliberately kept at position 0 (priority=0 → listed first).
    prioritised = [raw_terms[0]] + sorted(raw_terms[1:], key=lambda t: _term_priority(t["cs"], t["en"]))

    def build_candidate_prompt(term_list, other_cap: int = 15):
        themes: Dict[str, List[str]] = {}
        other_terms: List[dict] = []

        for t in term_list:
            if t["theme"] == "Other":
                other_terms.append(t)
            else:
                themes.setdefault(t["theme"], []).append(f"{t['cs']} ({t['en']})")

        prompt = header
        for theme_name, lines in themes.items():
            prompt += f"\n--- {theme_name} ---\n"
            prompt += "\n".join(f"- {line}" for line in lines) + "\n"

        if other_terms:
            prompt += "\n--- Other (Misc) ---\n"
            prompt += "\n".join(
                f"- {t['cs']} ({t['en']})" for t in other_terms[:other_cap]
            ) + "\n"

        return prompt

    full_prompt = build_candidate_prompt(prioritised)
    token_count = len(tokenizer.encode(full_prompt))
    print(f"[vocab] {len(prioritised)} terms, {token_count} tokens (grouped by theme)")

    if token_count <= max_tokens:
        print("[vocab] Full vocabulary fits in context window — no truncation needed.")
        return full_prompt, [t["cs"] for t in prioritised]

    print(f"[WARN] Vocabulary ({token_count} tokens) exceeds budget ({max_tokens}). Truncating.")

    lo, hi = 0, len(prioritised)
    while lo < hi - 1:
        mid = (lo + hi) // 2
        candidate = build_candidate_prompt(prioritised[:mid])
        if len(tokenizer.encode(candidate)) <= max_tokens:
            lo = mid
        else:
            hi = mid

    surviving_terms = prioritised[:lo]
    surviving_prompt = build_candidate_prompt(surviving_terms)
    surviving_cs = [t["cs"] for t in surviving_terms]
    return surviving_prompt, surviving_cs


# ---------------------------------------------------------------------------
# 7. Model + Tokenizer Loader
# ---------------------------------------------------------------------------
def load_model_and_tokenizer(model_key: str, hf_token: Optional[str] = None):
    if model_key not in MODEL_REGISTRY:
        raise ValueError(f"Unknown MODEL_KEY '{model_key}'. Available: {', '.join(MODEL_REGISTRY.keys())}")

    spec = MODEL_REGISTRY[model_key]
    hf_id = spec["hf_id"]

    if spec["hf_token_required"] and not hf_token:
        raise EnvironmentError("Model requires a HuggingFace token. Set HF_TOKEN in config.")

    print(f"=== Loading: {hf_id} ===")
    tokenizer = AutoTokenizer.from_pretrained(hf_id, trust_remote_code=spec["trust_remote_code"], token=hf_token or None)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        hf_id,
        device_map="auto",
        torch_dtype=spec["torch_dtype"],
        trust_remote_code=spec["trust_remote_code"],
        token=hf_token or None,
        attn_implementation="flash_attention_2"
    )
    model.eval()
    return model, tokenizer, spec


# ---------------------------------------------------------------------------
# 8. Utilities
# ---------------------------------------------------------------------------
def get_context_window(rows: List[dict], center_idx: int, window: int = 2) -> str:
    """
    Returns a sliding context window of up to `window` lines before and after
    center_idx, marked with '>>>'. Page boundaries are respected: context lines
    from a different page than the center line are excluded.

    For lines that are far from the document start (beyond window + 2 positions),
    prepends a GLOBAL DOCUMENT HEADER containing the first 2 non-noise lines of
    the document. This anchors the LLM when processing mid-document lines that
    would otherwise have no indication of the document's domain or purpose.
    """
    _NOISE_CATEG = {"Empty", "Trash", "Non-text"}

    center_row  = rows[center_idx]
    center_page = center_row.get("page_num", center_row.get("page", None))

    start = max(0, center_idx - window)
    end   = min(len(rows), center_idx + window + 1)

    parts = []

    # --- Global Document Header ---
    if center_idx > window + 2:
        parts.append("--- GLOBAL DOCUMENT HEADER ---")
        header_lines_added = 0
        for row in rows:
            if row.get("categ", "").strip() not in _NOISE_CATEG:
                pg = row.get("page_num", row.get("page", 0))
                ln = row.get("line_num",  row.get("line", 0))
                parts.append(f"    [P{pg} L{ln}] {row.get('text', '').strip()}")
                header_lines_added += 1
                if header_lines_added >= 2:
                    break
        parts.append("--- LOCAL CONTEXT WINDOW ---")
    # --------------------------------

    for i in range(start, end):
        row      = rows[i]
        row_page = row.get("page_num", row.get("page", None))
        categ    = row.get("categ", "").strip()

        # Exclude context lines from a different page (but always include the target)
        if row_page != center_page and i != center_idx:
            continue

        # Exclude noise-category context lines (but always include the target)
        if i != center_idx and categ in _NOISE_CATEG:
            continue

        marker = ">>>" if i == center_idx else "   "
        text   = row.get("text", "").strip()
        parts.append(f"{marker} [P{row_page} L{row.get('line_num', row.get('line', 0))}] {text}")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 9. Core Processing Logic
# ---------------------------------------------------------------------------
def process_document(
    csv_path: Path, model: AutoModelForCausalLM, tokenizer: AutoTokenizer,
    prefix_function, system_prompt: str, EnrichmentModel: type, max_input_tokens: int,
    include_non_text: bool = True, min_char_count: int = 3, min_char_non_text: int = 8,
    min_alpha_ratio_non_text: float = 0.40,
) -> Tuple[List[dict], Dict[str, int]]:

    file_id = csv_path.stem
    enriched_lines: List[dict] = []
    stats: Dict[str, int] = {"processed": 0, "skipped_filter": 0, "skipped_error": 0}

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    for i, row in enumerate(rows):
        try:
            page_num = int(row.get("page_num", row.get("page", 0)))
            line_num = int(row.get("line_num", row.get("line", 0)))
        except (ValueError, TypeError):
            print(f"  [{file_id}] Bad page/line value in row — skipping.")
            stats["skipped_filter"] += 1
            continue

        text_chunk = row.get("text", "").strip()
        categ      = row.get("categ", "").strip()

        should_process, skip_reason = _should_process_line(
            text_chunk, categ, include_non_text, min_char_count, min_char_non_text, min_alpha_ratio_non_text
        )
        if not should_process:
            stats["skipped_filter"] += 1
            continue

        context_chunk = get_context_window(rows, i, window=2)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"DOCUMENT CONTEXT:\n{context_chunk}\n\nTask: Extract keywords and determine the TEATER category ONLY for the line marked with '>>>'."},
        ]
        prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)

        try:
            inputs = tokenizer(prompt, return_tensors="pt", truncation=True, max_length=max_input_tokens).to(model.device)

            with torch.no_grad():
                output = model.generate(
                    **inputs,
                    max_new_tokens=MAX_NEW_TOKENS,
                    do_sample=False,
                    temperature=None,
                    top_p=None,
                    top_k=None,
                    pad_token_id=tokenizer.pad_token_id,
                    eos_token_id=tokenizer.eos_token_id,
                    prefix_allowed_tokens_fn=prefix_function,
                )

            generated_tokens = output[0][inputs["input_ids"].shape[1]:]
            result_json = tokenizer.decode(generated_tokens, skip_special_tokens=True)

            try:
                semantic_data = EnrichmentModel.model_validate_json(result_json)
            except ValidationError:
                try:
                    # Fallback: Safely parse literal unescaped control chars (e.g., \t)
                    raw_dict = json.loads(result_json, strict=False)

                    # Safely clamp the confidence score in dictionary space
                    if "confidence_score" in raw_dict:
                        try:
                            val = float(raw_dict["confidence_score"])
                            raw_dict["confidence_score"] = min(1.0, max(0.0, val))
                        except (ValueError, TypeError):
                            pass
                    semantic_data = EnrichmentModel.model_validate(raw_dict)
                except (json.JSONDecodeError, ValidationError) as e:
                    print(f"  [{file_id}] Persistent validation error P{page_num} L{line_num}: {e}")
                    stats["skipped_error"] += 1
                    continue

            dump_data = semantic_data.model_dump()
            dump_data["teater_category"] = semantic_data.category_name()

            enriched_lines.append({
                "file_id": file_id,
                "page": page_num,
                "line": line_num,
                "categ": categ,
                "quality_score": float(row.get("quality_score") or 0.0),
                "original_text": text_chunk,
                "enrichment": dump_data,
            })
            stats["processed"] += 1

        except Exception as e:
            print(f"  [{file_id}] Inference error P{page_num} L{line_num}: {e}")
            stats["skipped_error"] += 1

    return enriched_lines, stats


# ---------------------------------------------------------------------------
# 10. Pipeline Execution
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    config = load_config("llm_config.txt")

    MODEL_KEY    = config.get("MODEL_KEY",    "qwen2.5-14b-awq")
    HF_TOKEN     = config.get("HF_TOKEN",     os.environ.get("HF_TOKEN", None))
    INPUT_DIR    = Path(config.get("INPUT_DIR",  "data_samples/DOC_LINE_LANG_CLASS"))
    VOCAB_PATH   = config.get("VOCAB_PATH",   "data_samples/teater_nested_vocab.json")
    PARADATA_DIR = config.get("PARADATA_DIR", "paradata")

    _base_out     = Path(config.get("OUTPUT_DIR", "data_samples/KW_PER_DOC_LLM"))
    _model_suffix = MODEL_KEY.replace(".", "").replace("-", "_")
    OUTPUT_DIR    = _base_out.parent / f"{_base_out.name}_{_model_suffix}"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"=== Output directory: {OUTPUT_DIR} ===")

    INCLUDE_NON_TEXT         = config.get("INCLUDE_NON_TEXT", "true").lower() == "true"
    MIN_CHAR_COUNT           = int(config.get("MIN_CHAR_COUNT", "3"))
    MIN_CHAR_NON_TEXT        = int(config.get("MIN_CHAR_NON_TEXT", "8"))
    MIN_ALPHA_RATIO_NON_TEXT = float(config.get("MIN_ALPHA_RATIO_NON_TEXT", "0.40"))

    logger = ParadataLogger(
        program="nlp-enrich",
        config={
            **config,
            "output_dir_resolved": str(OUTPUT_DIR),
            "include_non_text": INCLUDE_NON_TEXT,
            "min_char_count": MIN_CHAR_COUNT,
            "min_char_non_text": MIN_CHAR_NON_TEXT,
            "min_alpha_ratio_non_text": MIN_ALPHA_RATIO_NON_TEXT,
        },
        paradata_dir=PARADATA_DIR,
        output_types=["json"],
    )

    vocab_mgr = VocabularyManager(vocab_path=VOCAB_PATH)
    vocab_data = vocab_mgr.load()
    total_terms = sum(len(v) for v in vocab_data.values() if isinstance(v, dict))
    if total_terms == 0:
        raise RuntimeError("Vocabulary is empty. Run vocab_manager.py on a node with internet access first.")
    print(f"=== Vocabulary: {total_terms} terms in {len(vocab_data)} broad categories ===")

    model, tokenizer, spec = load_model_and_tokenizer(MODEL_KEY, HF_TOKEN)
    max_input_tokens = spec["context_window"] - CONTEXT_RESERVED

    system_prompt, surviving_terms = build_system_prompt(vocab_data, tokenizer, max_input_tokens)
    EnrichmentModel = build_schema(surviving_terms)

    print("=== Compiling JSON Schema State Machine ===")
    parser = JsonSchemaParser(EnrichmentModel.model_json_schema())
    prefix_function = build_transformers_prefix_allowed_tokens_fn(tokenizer, parser)
    print("=== Pipeline ready ===")

    csv_files = sorted(INPUT_DIR.glob("*.csv"))
    total_processed = 0
    total_errors    = 0

    for csv_file in csv_files:
        out_file = OUTPUT_DIR / f"{csv_file.stem}_enriched.json"

        if out_file.exists():
            print(f"[skip] {csv_file.name} — output already exists.")
            logger.log_skip(csv_file.name, "already_exists")
            continue

        print(f"Processing: {csv_file.name} ...")
        try:
            enriched_results, doc_stats = process_document(
                csv_path=csv_file, model=model, tokenizer=tokenizer, prefix_function=prefix_function,
                system_prompt=system_prompt, EnrichmentModel=EnrichmentModel, max_input_tokens=max_input_tokens,
                include_non_text=INCLUDE_NON_TEXT, min_char_count=MIN_CHAR_COUNT, min_char_non_text=MIN_CHAR_NON_TEXT,
                min_alpha_ratio_non_text=MIN_ALPHA_RATIO_NON_TEXT,
            )

            total_processed += doc_stats["processed"]
            total_errors    += doc_stats["skipped_error"]
            print(f"  processed={doc_stats['processed']}, skipped_filter={doc_stats['skipped_filter']}, errors={doc_stats['skipped_error']}")

            if enriched_results:
                with open(out_file, "w", encoding="utf-8") as out_f:
                    json.dump(enriched_results, out_f, indent=4, ensure_ascii=False)
                print(f"  -> {len(enriched_results)} records → {out_file.name}")
                logger.log_success("json", count=1)
                logger.log_document_success()
            else:
                logger.log_skip(csv_file.name, f"No lines passed quality filter or all inference calls failed (stats={doc_stats}).")

        except Exception as e:
            print(f"Critical error on {csv_file.name}: {e}")
            logger.log_skip(csv_file.name, str(e))

        finally:
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    already_done = sum(1 for s in logger._skipped if s.get("reason") == "already_exists")
    true_failures = sum(1 for s in logger._skipped if s.get("reason") != "already_exists")
    print(f"\n=== Paradata note: {already_done} files skipped (already done), {true_failures} files skipped (errors) ===")

    print(f"=== Run complete: {total_processed} lines enriched, {total_errors} inference errors across {len(csv_files)} files ===")
    logger.finalize(input_total=len(csv_files))