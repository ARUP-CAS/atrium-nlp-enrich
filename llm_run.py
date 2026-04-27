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

import torch
from lmformatenforcer import JsonSchemaParser
from lmformatenforcer.integrations.transformers import (
    build_transformers_prefix_allowed_tokens_fn,
)

from atrium_paradata import ParadataLogger
from vocab_manager import VocabularyManager

from llm_utils import (
    CONTEXT_RESERVED,
    MAX_NEW_TOKENS,
    build_schema,
    build_system_prompt,
    load_config,
    load_model_and_tokenizer,
    process_document,
)


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

    MODEL_KEY    = config.get("MODEL_KEY",    "gemma-4-26b-moe-gguf")
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