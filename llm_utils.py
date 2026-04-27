"""
llm_utils.py — Reusable components for the LLM Semantic Enrichment Pipeline.

Contains: compatibility shims, model registry, config loader, line-quality filter,
dynamic Pydantic schema builder, vocabulary helpers, system-prompt builder,
model/tokenizer loader, context-window helper, and core document processor.

Import this module before any other CUDA-touching library — the PYTORCH_CUDA_ALLOC_CONF
guard at the top of this file must be the very first thing that runs.
"""

# Must be set before ANY import that can touch CUDA (bitsandbytes initialises the
# CUDA context on import via its C extension). Setting this inside __main__ is too
# late — the allocator config is locked in when the context is first created.
import os
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

import csv
import gc
import json
import enum
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import torch

# ---------------------------------------------------------------------------
# Compatibility shim: transformers 5.x dev moved PreTrainedTokenizerBase
# ---------------------------------------------------------------------------
import transformers.tokenization_utils as _tu
import transformers.tokenization_utils_base as _tub
if not hasattr(_tu, "PreTrainedTokenizerBase"):
    _tu.PreTrainedTokenizerBase = _tub.PreTrainedTokenizerBase


def _patch_params4bit_compat() -> bool:
    """
    Compatibility shim for bitsandbytes < 0.44 vs. newer transformers/accelerate.

    Patches two known breakage points:
      1. Params4bit.__new__ / __init__ chokes on the _is_hf_initialized kwarg.
      2. QuantState.as_dict raises when offset is a meta-device tensor.

    Permanent fix: pip install -U bitsandbytes inside your venv.
    """
    try:
        import bitsandbytes.nn as _bnb_nn
        import bitsandbytes.functional as _bnb_func
        import inspect

        patched_anything = False

        # --- 1. Patch Params4bit __new__ / __init__ (stray kwarg fix) ---
        new_sig = str(inspect.signature(_bnb_nn.Params4bit.__new__))
        if "_is_hf_initialized" not in new_sig and "**" not in new_sig:
            _orig_p4b_new = _bnb_nn.Params4bit.__new__

            def _p4b_new(cls, *args, **kwargs):
                kwargs.pop("_is_hf_initialized", None)
                return _orig_p4b_new(cls, *args, **kwargs)

            _bnb_nn.Params4bit.__new__ = _p4b_new

            if "__init__" in _bnb_nn.Params4bit.__dict__:
                _orig_p4b_init = _bnb_nn.Params4bit.__init__

                def _p4b_init(self, *args, **kwargs):
                    kwargs.pop("_is_hf_initialized", None)
                    return _orig_p4b_init(self, *args, **kwargs)

                _bnb_nn.Params4bit.__init__ = _p4b_init

            patched_anything = True

        # --- 2. Patch QuantState.as_dict (meta-tensor .item() fix) ---
        if hasattr(_bnb_func, "QuantState"):
            _orig_as_dict = _bnb_func.QuantState.as_dict

            def _patched_as_dict(self, packed: bool = False):
                orig_offset = getattr(self, "offset", None)
                is_meta_offset = (
                    isinstance(orig_offset, torch.Tensor)
                    and orig_offset.device.type == "meta"
                )
                if is_meta_offset:
                    self.offset = torch.tensor(0.0)
                try:
                    return _orig_as_dict(self, packed=packed)
                finally:
                    if is_meta_offset:
                        self.offset = orig_offset

            if _bnb_func.QuantState.as_dict.__name__ != "_patched_as_dict":
                _bnb_func.QuantState.as_dict = _patched_as_dict
                patched_anything = True

        if patched_anything:
            print(
                "[COMPAT] Patched bitsandbytes (Params4bit & QuantState) for "
                "accelerate/meta-device compatibility. "
                "Permanent fix: pip install -U bitsandbytes inside your venv."
            )
        return patched_anything

    except Exception as exc:
        print(f"[WARN] Could not apply compatibility patch: {exc}")
        return False


_patch_params4bit_compat()

from transformers import AutoModelForCausalLM, AutoTokenizer  # noqa: E402
from pydantic import BaseModel, Field, ValidationError         # noqa: E402
from lmformatenforcer import JsonSchemaParser                  # noqa: E402
from lmformatenforcer.integrations.transformers import (       # noqa: E402
    build_transformers_prefix_allowed_tokens_fn,
)

from atrium_paradata import ParadataLogger   # noqa: E402
from vocab_manager import VocabularyManager  # noqa: E402


# ---------------------------------------------------------------------------
# 1. Model Registry
# ---------------------------------------------------------------------------

MODEL_REGISTRY: Dict[str, Dict] = {
    "gemma-4-26b-moe-gguf": {
        "hf_id": "bartowski/google_gemma-4-26B-A4B-it-GGUF",
        "filename": "*Q4_K_M.gguf",
        "context_window": 8192,
        "is_gguf": True,
        "hf_token_required": False,
    },
    "gemma-4-31b-it": {
        "hf_id": "google/gemma-4-31B-it",
        "context_window": 256000,
        "trust_remote_code": False,
        "torch_dtype": torch.bfloat16,
        "hf_token_required": True,
        "load_in_4bit": True,
    },
    "qwen-3.6-35b-moe": {
        "hf_id": "Qwen/Qwen3.6-35B-A3B",
        "context_window": 262144,
        "trust_remote_code": False,
        "torch_dtype": torch.bfloat16,
        "hf_token_required": False,
        "is_moe": True,
        "bnb_experts_broken": True,
    },
    "qwen-3.6-27b-it": {
        "hf_id": "Qwen/Qwen3.6-27B",
        "context_window": 262144,
        "trust_remote_code": False,
        "torch_dtype": torch.bfloat16,
        "hf_token_required": False,
        "load_in_4bit": True,
    },
    "gemma-4-26b-moe": {
        "hf_id": "google/gemma-4-26B-A4B-it",
        "context_window": 256000,
        "trust_remote_code": False,
        "torch_dtype": torch.bfloat16,
        "hf_token_required": True,
        "is_moe": True,
        "bnb_experts_broken": True,
    },
    "qwen-3.5-9b-it": {
        "hf_id": "Qwen/Qwen3.5-9B",
        "context_window": 262144,
        "trust_remote_code": False,
        "torch_dtype": torch.bfloat16,
        "hf_token_required": False,
    },
    "qwen3-14b": {
        "hf_id": "OpenPipe/Qwen3-14B-Instruct",
        "context_window": 131072,
        "trust_remote_code": False,
        "torch_dtype": torch.bfloat16,
        "hf_token_required": False,
    },
    "qwen3-8b": {
        "hf_id": "Qwen/Qwen3-8B",
        "context_window": 131072,
        "trust_remote_code": False,
        "torch_dtype": torch.bfloat16,
        "hf_token_required": False,
    },
    "gemma-4-26b-moe-awq": {
        "hf_id": "google/gemma-4-26B-A4B-it",   # update to actual AWQ repo when available
        "context_window": 256000,
        "trust_remote_code": False,
        "torch_dtype": torch.bfloat16,
        "hf_token_required": True,
        "is_moe": True,
        "bnb_experts_broken": True,
    },
    "qwen2.5-14b-awq": {
        "hf_id": "Qwen/Qwen2.5-14B-Instruct-AWQ",
        "context_window": 131072,
        "trust_remote_code": False,
        "torch_dtype": torch.float16,
        "hf_token_required": False,
    },
}

MAX_NEW_TOKENS = 2048
CONTEXT_RESERVED = MAX_NEW_TOKENS + 512
_ALWAYS_SKIP_CATEG = {"Empty", "Trash"}


# ---------------------------------------------------------------------------
# 2. Configuration Loader
# ---------------------------------------------------------------------------

def load_config(config_path: str = "llm_config.txt") -> Dict[str, str]:
    """Parse a simple KEY=VALUE config file, ignoring blank lines and # comments."""
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
    Return (should_process, skip_reason).

    Applies category-level and character-level filters before sending a line
    to the LLM, avoiding wasted inference on noise rows.
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
            return False, f"Non-text alpha ratio too low ({alpha_ratio:.2f})"
        return True, ""

    if len(text) < min_char_count:
        return False, f"text too short ({len(text)} < {min_char_count} chars)"

    return True, ""


# ---------------------------------------------------------------------------
# 4. Dynamic Pydantic Schema Builder
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
# 5. Vocabulary Helpers
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


def _count_tokens(text: str, tokenizer: Any) -> int:
    """Count tokens uniformly for both transformers tokenizers and llama.cpp models."""
    if hasattr(tokenizer, "tokenize") and not isinstance(
        tokenizer, _tu.PreTrainedTokenizerBase
    ):
        # llama_cpp.Llama acts as both model and tokenizer
        return len(tokenizer.tokenize(text.encode("utf-8")))
    return len(tokenizer.encode(text))


# ---------------------------------------------------------------------------
# 6. Thematic System Prompt Builder
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
        "CRITICAL: If and only if the marked line is purely administrative "
        "(e.g., page numbers, titles, author names) and lacks archaeological context, "
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
    token_count = _count_tokens(full_prompt, tokenizer)
    print(f"[vocab] {len(prioritised)} terms, {token_count} tokens (grouped by theme)")

    if token_count <= max_tokens:
        print("[vocab] Full vocabulary fits in context window.")
        return full_prompt, [t["cs"] for t in prioritised]

    print(f"[WARN] Vocabulary ({token_count} tokens) exceeds budget ({max_tokens}). Truncating.")

    lo, hi = 0, len(prioritised)
    while lo < hi - 1:
        mid = (lo + hi) // 2
        candidate = _build_candidate_prompt(prioritised[:mid])
        if _count_tokens(candidate, tokenizer) <= max_tokens:
            lo = mid
        else:
            hi = mid

    surviving_terms = prioritised[:lo]
    surviving_prompt = _build_candidate_prompt(surviving_terms)
    surviving_cs = [t["cs"] for t in surviving_terms]
    return surviving_prompt, surviving_cs


# ---------------------------------------------------------------------------
# 7. Model + Tokenizer Loader
# ---------------------------------------------------------------------------

def _verify_quantization_effective(model: Any, model_key: str, spec: dict) -> None:
    """
    Sanity-check that 4-bit quantization actually reduced memory footprint.
    Raises RuntimeError if the ratio vs. BF16 baseline is suspiciously high,
    which usually means bitsandbytes silently fell back to full precision.
    """
    if not spec.get("load_in_4bit"):
        return

    footprint_bytes = model.get_memory_footprint()
    footprint_gb = footprint_bytes / 1024 ** 3

    total_params = sum(p.numel() for p in model.parameters())
    bf16_estimate_gb = total_params * 2 / 1024 ** 3
    ratio = footprint_gb / bf16_estimate_gb if bf16_estimate_gb > 0 else 0.0

    print(
        f"[INFO] Model footprint: {footprint_gb:.1f} GB "
        f"(BF16 estimate: {bf16_estimate_gb:.1f} GB, ratio: {ratio:.2f})"
    )

    if ratio > 0.50:
        raise RuntimeError(
            f"Quantization failed for {model_key}. Review MoE BitsAndBytes bugs."
        )


def load_model_and_tokenizer(
    model_key: str, hf_token: Optional[str] = None
) -> Tuple[Any, Any, dict]:
    """
    Load model and tokenizer for the given registry key.

    Returns:
        (model, tokenizer, spec)  — for GGUF models the same llama_cpp.Llama
        object is returned for both model and tokenizer positions.
    """
    if model_key not in MODEL_REGISTRY:
        raise ValueError(
            f"Unknown MODEL_KEY '{model_key}'. "
            f"Available: {', '.join(MODEL_REGISTRY.keys())}"
        )

    spec = MODEL_REGISTRY[model_key]
    hf_id = spec["hf_id"]

    # --- GGUF path (llama.cpp) ---
    if spec.get("is_gguf"):
        try:
            from llama_cpp import Llama
        except ImportError:
            raise ImportError(
                "Please install llama-cpp-python to use GGUF models: "
                "pip install llama-cpp-python"
            )

        print(f"=== Loading GGUF via llama.cpp: {hf_id} ===")
        model = Llama.from_pretrained(
            repo_id=hf_id,
            filename=spec.get("filename", "*.gguf"),
            n_ctx=spec["context_window"],
            n_gpu_layers=-1,
            flash_attn=True,
            verbose=False,
        )
        return model, model, spec  # llama.cpp object acts as both model & tokenizer

    # --- Transformers path ---
    if spec.get("bnb_experts_broken") and spec.get("load_in_4bit"):
        raise RuntimeError(
            "BnB 4-bit requested for MoE model with fused experts. Use GGUF instead."
        )

    print(f"=== Loading: {hf_id} ===")

    from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig

    tokenizer = AutoTokenizer.from_pretrained(
        hf_id,
        trust_remote_code=spec.get("trust_remote_code", False),
        token=hf_token or None,
    )

    if getattr(tokenizer, "pad_token", None) is None:
        tokenizer.pad_token = getattr(tokenizer, "eos_token", None)

    bnb_config = None
    if spec.get("load_in_4bit"):
        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=spec["torch_dtype"],
            bnb_4bit_use_double_quant=True,
            bnb_4bit_quant_type="nf4",
            llm_int8_enable_fp32_cpu_offload=True,
        )

    load_kwargs: Dict[str, Any] = dict(
        device_map="auto",
        quantization_config=bnb_config,
        trust_remote_code=spec.get("trust_remote_code", False),
        token=hf_token or None,
        attn_implementation="sdpa",
    )
    if bnb_config is None:
        load_kwargs["dtype"] = spec["torch_dtype"]

    model = AutoModelForCausalLM.from_pretrained(hf_id, **load_kwargs)
    _verify_quantization_effective(model, model_key, spec)
    model.eval()
    return model, tokenizer, spec


# ---------------------------------------------------------------------------
# 8. Context Window Builder
# ---------------------------------------------------------------------------

def get_context_window(rows: List[dict], center_idx: int, window: int = 2) -> str:
    """
    Build a text snippet around rows[center_idx] for use as the LLM user prompt.

    The target line is wrapped in <target_line> tags. Surrounding lines on the
    same page (within ±window) are included as plain context. For non-leading
    rows, the first two non-noise lines of the document are prepended as a
    global header so the model can anchor the archaeological context.
    """
    _NOISE_CATEG = {"Empty", "Trash", "Non-text"}

    center_row = rows[center_idx]
    center_page = center_row.get("page_num", center_row.get("page", None))

    start = max(0, center_idx - window)
    end = min(len(rows), center_idx + window + 1)

    parts: List[str] = []

    if center_idx > window + 2:
        parts.append("--- GLOBAL DOCUMENT HEADER ---")
        header_lines_added = 0
        for row in rows:
            if row.get("categ", "").strip() not in _NOISE_CATEG:
                pg = row.get("page_num", row.get("page", 0))
                ln = row.get("line_num", row.get("line", 0))
                parts.append(f"    [P{pg} L{ln}] {row.get('text', '').strip()}")
                header_lines_added += 1
                if header_lines_added >= 2:
                    break
        parts.append("--- LOCAL CONTEXT WINDOW ---")

    for i in range(start, end):
        row = rows[i]
        row_page = row.get("page_num", row.get("page", None))
        categ = row.get("categ", "").strip()

        if row_page != center_page and i != center_idx:
            continue
        if i != center_idx and categ in _NOISE_CATEG:
            continue

        text = row.get("text", "").strip()
        pg = row_page
        ln = row.get("line_num", row.get("line", 0))

        if i == center_idx:
            parts.append(f"<target_line> >>> [P{pg} L{ln}] {text} </target_line>")
        else:
            parts.append(f"    [P{pg} L{ln}] {text}")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 9. Core Document Processor
# ---------------------------------------------------------------------------

def process_document(
    csv_path: Path,
    model: Any,
    tokenizer: Any,
    parser: JsonSchemaParser,
    prefix_function: Any,
    system_prompt: str,
    EnrichmentModel: type,
    max_input_tokens: int,
    is_gguf: bool,
    model_key: str,
    include_non_text: bool = True,
    min_char_count: int = 3,
    min_char_non_text: int = 8,
    min_alpha_ratio_non_text: float = 0.40,
) -> Tuple[List[dict], Dict[str, int]]:
    """
    Run LLM inference over every qualifying line in a single CSV document.

    Returns:
        (enriched_lines, stats)  where stats keys are
        'processed', 'skipped_filter', 'skipped_error'.
    """
    file_id = csv_path.stem
    enriched_lines: List[dict] = []
    stats: Dict[str, int] = {"processed": 0, "skipped_filter": 0, "skipped_error": 0}
    consecutive_errors = 0

    with open(csv_path, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if is_gguf:
        from lmformatenforcer.integrations.llamacpp import build_llamacpp_logits_processor
        from llama_cpp import LogitsProcessorList

    for i, row in enumerate(rows):
        inputs = None
        output = None
        try:
            try:
                page_num = int(row.get("page_num", row.get("page", 0)))
                line_num = int(row.get("line_num", row.get("line", 0)))
            except (ValueError, TypeError):
                stats["skipped_filter"] += 1
                continue

            text_chunk = row.get("text", "").strip()
            categ = row.get("categ", "").strip()

            should_process, _ = _should_process_line(
                text_chunk, categ,
                include_non_text, min_char_count,
                min_char_non_text, min_alpha_ratio_non_text,
            )
            if not should_process:
                stats["skipped_filter"] += 1
                continue

            context_chunk = get_context_window(rows, i, window=2)

            messages = [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        f"DOCUMENT CONTEXT:\n{context_chunk}\n\n"
                        "Task: Extract keywords and determine the TEATER category "
                        "ONLY for the line marked inside <target_line>."
                    ),
                },
            ]

            # --- Inference ---
            if is_gguf:
                logits_processors = LogitsProcessorList(
                    [build_llamacpp_logits_processor(model, parser)]
                )
                output = model.create_chat_completion(
                    messages=messages,
                    max_tokens=MAX_NEW_TOKENS,
                    temperature=0.0,
                    logits_processor=logits_processors,
                )
                result_json = output["choices"][0]["message"]["content"]

            else:
                is_qwen3 = any(
                    k in model_key.lower()
                    for k in ("qwen3", "qwen-3.5", "qwen-3.6")
                )
                if is_qwen3:
                    messages[-1]["content"] += "\n/no_think"

                try:
                    prompt = tokenizer.apply_chat_template(
                        messages,
                        tokenize=False,
                        add_generation_prompt=True,
                        **({"enable_thinking": False} if is_qwen3 else {}),
                    )
                except TypeError:
                    prompt = tokenizer.apply_chat_template(
                        messages,
                        tokenize=False,
                        add_generation_prompt=True,
                    )

                inputs = tokenizer(
                    prompt,
                    return_tensors="pt",
                    truncation=True,
                    max_length=max_input_tokens,
                ).to(model.device)

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
                result_json = tokenizer.decode(
                    generated_tokens, skip_special_tokens=True
                )

                del inputs, output
                torch.cuda.empty_cache()

            # --- Parse & validate ---
            try:
                semantic_data = EnrichmentModel.model_validate_json(result_json)
            except ValidationError:
                try:
                    raw_dict = json.loads(result_json, strict=False)
                    if "confidence_score" in raw_dict:
                        try:
                            val = float(raw_dict["confidence_score"])
                            raw_dict["confidence_score"] = min(1.0, max(0.0, val))
                        except (ValueError, TypeError):
                            pass
                    semantic_data = EnrichmentModel.model_validate(raw_dict)
                except (json.JSONDecodeError, ValidationError) as e:
                    print(
                        f"  [{file_id}] Persistent validation error "
                        f"P{page_num} L{line_num}: {e}"
                    )
                    stats["skipped_error"] += 1
                    consecutive_errors += 1
                    if consecutive_errors >= 10:
                        break
                    continue

            dump_data = semantic_data.model_dump()
            dump_data["teater_category"] = semantic_data.category_name()

            if dump_data.get("teater_category") == "Nerelevantní (meta-text)":
                dump_data["extracted_keywords_cs"] = []
                dump_data["extracted_keywords_en"] = []

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
            consecutive_errors = 0

        except Exception as e:
            print(f"  [{file_id}] Inference error P{page_num} L{line_num}: {e}")
            stats["skipped_error"] += 1
            consecutive_errors += 1

            if not is_gguf:
                if inputs is not None:
                    del inputs
                if output is not None:
                    del output
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()

            if consecutive_errors >= 10:
                print(
                    f"  [{file_id}] Aborting document after "
                    f"{consecutive_errors} consecutive errors."
                )
                break

    return enriched_lines, stats
