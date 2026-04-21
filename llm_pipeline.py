"""
llm_pipeline.py — LLM Semantic Enrichment Pipeline for ATRIUM archaeological documents.

Input:  CSV files produced by the ALTO postprocessing pipeline.
        Each row contains one OCR text line together with quality metadata columns:
            categ         — upstream quality label: Clear | Noisy | Non-text | Trash | Empty
            quality_score — 0.0–1.0 numeric quality estimate
            char_count    — character count (may be 0 for Non-text rows even when text exists)
            garbage_density, gibberish, … — other signal columns (available for future use)

Output: Per-document JSON files, one record per processed line, with enriched semantic
        categories mapped to the TEATER/AMCR vocabulary.

Changes from v1
───────────────
  1. categ-aware line filter  — Clear/Noisy always processed; Trash/Empty always skipped;
                                Non-text processed when INCLUDE_NON_TEXT=true (default) and
                                the line passes stricter char-count + alpha-ratio checks.
  2. torch.no_grad()          — Wraps every model.generate() call to prevent gradient
                                accumulation and GPU memory growth across a long document run.
  3. Resume logic             — Output file is checked before processing; already-finished
                                documents are skipped with a log message.
  4. CUDA cache clearing      — torch.cuda.empty_cache() called after each document in a
                                `finally` block to prevent memory fragmentation.
  5. Model-keyed output dir   — OUTPUT_DIR is auto-suffixed with the model key
                                (e.g. KW_PER_DOC_LLM_qwen25_14b_awq) so that runs with
                                different models never overwrite each other.
  6. Smart vocab truncation   — _term_priority() sorts the vocabulary before binary-search
                                truncation so that ISO country names and airport/city codes
                                are dropped first when the context window is tight, keeping
                                core archaeological terms intact as long as possible.
  7. Source quality in output — Each output record includes the upstream `categ` and
                                `quality_score` values so downstream consumers can filter
                                by confidence.
  8. Per-document stats       — process_document() returns a stats dict
                                (processed / skipped_filter / skipped_error) for
                                informative console output.

New llm_config.txt keys (all optional — defaults shown):
─────────────────────────────────────────────────────────
  INCLUDE_NON_TEXT=true         Process lines labelled Non-text (date stamps, form labels…)
  MIN_CHAR_COUNT=3              Min characters for Clear/Noisy lines
  MIN_CHAR_NON_TEXT=8           Min characters for Non-text lines (stricter)
  MIN_ALPHA_RATIO_NON_TEXT=0.40 Min fraction of alphabetic chars in a Non-text line
"""

import csv
import json
import enum
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple
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

# Increased to 512 to prevent truncated JSON on lines with long keyword lists.
MAX_NEW_TOKENS = 1024
CONTEXT_RESERVED = MAX_NEW_TOKENS + 256

# Categories that are unconditionally skipped — no config override possible.
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
    text: str,
    categ: str,
    include_non_text: bool,
    min_char_count: int,
    min_char_non_text: int,
    min_alpha_ratio_non_text: float,
) -> Tuple[bool, str]:
    """
    Decide whether a CSV row should be sent to the LLM.

    Returns (process: bool, reason: str).
    `reason` is non-empty only when the line is skipped and is used solely
    for debug output — it is never written to the output JSON.

    Filtering logic
    ───────────────
    • Empty text or categ in {Empty, Trash} → always skip.
    • Non-text categ:
        – Skip when INCLUDE_NON_TEXT=false.
        – Skip when the raw text is shorter than min_char_non_text.
        – Skip when the fraction of alphabetic characters in the raw text is
          below min_alpha_ratio_non_text (catches lines like `' *4242& -'` or
          `<• 55 1501 M` that are pure symbols/punctuation).
          NOTE: the `char_count` and `quality_score` CSV columns are unreliable
          for Non-text rows (they are set to 0 by the upstream classifier even
          when the text field contains meaningful content such as a date stamp),
          so the filter operates directly on the raw text string.
    • Clear / Noisy:
        – Skip only when the raw text is shorter than min_char_count.
        – `quality_score` is intentionally NOT used as an additional threshold
          because the `categ` label already encodes the upstream classifier's
          verdict; double-filtering would redundantly discard valid lines.
    """
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
            return False, (
                f"Non-text alpha ratio too low "
                f"({alpha_ratio:.2f} < {min_alpha_ratio_non_text:.2f})"
            )
        return True, ""

    # Clear / Noisy
    if len(text) < min_char_count:
        return False, f"text too short ({len(text)} < {min_char_count} chars)"
    return True, ""


# ---------------------------------------------------------------------------
# 4. Dynamic Schema Definition
# ---------------------------------------------------------------------------
def build_schema(term_names: List[str]) -> type:
    """
    Build a Pydantic model whose `teater_category` field is an enum constrained
    to exactly the terms that appear in the system prompt.

    The enum is rebuilt from the surviving term list every run, so the
    constrained decoder and the prompt vocabulary are always in sync.
    """
    if not term_names:
        raise ValueError(
            "term_names is empty — vocabulary failed to load or was fully truncated."
        )

    TermEnum = enum.Enum(
        "TermEnum",
        {f"term_{i}": name for i, name in enumerate(term_names)},
    )

    class ConstrainedEnrichment(BaseModel):
        extracted_keywords_cs: List[str] = Field(
            ..., description="Key Czech terms found in the text."
        )
        extracted_keywords_en: List[str] = Field(
            ..., description="English translations of the Czech keywords."
        )
        teater_category: TermEnum = Field(
            ...,
            description="The single most relevant TEATER term from the vocabulary.",
        )
        confidence_score: float = Field(
            ..., ge=0.0, le=1.0,
            description="How confident you are in this categorization. MUST be a float between 0.0 and 1.0 (e.g., 0.85, NOT 85)."
        )

        def category_name(self) -> str:
            return self.teater_category.value

    return ConstrainedEnrichment


# ---------------------------------------------------------------------------
# 5. Vocabulary Helpers
# ---------------------------------------------------------------------------

def _term_priority(cs: str, en: str) -> int:
    """
    Assign a truncation priority to a vocabulary term.
    Lower value = higher priority = kept longer when the context window is tight.

      0  Core archaeological terms and concepts              (dropped last)
      1  Czech / Slovak city and region codes used as location labels
      2  ISO country names and airport / suburb codes        (dropped first)

    Rules applied in order:

    Rule A — ISO country names carrying a definite article in English:
        "United States of America (the)", "Netherlands (the)", etc.
        → priority 2

    Rule B — Terms identical in both languages, starting with uppercase, no slash:
        Covers airport codes (Praha-Letňany), Czech-city location codes (Znojmo,
        Kladno), and transliteration-equal country names (Zimbabwe, Palau).
        → priority 2

    Rule C — Multi-word Czech place names where every word is capitalised, no
        slash, no digits, and the English is also fully capitalised — but
        excluding known archaeological period/culture vocabulary:
        "Velká Británie", "Jižní Afrika", "Wallis a Futuna", etc.
        → priority 1

    Rule D — Everything else (lowercase-starting terms, slashed compounds, etc.)
        → priority 0
    """
    # Rule A
    if en.rstrip().endswith("(the)"):
        return 2

    # Rule B
    if cs == en and cs[0].isupper() and "/" not in cs:
        return 2

    # Rule C — multi-word proper-noun geography
    cs_words = cs.split()
    if (
        len(cs_words) >= 2
        and all(w[0].isupper() for w in cs_words if w)
        and "/" not in cs
        and not any(c.isdigit() for c in cs)
    ):
        # Guard: archaeological period/culture names that happen to be capitalised
        _arch_keywords = {
            "kultura", "doba", "období", "eneolit", "paleolit", "neolit",
            "středověk", "novověk", "pravěk", "mezolit", "bronzová",
            "laténská", "halštatská", "stěhování",
        }
        _arch_prefixes = ("Creative", "Ha")  # Creative Commons; HaC / HaD sub-periods

        if not cs.startswith(_arch_prefixes) and not any(
            kw in cs.lower() for kw in _arch_keywords
        ):
            en_words = en.split()
            _stop = {"a", "an", "the", "of", "and", "in"}
            if len(en_words) >= 2 and all(
                w[0].isupper() for w in en_words if w.lower() not in _stop
            ):
                return 1

    return 0


def _prioritised_terms(raw_terms: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """
    Return raw_terms sorted so that core archaeological vocabulary comes first
    and country / location codes come last.

    The sort is stable, so the relative order within each priority group is
    preserved exactly as it appears in teater_nested_vocab.json.
    """
    return sorted(raw_terms, key=lambda t: _term_priority(t[0], t[1]))


# ---------------------------------------------------------------------------
# 6. System Prompt Builder with Smart Truncation
# ---------------------------------------------------------------------------
def build_system_prompt(
    vocab_data: dict, tokenizer, max_tokens: int
) -> Tuple[str, List[str]]:
    """
    Build the system prompt with a flat, priority-ordered vocabulary list.

    The vocabulary is sorted by _term_priority() before the token budget is
    applied, so the binary-search truncation drops country names and city codes
    first rather than the archaeologically relevant terms that appear later in
    the raw vocabulary file.

    Returns:
        prompt_string       — the fully assembled system prompt
        surviving_cs_terms  — list of Czech term strings that appear in the prompt
                              (used to build the constrained-decoding schema)
    """
    raw_terms: List[Tuple[str, str]] = []
    for _broad_key, terms in vocab_data.items():
        if isinstance(terms, dict):
            for cs_key, pair in terms.items():
                en = pair.get("en", cs_key) if isinstance(pair, dict) else cs_key
                raw_terms.append((cs_key, en))

    all_terms = _prioritised_terms(raw_terms)
    term_lines = [f"{cs} ({en})" for cs, en in all_terms]

    header = (
        "You are an expert archaeological data extractor. "
        "Analyze the input text and extract key archaeological terms. "
        "Select the SINGLE most relevant category from the permitted vocabulary list below. "
        "You MUST use the exact Czech term as written.\n"
        "You MUST respond ONLY with a valid JSON object matching the requested schema.\n\n"
        "PERMITTED VOCABULARY TERMS (Czech | English):\n"
    )

    full_prompt = header + "\n".join(f"- {line}" for line in term_lines)
    token_count = len(tokenizer.encode(full_prompt))
    print(
        f"[vocab] {len(all_terms)} terms, {token_count} tokens "
        f"(priority sort applied: country/place codes moved to end)"
    )

    if token_count <= max_tokens:
        print("[vocab] Full vocabulary fits in context window — no truncation needed.")
        return full_prompt, [t[0] for t in all_terms]

    print(
        f"[WARN] Vocabulary ({token_count} tokens) exceeds budget ({max_tokens}). "
        f"Truncating from the end (country codes dropped first)."
    )

    # Binary search for the largest prefix that still fits within max_tokens.
    lo, hi = 0, len(term_lines)
    while lo < hi - 1:
        mid = (lo + hi) // 2
        candidate = header + "\n".join(f"- {line}" for line in term_lines[:mid])
        if len(tokenizer.encode(candidate)) <= max_tokens:
            lo = mid
        else:
            hi = mid

    dropped = all_terms[lo:]
    n_p2 = sum(1 for cs, en in dropped if _term_priority(cs, en) == 2)
    n_p1 = sum(1 for cs, en in dropped if _term_priority(cs, en) == 1)
    n_p0 = sum(1 for cs, en in dropped if _term_priority(cs, en) == 0)
    print(
        f"[WARN] Keeping {lo}/{len(all_terms)} terms. "
        f"Dropped: {n_p2} country/airport codes, {n_p1} place-name codes, "
        f"{n_p0} core terms."
    )
    if n_p0 > 0:
        print(
            "[WARN] Some core archaeological terms were also dropped — "
            "consider a model with a larger context window."
        )

    surviving_prompt = header + "\n".join(f"- {line}" for line in term_lines[:lo])
    surviving_cs = [all_terms[i][0] for i in range(lo)]
    return surviving_prompt, surviving_cs


# ---------------------------------------------------------------------------
# 7. Model + Tokenizer Loader
# ---------------------------------------------------------------------------
def load_model_and_tokenizer(model_key: str, hf_token: Optional[str] = None):
    if model_key not in MODEL_REGISTRY:
        available = ", ".join(MODEL_REGISTRY.keys())
        raise ValueError(
            f"Unknown MODEL_KEY '{model_key}'. Available: {available}"
        )

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
# 8. Core Processing Logic
# ---------------------------------------------------------------------------
def process_document(
    csv_path: Path,
    model: AutoModelForCausalLM,
    tokenizer: AutoTokenizer,
    prefix_function,
    system_prompt: str,
    EnrichmentModel: type,
    max_input_tokens: int,
    include_non_text: bool = True,
    min_char_count: int = 3,
    min_char_non_text: int = 8,
    min_alpha_ratio_non_text: float = 0.40,
) -> Tuple[List[dict], Dict[str, int]]:
    """
    Process a single CSV document.

    Returns:
        enriched_lines  — list of enriched record dicts ready for JSON serialisation
        stats           — {'processed': int, 'skipped_filter': int, 'skipped_error': int}

    Each output record includes the upstream `categ` and `quality_score` values
    so that downstream consumers can filter or weight results by source quality.

    The inference call is wrapped in torch.no_grad() to suppress gradient
    accumulation; without this, GPU memory grows steadily across a long document
    run and eventually causes an out-of-memory crash.
    """
    file_id = csv_path.stem
    enriched_lines: List[dict] = []
    stats: Dict[str, int] = {
        "processed": 0,
        "skipped_filter": 0,
        "skipped_error": 0,
    }

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:
            # --- parse mandatory fields ---
            try:
                page_num = int(row.get("page_num", row.get("page", 0)))
                line_num = int(row.get("line_num", row.get("line", 0)))
            except (ValueError, TypeError):
                print(f"  [{file_id}] Bad page/line value in row — skipping.")
                stats["skipped_filter"] += 1
                continue

            text_chunk = row.get("text", "").strip()
            categ      = row.get("categ", "").strip()

            # --- quality filter (uses categ column + raw text metrics) ---
            should_process, skip_reason = _should_process_line(
                text=text_chunk,
                categ=categ,
                include_non_text=include_non_text,
                min_char_count=min_char_count,
                min_char_non_text=min_char_non_text,
                min_alpha_ratio_non_text=min_alpha_ratio_non_text,
            )
            if not should_process:
                stats["skipped_filter"] += 1
                continue  # silent — reason available via skip_reason if debug needed

            # --- build prompt ---
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": text_chunk},
            ]
            prompt = tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )

            # --- inference ---
            try:
                inputs = tokenizer(
                    prompt,
                    return_tensors="pt",
                    truncation=True,
                    max_length=max_input_tokens,
                ).to(model.device)

                with torch.no_grad():  # prevent gradient accumulation across lines
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
                result_json = tokenizer.decode(
                    generated_tokens, skip_special_tokens=True
                )

                semantic_data = EnrichmentModel.model_validate_json(result_json)

                dump_data = semantic_data.model_dump()
                dump_data["teater_category"] = semantic_data.category_name()

                enriched_lines.append({
                    "file_id":       file_id,
                    "page":          page_num,
                    "line":          line_num,
                    # Preserve upstream quality metadata for downstream filtering.
                    "categ":         categ,
                    "quality_score": float(row.get("quality_score") or 0.0),
                    "original_text": text_chunk,
                    "enrichment":    dump_data,
                })
                stats["processed"] += 1

            except Exception as e:
                print(
                    f"  [{file_id}] Inference error "
                    f"P{page_num} L{line_num}: {e}"
                )
                stats["skipped_error"] += 1

    return enriched_lines, stats


# ---------------------------------------------------------------------------
# 9. Pipeline Execution
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    config = load_config("llm_config.txt")

    MODEL_KEY    = config.get("MODEL_KEY",    "qwen2.5-14b-awq")
    HF_TOKEN     = config.get("HF_TOKEN",     os.environ.get("HF_TOKEN", None))
    INPUT_DIR    = Path(config.get("INPUT_DIR",  "data_samples/DOC_LINE_LANG_CLASS"))
    VOCAB_PATH   = config.get("VOCAB_PATH",   "data_samples/teater_nested_vocab.json")
    PARADATA_DIR = config.get("PARADATA_DIR", "paradata")

    # Output directory: the base path from config is suffixed with a sanitised
    # model-key string so that runs with different models never overwrite each other.
    # e.g.  data_samples/KW_PER_DOC_LLM  →  data_samples/KW_PER_DOC_LLM_qwen25_14b_awq
    _base_out     = Path(config.get("OUTPUT_DIR", "data_samples/KW_PER_DOC_LLM"))
    _model_suffix = MODEL_KEY.replace(".", "").replace("-", "_")
    OUTPUT_DIR    = _base_out.parent / f"{_base_out.name}_{_model_suffix}"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"=== Output directory: {OUTPUT_DIR} ===")

    # --- line-filtering parameters (all optional; documented defaults used if absent) ---
    INCLUDE_NON_TEXT         = config.get("INCLUDE_NON_TEXT",         "true").lower() == "true"
    MIN_CHAR_COUNT           = int(config.get("MIN_CHAR_COUNT",           "3"))
    MIN_CHAR_NON_TEXT        = int(config.get("MIN_CHAR_NON_TEXT",        "8"))
    MIN_ALPHA_RATIO_NON_TEXT = float(config.get("MIN_ALPHA_RATIO_NON_TEXT", "0.40"))

    logger = ParadataLogger(
        program="nlp-enrich",
        config={
            **config,
            "output_dir_resolved":       str(OUTPUT_DIR),
            "include_non_text":          INCLUDE_NON_TEXT,
            "min_char_count":            MIN_CHAR_COUNT,
            "min_char_non_text":         MIN_CHAR_NON_TEXT,
            "min_alpha_ratio_non_text":  MIN_ALPHA_RATIO_NON_TEXT,
        },
        paradata_dir=PARADATA_DIR,
        output_types=["json"],
    )

    # 1. Load and validate vocabulary
    vocab_mgr = VocabularyManager(vocab_path=VOCAB_PATH)
    vocab_data = vocab_mgr.load()
    total_terms = sum(len(v) for v in vocab_data.values() if isinstance(v, dict))
    if total_terms == 0:
        raise RuntimeError(
            f"Vocabulary at {VOCAB_PATH} is empty. "
            "Run vocab_manager.py on a node with internet access first."
        )
    print(f"=== Vocabulary: {total_terms} terms in {len(vocab_data)} broad categories ===")

    # 2. Load model and tokeniser
    model, tokenizer, spec = load_model_and_tokenizer(MODEL_KEY, HF_TOKEN)
    max_input_tokens = spec["context_window"] - CONTEXT_RESERVED

    # 3. Build priority-ordered system prompt; truncate if needed
    system_prompt, surviving_terms = build_system_prompt(
        vocab_data, tokenizer, max_input_tokens
    )
    print(
        f"=== System prompt: {len(tokenizer.encode(system_prompt))} tokens, "
        f"{len(surviving_terms)}/{total_terms} terms survive ==="
    )

    # 4. Build constrained Pydantic schema from surviving terms
    EnrichmentModel = build_schema(surviving_terms)

    # 5. Compile JSON Schema state machine — done once, shared across all documents
    print("=== Compiling JSON Schema State Machine ===")
    parser = JsonSchemaParser(EnrichmentModel.model_json_schema())
    prefix_function = build_transformers_prefix_allowed_tokens_fn(tokenizer, parser)
    print("=== Pipeline ready ===")

    csv_files = sorted(INPUT_DIR.glob("*.csv"))
    total_processed = 0
    total_errors    = 0

    for csv_file in csv_files:
        out_file = OUTPUT_DIR / f"{csv_file.stem}_enriched.json"

        # --- resume: skip documents that have already been fully processed ---
        if out_file.exists():
            print(f"[skip] {csv_file.name} — output already exists.")
            # Count toward totals so paradata statistics remain accurate.
            logger.log_document_success()
            continue

        print(f"Processing: {csv_file.name} ...")
        try:
            enriched_results, doc_stats = process_document(
                csv_path=csv_file,
                model=model,
                tokenizer=tokenizer,
                prefix_function=prefix_function,
                system_prompt=system_prompt,
                EnrichmentModel=EnrichmentModel,
                max_input_tokens=max_input_tokens,
                include_non_text=INCLUDE_NON_TEXT,
                min_char_count=MIN_CHAR_COUNT,
                min_char_non_text=MIN_CHAR_NON_TEXT,
                min_alpha_ratio_non_text=MIN_ALPHA_RATIO_NON_TEXT,
            )

            total_processed += doc_stats["processed"]
            total_errors    += doc_stats["skipped_error"]
            print(
                f"  processed={doc_stats['processed']}, "
                f"skipped_filter={doc_stats['skipped_filter']}, "
                f"errors={doc_stats['skipped_error']}"
            )

            if enriched_results:
                with open(out_file, "w", encoding="utf-8") as out_f:
                    json.dump(enriched_results, out_f, indent=4, ensure_ascii=False)
                print(f"  -> {len(enriched_results)} records → {out_file.name}")
                logger.log_success("json", count=1)
                logger.log_document_success()
            else:
                logger.log_skip(
                    csv_file.name,
                    f"No lines passed quality filter or all inference calls failed "
                    f"(stats={doc_stats}).",
                )

        except Exception as e:
            print(f"Critical error on {csv_file.name}: {e}")
            logger.log_skip(csv_file.name, str(e))

        finally:
            # Release cached GPU memory after each document to prevent fragmentation
            # across a long multi-document run.
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    print(
        f"\n=== Run complete: {total_processed} lines enriched, "
        f"{total_errors} inference errors across {len(csv_files)} files ==="
    )
    logger.finalize(input_total=len(csv_files))