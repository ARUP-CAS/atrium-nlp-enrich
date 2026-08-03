"""Tests for api_util/validate_teitok_xml.py (issue #28).

Run from the repo root: pytest tests/test_validate_teitok_xml.py -v
"""

import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from api_util.validate_teitok_xml import DEFAULT_SCHEMA, validate_directory  # noqa: E402

FIXTURES = REPO_ROOT / "data_samples" / "TEITOK"


def test_schema_file_exists():
    assert DEFAULT_SCHEMA.is_file(), f"schema not found at {DEFAULT_SCHEMA}"


def test_valid_fixture_passes(tmp_path):
    shutil.copy(FIXTURES / "CTX_valid.teitok.xml", tmp_path)
    assert validate_directory(tmp_path) is True


def test_no_alto_fallback_fixture_passes(tmp_path):
    """Documents generated without an ALTO source (no bboxes, no
    <facsimile>) must still satisfy the contract."""
    shutil.copy(FIXTURES / "CTX_no_alto.teitok.xml", tmp_path)
    assert validate_directory(tmp_path) is True


def test_invalid_fixture_fails(tmp_path):
    shutil.copy(FIXTURES / "CTX_invalid.teitok.xml", tmp_path)
    assert validate_directory(tmp_path) is False


def test_invalid_fixture_reports_filename_and_diagnostics(tmp_path, capsys):
    shutil.copy(FIXTURES / "CTX_invalid.teitok.xml", tmp_path)
    validate_directory(tmp_path)
    captured = capsys.readouterr()
    assert "CTX_invalid.teitok.xml" in captured.err
    assert "unexpectedElement" in captured.err


def test_mixed_directory_fails_and_names_only_the_bad_file(tmp_path, capsys):
    """A gate over a whole run must fail if *any* document is malformed,
    while still reporting which one(s)."""
    shutil.copy(FIXTURES / "CTX_valid.teitok.xml", tmp_path)
    shutil.copy(FIXTURES / "CTX_no_alto.teitok.xml", tmp_path)
    shutil.copy(FIXTURES / "CTX_invalid.teitok.xml", tmp_path)
    ok = validate_directory(tmp_path)
    captured = capsys.readouterr()
    assert ok is False
    assert "CTX_invalid.teitok.xml" in captured.err
    assert "2/3 documents passed" in captured.err


def test_empty_directory_fails(tmp_path):
    assert validate_directory(tmp_path) is False


def test_missing_directory_fails(tmp_path):
    assert validate_directory(tmp_path / "does_not_exist") is False


def test_nested_layout_is_found_via_rglob(tmp_path):
    """TEITOK_OUTPUT_DIR can be flat or nested per-document; rglob must
    catch both."""
    nested = tmp_path / "some_doc_subdir"
    nested.mkdir()
    shutil.copy(FIXTURES / "CTX_valid.teitok.xml", nested)
    assert validate_directory(tmp_path) is True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
