"""
tests/test_lazy_imports.py – Unit tests for utils/lazy_imports.py, the
``@lru_cache``-wrapped deferred loaders that keep torch/transformers out of the
CPU import path.
"""

import pytest

from utils.lazy_imports import load_torch, load_transformers


def test_loaders_are_lru_cached():
    assert hasattr(load_torch, "cache_info")
    assert hasattr(load_transformers, "cache_info")


def test_load_torch_returns_cached_module():
    try:
        first = load_torch()
    except Exception as exc:  # torch missing or partially installed
        pytest.skip(f"torch unavailable: {exc}")
    assert load_torch() is first  # second call served from cache


def test_load_transformers_returns_cached_tuple():
    # Guard on the real load (not importorskip): a namespace-only transformers
    # would pass importorskip but fail on attribute import.
    try:
        first = load_transformers()
    except Exception as exc:  # transformers missing or partially installed
        pytest.skip(f"transformers unavailable: {exc}")
    assert load_transformers() is first
    assert len(first) == 3  # (AutoTokenizer, AutoModel, pipeline)
