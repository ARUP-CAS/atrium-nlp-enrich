import pytest

torch = pytest.importorskip("torch")
transformers = pytest.importorskip("transformers")

from api_util.call_nametag import _get_ne_suffix, process_data  # noqa: E402


def test_get_ne_suffix_ontonotes():
    # Standard single tags
    assert _get_ne_suffix("B-PERSON") == "PERSON"
    assert _get_ne_suffix("I-ORG") == "ORG"

    # Out tag
    assert _get_ne_suffix("O") == ""

    # Complex/Nested overlapping tags
    # Ensures the parser still drops the 'B-' and 'I-' while keeping the structure intact
    assert _get_ne_suffix("B-GPE|I-FAC") == "GPE|FAC"
    assert _get_ne_suffix("I-DATE|O") == "DATE|"
    assert _get_ne_suffix("B-WORK_OF_ART|I-WORK_OF_ART") == "WORK_OF_ART|WORK_OF_ART"

def test_process_data_smoke():
    result = process_data({"text": "Hello"})
    assert result == {"text": "Hello"}
