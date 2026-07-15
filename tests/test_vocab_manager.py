"""
tests/test_vocab_manager.py – Unit tests for vocab_manager.py's
VocabularyManager: config/taxonomy loading, keyword theme assignment, injectable
LLM classification, and vocab persistence. Network paths (AMCR OAI-PMH sync) are
never exercised — every test provides an on-disk vocab file or a mock predictor.
"""

import json

from vocab_manager import VocabularyManager

TAXONOMY = {
    "Site Types": {"priority": 10, "keywords": {"cs": ["hrad", "mohyla"]}},
    "Find Types": {"priority": 8, "keywords": {"cs": ["keramika", "nůž"]}},
}


def _mgr(tmp_path, taxonomy=TAXONOMY, llm_predictor=None):
    cfg = tmp_path / "taxonomy.json"
    cfg.write_text(json.dumps(taxonomy), encoding="utf-8")
    vocab = tmp_path / "vocab.json"
    return VocabularyManager(str(vocab), str(cfg), llm_predictor=llm_predictor)


# ── config loading ──────────────────────────────────────────────────────────
def test_load_config_from_file(tmp_path):
    assert "Site Types" in _mgr(tmp_path).taxonomy


def test_load_config_missing_uses_builtin_default(tmp_path):
    m = VocabularyManager(str(tmp_path / "v.json"), str(tmp_path / "nope.json"))
    assert "Site Types" in m.taxonomy  # built-in default taxonomy


# ── _assign_theme ───────────────────────────────────────────────────────────
def test_assign_theme_matches_keyword(tmp_path):
    m = _mgr(tmp_path)
    assert m._assign_theme({"cs": "starý hrad"}) == "Site Types"
    assert m._assign_theme({"cs": "zdobená keramika"}) == "Find Types"


def test_assign_theme_no_match_is_other(tmp_path):
    assert _mgr(tmp_path)._assign_theme({"cs": "nesmysl"}) == "Other"


def test_assign_theme_higher_priority_wins(tmp_path):
    m = _mgr(tmp_path)
    # contains both a Find (pri 8) and Site (pri 10) keyword → higher priority wins
    assert m._assign_theme({"cs": "hrad s keramikou"}) == "Site Types"


# ── classify_with_llm (injectable predictor) ────────────────────────────────
def test_classify_with_llm_none_without_predictor(tmp_path):
    assert _mgr(tmp_path).classify_with_llm({"cs": "x"}) is None


def test_classify_with_llm_returns_matched_category(tmp_path):
    m = _mgr(tmp_path, llm_predictor=lambda prompt: "Find Types")
    assert m.classify_with_llm({"cs": "x", "en": "y"}) == "Find Types"


def test_classify_with_llm_unmatched_returns_none(tmp_path):
    m = _mgr(tmp_path, llm_predictor=lambda prompt: "Nonexistent")
    assert m.classify_with_llm({"cs": "x"}) is None


def test_classify_with_llm_swallows_predictor_error(tmp_path):
    def boom(prompt):
        raise RuntimeError("llm down")

    assert _mgr(tmp_path, llm_predictor=boom).classify_with_llm({"cs": "x"}) is None


# ── persistence + stats ─────────────────────────────────────────────────────
def test_save_and_load_round_trip(tmp_path):
    m = _mgr(tmp_path)
    m.vocab_data = {"Site Types": {"hrad": {"en": "castle"}}}
    m.save()

    loaded = _mgr(tmp_path).load()
    assert loaded == {"Site Types": {"hrad": {"en": "castle"}}}


def test_vocab_statistics_counts_terms_per_theme(tmp_path):
    m = _mgr(tmp_path)
    m.vocab_data = {"Site Types": {"a": 1, "b": 2}, "Find Types": {"c": 3}}
    assert m.vocab_statistics() == {"Site Types": 2, "Find Types": 1}


def test_get_prompt_string_is_cached(tmp_path):
    m = _mgr(tmp_path)
    m.vocab_data = {"X": {"a": 1}}
    first = m.get_prompt_string()
    assert first is m.get_prompt_string()  # served from cache
    m._invalidate_cache()
    assert m.get_prompt_string() == first  # rebuilt to equal content
