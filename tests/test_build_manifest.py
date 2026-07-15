"""
tests/test_build_manifest_row.py – Unit tests for api_util/build_manifest_row.py,
the CSV/XLSX → ordered-text extractor used to build the enrichment manifest.
"""

import csv

import pytest

from api_util.build_manifest_row import _read_csv, get_sorted_text_and_page_count


def _write_csv(tmp_path, rows, header=("page_num", "line_num", "text")):
    path = tmp_path / "in.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)
    return str(path)


def test_read_csv_parses_rows(tmp_path):
    entries = _read_csv(_write_csv(tmp_path, [(1, 1, "first"), (1, 2, "second")]))
    assert [e["text"] for e in entries] == ["first", "second"]
    assert entries[0]["p"] == 1 and entries[0]["l"] == 1


def test_read_csv_skips_empty_text(tmp_path):
    entries = _read_csv(_write_csv(tmp_path, [(1, 1, ""), (1, 2, "kept")]))
    assert [e["text"] for e in entries] == ["kept"]


def test_read_csv_non_numeric_indices_default_to_zero(tmp_path):
    entry = _read_csv(_write_csv(tmp_path, [("x", "y", "txt")]))[0]
    assert entry["p"] == 0 and entry["l"] == 0


def test_sorted_text_orders_by_page_then_line(tmp_path):
    path = _write_csv(tmp_path, [(2, 1, "page2"), (1, 2, "p1l2"), (1, 1, "p1l1")])
    text, page_count = get_sorted_text_and_page_count(path)
    assert text.splitlines() == ["p1l1", "p1l2", "page2"]
    assert page_count == 2


def test_sorted_text_unknown_extension_returns_none(tmp_path):
    path = tmp_path / "x.json"
    path.write_text("{}", encoding="utf-8")
    assert get_sorted_text_and_page_count(str(path)) == (None, 0)


def test_sorted_text_empty_file_returns_none(tmp_path):
    assert get_sorted_text_and_page_count(_write_csv(tmp_path, [])) == (None, 0)


def test_read_xlsx_reads_text_column(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    from api_util.build_manifest_row import _read_xlsx

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["page_num", "line_num", "text"])
    ws.append([1, 1, "alpha"])
    ws.append([1, 2, "beta"])
    xlsx_path = tmp_path / "in.xlsx"
    wb.save(xlsx_path)

    entries = _read_xlsx(str(xlsx_path))
    assert [e["text"] for e in entries] == ["alpha", "beta"]
