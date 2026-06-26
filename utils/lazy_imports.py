# utils/lazy_imports.py
from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=None)
def load_torch():
    import torch  # type: ignore

    return torch


@lru_cache(maxsize=None)
def load_transformers():
    from transformers import AutoModel, AutoTokenizer, pipeline  # type: ignore

    return AutoTokenizer, AutoModel, pipeline
