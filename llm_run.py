"""
llm_run.py — Entry point for the LLM Semantic Enrichment Pipeline.

Reads llm_config.txt, initialises the model and vocabulary, then iterates
over every CSV file in INPUT_DIR and writes per-document JSON enrichment
files to OUTPUT_DIR.

Usage:
    python llm_run.py               # uses llm_config.txt in the current directory
    python llm_run.py my_config.txt # uses a custom config file

NOTE: llm_utils is imported first so its PYTORCH_CUDA_ALLOC_CONF guard fires
before any other CUDA-touching library is loaded.
"""

# llm_utils sets PYTORCH_CUDA_ALLOC_CONF at module level, so this import
# must come before any other library that might touch the CUDA context.
import llm_utils  # noqa: F401  (side-effect: env-var + compat patches)

import json
import os
import sys
from pathlib import Path
from pydantic import BaseModel, Field, ValidationError         # noqa: E402
from typing import Any, Dict, List, Tuple
import enum

import torch
from lmformatenforcer import JsonSchemaParser
from lmformatenforcer.integrations.transformers import (
    build_transformers_prefix_allowed_tokens_fn,
)

from atrium_paradata import ParadataLogger
from vocab_manager import VocabularyManager

from llm_utils import count_tokens, load_config, load_model_and_tokenizer, process_document
from llm_utils import CONTEXT_RESERVED

# ---------------------------------------------------------------------------
# Vocabulary Helpers
# ---------------------------------------------------------------------------

def _term_priority(cs: str, en: str) -> int:
    """
    Return a sort key (lower = higher priority) for vocabulary term ordering.

    Priority 2 (lowest / sorted last): ambiguous or proper-noun-only terms.
    Priority 1: multi-word proper-noun pairs that are likely named entities.
    Priority 0 (default): ordinary archaeological terms kept at front.
    """
    if en.rstrip().endswith("(the)"):
        return 2

    if cs == en and cs and cs[0].isupper() and "/ " not in cs:
        return 2

    cs_words = cs.split()
    if (
        len(cs_words) >= 2
        and all(w[0].isupper() for w in cs_words if w)
        and "/" not in cs
        and not any(c.isdigit() for c in cs)
    ):
        _arch_keywords = {
            "kultura", "doba", "období", "eneolit", "paleolit", "neolit",
            "středověk", "novověk", "pravěk", "mezolit", "bronzová",
            "laténská", "halštatská", "stěhování",
        }
        _arch_prefixes = ("Creative", "HaA", "HaB", "HaC", "HaD")

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

# ---------------------------------------------------------------------------
# Dynamic Pydantic Schema Builder
# ---------------------------------------------------------------------------

def build_schema(term_names: List[str]) -> type:
    """
    Dynamically construct a Pydantic model whose teater_category field is an
    Enum constrained to exactly the vocabulary terms that survived the token
    budget.  This drives the lm-format-enforcer JSON schema state machine so
    the model can only ever output a valid category.
    """
    if not term_names:
        raise ValueError(
            "term_names is empty — vocabulary failed to load or was fully truncated."
        )

    TermEnum = enum.Enum(
        "TermEnum", {f"term_{i}": name for i, name in enumerate(term_names)}
    )

    class ConstrainedEnrichment(BaseModel):
        extracted_keywords_cs: List[str] = Field(
            ...,
            description=(
                "Key Czech archaeological terms, methods, or objects found ONLY in the text "
                "marked with (>>>). "
                "DO NOT copy terms from the THEMATIC VOCABULARY list into this array. "
                "If no relevant archaeological terms are present in the target line, return []. "
                "If teater_category is 'Nerelevantní (meta-text)', this array MUST be empty []. "
                "Do not extract any terms from administrative lines. "
                "Extract meaningful multi-word phrases when the archaeological entity is "
                "a compound concept (e.g., 'zásobní jáma', 'kamenná konstrukce', "
                "'kostrový pohřeb') rather than isolated single words."
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
            description="The single most relevant category from the thematic vocabulary.",
        )
        confidence_score: float = Field(
            ...,
            ge=0.0,
            le=1.0,
            description=(
                "Your confidence that the selected teater_category is correct. "
                "Use 1.0 only when the line unambiguously matches the category with no "
                "interpretation required. "
                "Use 0.7–0.9 for reasonable but non-obvious matches. "
                "Use 0.5–0.7 when multiple categories could apply. "
                "Use below 0.5 when forced to guess. "
                "Do NOT output 1.0 uniformly — this field is used for quality filtering."
            ),
        )

        def category_name(self) -> str:
            return self.teater_category.value

    return ConstrainedEnrichment

# ---------------------------------------------------------------------------
# Thematic System Prompt Builder
# ---------------------------------------------------------------------------

def build_system_prompt(
    vocab_data: dict, tokenizer: Any, max_tokens: int
) -> Tuple[str, List[str]]:
    """
    Build the system prompt that embeds the thematic vocabulary.

    If the full vocabulary exceeds the token budget (max_tokens), binary-search
    for the largest prefix that still fits, preserving the highest-priority terms.

    Returns:
        (system_prompt_text, surviving_term_names_cs)
    """
    header = (
        "You are an expert archaeological data extractor. "
        "Analyze the MARKED LINE enclosed in <target_line> ... </target_line> "
        "within its surrounding document context.\n"
        "1. Extract ONLY archaeological entities, features, periods, or materials "
        "from the marked line. "
        "Do NOT extract names of researchers, dates, conjunctions, or administrative words.\n"
        "2. Select the SINGLE most relevant category from the thematic vocabulary list below.\n"
        "CRITICAL: If the marked line is purely administrative, a table of contents, a generic heading "
        "(e.g., page numbers, titles, author names, 'Práce:', 'Obsah:', literature references) or lacks direct archaeological context, "
        "you MUST select 'Nerelevantní (meta-text)'.\n"
        "NEVER select a country name, language name, or geographic region name "
        "as the teater_category for any line — including administrative lines. "
        "For any line that lacks direct archaeological significance, "
        "you MUST use 'Nerelevantní (meta-text)'.\n"
        "When extracting keywords, normalize obvious OCR artifacts and typos to their "
        "correct Czech forms. "
        "Do NOT include garbled tokens or split words as keywords. "
        "Prefer the normalized phrase over the raw OCR text.\n"
        "You MUST use the exact Czech term as written.\n"
        "You MUST respond ONLY with a valid JSON object matching the requested schema.\n\n"
        "THEMATIC VOCABULARY:\n"
    )

    # Collect and prioritise all vocabulary terms
    raw_terms: List[dict] = []
    raw_terms.append({
        "theme": "Administrative / Meta",
        "cs": "Nerelevantní (meta-text)",
        "en": "Irrelevant / Meta-text",
    })

    for theme, data in vocab_data.items():
        if theme.lower() == "other":
            continue
        if isinstance(data, dict):
            if "keywords" in data and isinstance(data["keywords"], dict):
                cs_list = data["keywords"].get("cs", [])
                en_list = data["keywords"].get("en", [])
                for i, cs_key in enumerate(cs_list):
                    en = en_list[i] if i < len(en_list) else cs_key
                    raw_terms.append({"theme": theme, "cs": cs_key, "en": en})
            else:
                for cs_key, pair in data.items():
                    en = pair.get("en", cs_key) if isinstance(pair, dict) else cs_key
                    raw_terms.append({"theme": theme, "cs": cs_key, "en": en})

    prioritised = [raw_terms[0]] + sorted(
        raw_terms[1:], key=lambda t: _term_priority(t["cs"], t["en"])
    )

    def _build_candidate_prompt(term_list: List[dict], other_cap: int = 15) -> str:
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

        prompt += (
            "\nEXAMPLES:\n\n"
            "Input line: \"Výzkum odhalil základy gotického kostela ze 14. století.\"\n"
            "Correct output:\n"
            "{\n"
            "  \"extracted_keywords_cs\": [\"základy\", \"gotický kostel\"],\n"
            "  \"extracted_keywords_en\": [\"foundations\", \"Gothic church\"],\n"
            "  \"teater_category\": \"kostel\",\n"
            "  \"confidence_score\": 0.92\n"
            "}\n\n"
            "Input line: \"Praha, dne 6. října 1956, Dr. Solle\"\n"
            "Correct output:\n"
            "{\n"
            "  \"extracted_keywords_cs\": [],\n"
            "  \"extracted_keywords_en\": [],\n"
            "  \"teater_category\": \"Nerelevantní (meta-text)\",\n"
            "  \"confidence_score\": 1.0\n"
            "}\n"
        )
        return prompt

    full_prompt = _build_candidate_prompt(prioritised)
    token_count = count_tokens(full_prompt, tokenizer)
    print(f"[vocab] {len(prioritised)} terms, {token_count} tokens (grouped by theme)")

    if token_count <= max_tokens:
        print("[vocab] Full vocabulary fits in context window.")
        return full_prompt, [t["cs"] for t in prioritised]

    print(f"[WARN] Vocabulary ({token_count} tokens) exceeds budget ({max_tokens}). Truncating.")

    lo, hi = 0, len(prioritised)
    while lo < hi - 1:
        mid = (lo + hi) // 2
        candidate = _build_candidate_prompt(prioritised[:mid])
        if count_tokens(candidate, tokenizer) <= max_tokens:
            lo = mid
        else:
            hi = mid

    surviving_terms = prioritised[:lo]
    surviving_prompt = _build_candidate_prompt(surviving_terms)
    surviving_cs = [t["cs"] for t in surviving_terms]
    return surviving_prompt, surviving_cs

# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

def main(config_path: str = "llm_config.txt") -> None:
    # ------------------------------------------------------------------
    # 1. Load configuration
    # These steps happen before the logger is created; any error here is
    # a hard misconfiguration that should surface immediately.
    # ------------------------------------------------------------------
    config = load_config(config_path)

    MODEL_KEY    = config.get("MODEL_KEY",    "qwen-3.6-27b-it")
    HF_TOKEN     = config.get("HF_TOKEN",     os.environ.get("HF_TOKEN", None))
    INPUT_DIR    = Path(config.get("INPUT_DIR",  "data_samples/DOC_LINE_LANG_CLASS"))
    VOCAB_PATH   = config.get("VOCAB_PATH",   "data_samples/teater_nested_vocab.json")
    PARADATA_DIR = config.get("PARADATA_DIR", "paradata")

    # Append model name suffix so results from different models never
    # overwrite each other.
    _base_out     = Path(config.get("OUTPUT_DIR", "data_samples/KW_PER_DOC_LLM"))
    _model_suffix = MODEL_KEY.replace(".", "").replace("-", "_")
    OUTPUT_DIR    = _base_out.parent / f"{_base_out.name}_{_model_suffix}"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"=== Output directory: {OUTPUT_DIR} ===")

    INCLUDE_NON_TEXT         = config.get("INCLUDE_NON_TEXT", "true").lower() == "true"
    MIN_CHAR_COUNT           = int(config.get("MIN_CHAR_COUNT",           "3"))
    MIN_CHAR_NON_TEXT        = int(config.get("MIN_CHAR_NON_TEXT",        "8"))
    MIN_ALPHA_RATIO_NON_TEXT = float(config.get("MIN_ALPHA_RATIO_NON_TEXT", "0.40"))

    # ------------------------------------------------------------------
    # 2. Paradata logger
    #
    # Used as a context manager so that finalize() is ALWAYS called —
    # even when steps 3-6 raise an unexpected exception.
    #
    # Normal path:  logger.finalize() is called explicitly at the end of
    #               the `with` block (step 7), with the correct input_total.
    # Exception path: __exit__ calls finalize() automatically with no
    #               input_total (inferred as processed + skipped), then
    #               re-raises the original exception.
    # ------------------------------------------------------------------
    logger = ParadataLogger(
        program="nlp-enrich",
        config={
            **config,
            "output_dir_resolved":      str(OUTPUT_DIR),
            "include_non_text":         INCLUDE_NON_TEXT,
            "min_char_count":           MIN_CHAR_COUNT,
            "min_char_non_text":        MIN_CHAR_NON_TEXT,
            "min_alpha_ratio_non_text": MIN_ALPHA_RATIO_NON_TEXT,
        },
        paradata_dir=PARADATA_DIR,
        output_types=["json"],
    )

    with logger:
        # --------------------------------------------------------------
        # 3. Vocabulary
        # Inside the context manager so a vocab failure is logged before
        # the exception propagates.
        # --------------------------------------------------------------
        vocab_mgr  = VocabularyManager(vocab_path=VOCAB_PATH)
        vocab_data = vocab_mgr.load()
        total_terms = sum(
            len(v.get("keywords", {}).get("cs", [])) if isinstance(v, dict) and "keywords" in v
            else len(v)
            for v in vocab_data.values()
            if isinstance(v, dict)
        )
        if total_terms == 0:
            raise RuntimeError(
                "Vocabulary is empty. "
                "Run vocab_manager.py on a node with internet access first."
            )
        print(f"=== Vocabulary: {total_terms} terms in {len(vocab_data)} broad categories ===")

        # --------------------------------------------------------------
        # 4. Model + tokenizer
        # --------------------------------------------------------------
        model, tokenizer, spec = load_model_and_tokenizer(MODEL_KEY, HF_TOKEN)
        is_gguf          = spec.get("is_gguf", False)
        max_input_tokens = spec["context_window"] - CONTEXT_RESERVED

        # --------------------------------------------------------------
        # 5. System prompt + constrained schema
        # --------------------------------------------------------------
        system_prompt, surviving_terms = build_system_prompt(
            vocab_data, tokenizer, max_input_tokens
        )
        EnrichmentModel = build_schema(surviving_terms)

        print("=== Compiling JSON Schema State Machine ===")
        parser = JsonSchemaParser(EnrichmentModel.model_json_schema())

        prefix_function = (
            None  # GGUF: handled inline inside process_document
            if is_gguf
            else build_transformers_prefix_allowed_tokens_fn(tokenizer, parser)
        )

        print("=== Pipeline ready ===")

        # --------------------------------------------------------------
        # 6. Main processing loop
        # --------------------------------------------------------------
        csv_files       = sorted(INPUT_DIR.glob("*.csv"))
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
                    csv_path=csv_file,
                    model=model,
                    tokenizer=tokenizer,
                    parser=parser,
                    prefix_function=prefix_function,
                    system_prompt=system_prompt,
                    EnrichmentModel=EnrichmentModel,
                    max_input_tokens=max_input_tokens,
                    is_gguf=is_gguf,
                    model_key=MODEL_KEY,
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
                        "No lines passed quality filter or all inference calls failed.",
                    )

            except Exception as e:
                print(f"Critical error on {csv_file.name}: {e}")
                logger.log_skip(csv_file.name, str(e))

            finally:
                if not is_gguf and torch.cuda.is_available():
                    torch.cuda.empty_cache()

        # --------------------------------------------------------------
        # 7. Summary + explicit finalize (normal-path only)
        #
        # logger._skipped is a private attribute; access it here, before
        # finalize() is called, because finalize() marks the logger as
        # done. The attribute itself is not cleared by finalize(), but
        # reading it before makes the ordering dependency explicit.
        # --------------------------------------------------------------
        already_done  = sum(1 for s in logger._skipped if s.get("reason") == "already_exists")
        true_failures = sum(1 for s in logger._skipped if s.get("reason") != "already_exists")
        print(
            f"\n=== Paradata note: {already_done} files skipped (already done), "
            f"{true_failures} files skipped (errors) ==="
        )
        print(
            f"=== Run complete: {total_processed} lines enriched, "
            f"{total_errors} inference errors across {len(csv_files)} files ==="
        )
        logger.finalize(input_total=len(csv_files))
        # __exit__ checks _finalised and will not double-call finalize()


if __name__ == "__main__":
    config_path = sys.argv[1] if len(sys.argv) > 1 else "llm_config.txt"
    main(config_path)