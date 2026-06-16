"""
tests/test_keywords.py
======================
Unit tests for the pure-Python functions in ``keywords.py``.
"""

import csv
from collections import Counter
import pytest

from keywords import _sort_csv_file

TEITOK_MOCK_CONTENT = """<?xml version="1.0" encoding="UTF-8"?>
<teiCorpus>
    <text>
        <s>
            <tok lemma="archeologický" pos="ADJ">Archeologický</tok>
            <tok lemma="výzkum" pos="NOUN">výzkum</tok>
            <tok lemma="v" pos="ADP">v</tok>
            <tok lemma="Praha" pos="PROPN">Praze</tok>
            <tok lemma="." pos="PUNCT">.</tok>
        </s>
    </text>
</teiCorpus>
"""


class TestTEITOKDispatch:
    def _make_teitok(self, tmp_path):
        p = tmp_path / "doc.teitok.xml"
        p.write_text(TEITOK_MOCK_CONTENT, encoding="utf-8")
        return p

    def test_extract_surface_text_teitok(self, tmp_path):
        from keywords import _extract_surface_text
        p = self._make_teitok(tmp_path)

        # Should dynamically call teitok_read.read_teitok_text and stitch the tokens
        text = _extract_surface_text(str(p))
        assert text == "Archeologický výzkum v Praze ."

    def test_extract_lemmas_teitok(self, tmp_path):
        from keywords import _extract_lemmas
        p = self._make_teitok(tmp_path)

        # Should dynamically extract strictly NOUN/PROPN/ADJ lemmas mapped to lowercase
        lemmas = _extract_lemmas(str(p))
        assert lemmas == ["archeologický", "výzkum", "praha"]


class TestCSVSort:
    def _make_csv(self, tmp_path, lines):
        p = tmp_path / "test.csv"
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return p

    def _read_first_col(self, p):
        with open(p, "r", encoding="utf-8") as fh:
            return [row[0] for row in csv.reader(fh)]

    def test_sorts_rows_by_first_column(self, tmp_path):
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


def test_keybert_model_raises_backend_error_on_missing_torch(monkeypatch):
    from keywords import _get_keybert_model, KeywordBackendError
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "torch":
            raise ImportError("No module named torch")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(KeywordBackendError, match="PyTorch import failed"):
        _get_keybert_model("some-model")