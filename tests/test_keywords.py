"""
tests/test_keywords.py
======================
Unit tests for the pure-Python functions in ``keywords.py``.
"""

import csv
from collections import Counter
import pytest

from keywords import _sort_csv_file


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