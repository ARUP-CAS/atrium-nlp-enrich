"""
tests/test_keywords.py
======================
Unit tests for the pure-Python functions in ``keywords.py``.

No ML models, no network access, and no GPU are required.
All tests use the ``sample_conllu`` / ``empty_conllu`` fixtures defined in
``conftest.py``.

Fixture content summary (see ``fixtures/sample.conllu``):
    Sentence 1 — "Archeologický výzkum byl proveden."
        ADJ:  archeologický
        NOUN: výzkum
        AUX:  být           ← excluded (not NOUN/PROPN/ADJ)
        VERB: provedený     ← excluded
        SpaceAfter=No on token 4 (proveden), so "proveden." has no space.

    Sentence 2 — "Nalezená keramika a nádoby."
        ADJ:  nalezený
        NOUN: keramika
        CCONJ: a            ← excluded
        NOUN: nádoba
        SpaceAfter=No on token 4 (nádoby).

    Sentence 3 — "Starý výzkum trval."
        ADJ:  starý
        NOUN: výzkum        ← second occurrence; total count = 2
        VERB: trvat         ← excluded
        SpaceAfter=No on token 3 (trval).

Expected ``_extract_lemmas`` output (order may vary between sents):
    ["archeologický", "výzkum", "nalezený", "keramika", "nádoba", "starý", "výzkum"]

Expected ``Counter``:  výzkum=2, all others=1
"""

import csv
from collections import Counter
from pathlib import Path
from typing import List, Tuple

import pytest

from keywords import (
    _extract_lemmas,
    _extract_legacy,
    _extract_surface_text,
    _sort_csv_file,
    extract_keywords,
)


# ════════════════════════════════════════════════════════════════════════════
# _extract_surface_text
# ════════════════════════════════════════════════════════════════════════════
class TestExtractSurfaceText:

    def test_returns_nonempty_string_from_fixture(self, sample_conllu):
        text = _extract_surface_text(sample_conllu)
        assert isinstance(text, str)
        assert len(text) > 0

    def test_contains_expected_words(self, sample_conllu):
        text = _extract_surface_text(sample_conllu)
        for word in ("Archeologický", "výzkum", "keramika", "Starý"):
            assert word in text

    def test_spaceafter_no_attaches_token_to_next(self, sample_conllu):
        """
        Token 4 of sentence 1 has SpaceAfter=No, so "proveden" should be
        immediately followed by "." with no intervening space.
        """
        text = _extract_surface_text(sample_conllu)
        assert "proveden." in text

    def test_spaceafter_no_respected_in_all_sentences(self, sample_conllu):
        """Each sentence ends with a word+period pair, no space before the stop."""
        text = _extract_surface_text(sample_conllu)
        assert "nádoby." in text
        assert "trval." in text

    def test_no_leading_or_trailing_whitespace(self, sample_conllu):
        text = _extract_surface_text(sample_conllu)
        assert text == text.strip()

    def test_comment_lines_excluded(self, sample_conllu):
        text = _extract_surface_text(sample_conllu)
        # CoNLL-U comment markers should never appear in the surface form
        assert not any(line.startswith("#") for line in text.splitlines())

    def test_empty_file_returns_empty_string(self, empty_conllu):
        text = _extract_surface_text(empty_conllu)
        assert text == ""

    def test_nonexistent_file_returns_empty_string(self, tmp_path):
        """Graceful fallback — no exception raised for a missing file."""
        text = _extract_surface_text(str(tmp_path / "does_not_exist.conllu"))
        assert text == ""


# ════════════════════════════════════════════════════════════════════════════
# _extract_lemmas
# ════════════════════════════════════════════════════════════════════════════
class TestExtractLemmas:

    def test_returns_list_of_strings(self, sample_conllu):
        lemmas = _extract_lemmas(sample_conllu)
        assert isinstance(lemmas, list)
        assert all(isinstance(l, str) for l in lemmas)

    def test_all_lemmas_are_lowercase(self, sample_conllu):
        lemmas = _extract_lemmas(sample_conllu)
        assert all(l == l.lower() for l in lemmas), \
            f"Non-lowercase lemmas found: {[l for l in lemmas if l != l.lower()]}"

    def test_all_lemmas_are_alphabetic(self, sample_conllu):
        lemmas = _extract_lemmas(sample_conllu)
        assert all(l.isalpha() for l in lemmas), \
            f"Non-alpha lemmas found: {[l for l in lemmas if not l.isalpha()]}"

    def test_all_lemmas_longer_than_one_char(self, sample_conllu):
        lemmas = _extract_lemmas(sample_conllu)
        assert all(len(l) > 1 for l in lemmas)

    def test_noun_lemmas_present(self, sample_conllu):
        lemmas = _extract_lemmas(sample_conllu)
        assert "výzkum" in lemmas
        assert "keramika" in lemmas
        assert "nádoba" in lemmas

    def test_adj_lemmas_present(self, sample_conllu):
        lemmas = _extract_lemmas(sample_conllu)
        assert "archeologický" in lemmas
        assert "nalezený" in lemmas
        assert "starý" in lemmas

    def test_aux_verb_cconj_punct_excluded(self, sample_conllu):
        """AUX, VERB, CCONJ, and PUNCT tokens must not contribute lemmas."""
        lemmas = _extract_lemmas(sample_conllu)
        excluded = {"být", "provedený", "trvat", "a"}
        overlap = set(lemmas) & excluded
        assert not overlap, f"Unexpected lemmas from non-content POS: {overlap}"

    def test_výzkum_appears_twice(self, sample_conllu):
        """'výzkum' appears in sentences 1 and 3 — count must be 2."""
        lemmas = _extract_lemmas(sample_conllu)
        assert lemmas.count("výzkum") == 2

    def test_empty_file_returns_empty_list(self, empty_conllu):
        assert _extract_lemmas(empty_conllu) == []

    def test_total_lemma_count(self, sample_conllu):
        """
        3 ADJ + 4 NOUN across 3 sentences = 7 content-word lemmas.
        (ADJ: archeologický, nalezený, starý = 3;
         NOUN: výzkum ×2, keramika, nádoba = 4)
        """
        lemmas = _extract_lemmas(sample_conllu)
        assert len(lemmas) == 7


# ════════════════════════════════════════════════════════════════════════════
# _extract_legacy
# ════════════════════════════════════════════════════════════════════════════
class TestExtractLegacy:

    def test_returns_list_of_tuples(self, sample_conllu):
        result = _extract_legacy(sample_conllu, num_keywords=5)
        assert isinstance(result, list)
        assert all(isinstance(item, tuple) and len(item) == 2 for item in result)

    def test_scores_are_floats(self, sample_conllu):
        result = _extract_legacy(sample_conllu, num_keywords=5)
        assert all(isinstance(score, float) for _, score in result)

    def test_most_frequent_lemma_is_first(self, sample_conllu):
        """'výzkum' (count=2) must be the top keyword."""
        result = _extract_legacy(sample_conllu, num_keywords=10)
        assert result[0][0] == "výzkum"
        assert result[0][1] == 2.0

    def test_sorted_descending_by_score(self, sample_conllu):
        result = _extract_legacy(sample_conllu, num_keywords=10)
        scores = [score for _, score in result]
        assert scores == sorted(scores, reverse=True)

    def test_num_keywords_limits_result_length(self, sample_conllu):
        for n in (1, 3, 6):
            result = _extract_legacy(sample_conllu, num_keywords=n)
            assert len(result) <= n, f"Expected ≤{n} results, got {len(result)}"

    def test_top1_returns_výzkum(self, sample_conllu):
        result = _extract_legacy(sample_conllu, num_keywords=1)
        assert len(result) == 1
        assert result[0][0] == "výzkum"
        assert result[0][1] == 2.0

    def test_all_keywords_in_original_lemmas(self, sample_conllu):
        """Every returned keyword must have been present in the source file."""
        lemmas = set(_extract_lemmas(sample_conllu))
        result = _extract_legacy(sample_conllu, num_keywords=10)
        for kw, _ in result:
            assert kw in lemmas, f"Keyword '{kw}' not in source lemmas"

    def test_empty_file_returns_empty_list(self, empty_conllu):
        result = _extract_legacy(empty_conllu, num_keywords=10)
        assert result == []

    def test_num_keywords_larger_than_vocabulary(self, sample_conllu):
        """Requesting more keywords than unique lemmas should not raise."""
        result = _extract_legacy(sample_conllu, num_keywords=1000)
        # Only 6 unique lemmas in the fixture
        assert len(result) == 6

    def test_extra_kwargs_ignored(self, sample_conllu):
        """The **_ catch-all must silently absorb irrelevant kwargs."""
        result = _extract_legacy(sample_conllu, num_keywords=3,
                                 lang="cs", max_words=3)
        assert len(result) <= 3


# ════════════════════════════════════════════════════════════════════════════
# extract_keywords — dispatch layer
# ════════════════════════════════════════════════════════════════════════════
class TestExtractKeywordsDispatch:

    def test_unknown_method_raises_value_error(self, sample_conllu):
        with pytest.raises(ValueError, match="Unknown method"):
            extract_keywords(sample_conllu, "nonexistent_backend", num_keywords=5)

    def test_legacy_method_dispatches_correctly(self, sample_conllu):
        result = extract_keywords(sample_conllu, "legacy", num_keywords=3)
        assert isinstance(result, list)
        assert result[0][0] == "výzkum"

    def test_single_path_returns_list_of_tuples(self, sample_conllu):
        result = extract_keywords(sample_conllu, "legacy", num_keywords=5)
        assert isinstance(result, list)
        assert all(isinstance(item, tuple) for item in result)

    def test_batch_mode_returns_list_of_results(self, sample_conllu, empty_conllu):
        """Passing a list of paths returns a list-of-lists for non-keybert methods."""
        results = extract_keywords(
            [sample_conllu, empty_conllu], "legacy", num_keywords=5
        )
        assert isinstance(results, list)
        assert len(results) == 2
        # First doc has keywords; second (empty) is empty
        assert len(results[0]) > 0
        assert results[1] == []

    def test_batch_order_preserved(self, sample_conllu, empty_conllu):
        """Results must correspond to paths in the same order."""
        paths = [empty_conllu, sample_conllu]  # reversed order
        results = extract_keywords(paths, "legacy", num_keywords=3)
        assert results[0] == []       # empty_conllu → no keywords
        assert len(results[1]) > 0    # sample_conllu → has keywords

    def test_single_vs_batch_consistency(self, sample_conllu):
        """Single-path and batch(single-path) must return identical keyword lists."""
        single = extract_keywords(sample_conllu, "legacy", num_keywords=5)
        batched = extract_keywords([sample_conllu], "legacy", num_keywords=5)
        assert batched == [single]


# ════════════════════════════════════════════════════════════════════════════
# _sort_csv_file
# ════════════════════════════════════════════════════════════════════════════
class TestSortCsvFile:

    def _make_csv(self, tmp_path, rows: list[str]) -> Path:
        """Write a CSV file from a list of raw line strings."""
        p = tmp_path / "data.csv"
        p.write_text("\n".join(rows) + "\n", encoding="utf-8")
        return p

    def _read_first_col(self, path: Path) -> list[str]:
        rows = list(csv.reader(path.open(encoding="utf-8")))
        return [r[0] for r in rows]

    def test_header_stays_in_row_zero(self, tmp_path):
        p = self._make_csv(tmp_path, [
            "document_id,kw-1,score-1",
            "zoo,word,1.0",
            "abc,word,1.0",
        ])
        _sort_csv_file(str(p))
        first_col = self._read_first_col(p)
        assert first_col[0] == "document_id"

    def test_rows_sorted_alphabetically(self, tmp_path):
        p = self._make_csv(tmp_path, [
            "document_id,kw-1,score-1",
            "ctx_z,word,1.0",
            "ctx_a,word,2.0",
            "ctx_m,word,0.5",
        ])
        _sort_csv_file(str(p))
        first_col = self._read_first_col(p)
        data_rows = first_col[1:]
        assert data_rows == sorted(data_rows)

    def test_sort_is_stable_for_already_sorted_file(self, tmp_path):
        p = self._make_csv(tmp_path, [
            "document_id,score",
            "aaa,1.0",
            "bbb,2.0",
            "ccc,3.0",
        ])
        _sort_csv_file(str(p))
        first_col = self._read_first_col(p)
        assert first_col[1:] == ["aaa", "bbb", "ccc"]

    def test_single_data_row_unchanged(self, tmp_path):
        p = self._make_csv(tmp_path, [
            "document_id,kw-1",
            "only_doc,word",
        ])
        _sort_csv_file(str(p))
        first_col = self._read_first_col(p)
        assert first_col == ["document_id", "only_doc"]

    def test_original_data_preserved_after_sort(self, tmp_path):
        """Sorting must not drop or duplicate any row."""
        rows = [f"doc_{i},kw,{i}.0" for i in range(10, 0, -1)]
        p = self._make_csv(tmp_path, ["document_id,kw,score"] + rows)
        _sort_csv_file(str(p))
        sorted_rows = list(csv.reader(p.open(encoding="utf-8")))[1:]
        doc_ids = {r[0] for r in sorted_rows}
        assert doc_ids == {f"doc_{i}" for i in range(1, 11)}
        assert len(sorted_rows) == 10