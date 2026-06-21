"""
tests/test_llm_utils.py
=======================
Tests for the pure helpers carved out of the llm_utils.py monolith.
"""

from pydantic import BaseModel, Field

from llm_utils import _should_process_line, get_context_window, validate_llm_output


class DummyEnrichment(BaseModel):
    teater_category: str
    # Enforce a max of 1.0 so that 1.5 triggers the ValidationError fallback block
    confidence_score: float = Field(..., le=1.0)
    extracted_keywords_cs: list[str]

    def category_name(self):
        return self.teater_category


def test_validate_llm_output_success():
    """Test standard valid JSON parsing."""
    valid_json = (
        '{"teater_category": "Osoby", "confidence_score": 0.95, "extracted_keywords_cs": ["Jan"]}'
    )
    result = validate_llm_output(valid_json, DummyEnrichment, "doc1", 1, 1)
    assert result["teater_category"] == "Osoby"
    assert result["confidence_score"] == 0.95


def test_validate_llm_output_fallback_recovery():
    """Test recovery when strict JSON validation fails but fallback parsing works."""
    # Score is 1.5, which fails the strict Field(le=1.0) check.
    # The helper bounds it to 1.0 during the fallback sequence.
    recoverable_json = (
        '{"teater_category": "Místa", "confidence_score": 1.5, "extracted_keywords_cs": ["Praha"]}'
    )
    result = validate_llm_output(recoverable_json, DummyEnrichment, "doc1", 1, 1)
    assert result["confidence_score"] == 1.0


def test_validate_llm_output_meta_text_clearing():
    """Test that meta-text correctly strips out keywords."""
    meta_json = '{"teater_category": "Nerelevantní (meta-text)", "confidence_score": 0.9, "extracted_keywords_cs": ["fake"]}'
    result = validate_llm_output(meta_json, DummyEnrichment, "doc1", 1, 1)
    assert result["extracted_keywords_cs"] == []


def test_should_process_line_noise_rejection():
    """Test that the quality filter accurately drops low-quality and 'Trash' lines."""
    should_proc, _ = _should_process_line("Some text", "Empty", 0.30, True, 3, 8, 0.40)
    assert not should_proc

    should_proc, _ = _should_process_line("Good length text", "Trash", 0.80, True, 3, 8, 0.40)
    assert not should_proc


def test_get_context_window_formatting():
    """Verify that context windows correctly wrap the target line with <target_line>."""
    rows = [
        {"text": "Line 1", "page_num": 1, "line_num": 1, "categ": ""},
        {"text": "Line 2", "page_num": 1, "line_num": 2, "categ": ""},
        {"text": "Line 3", "page_num": 1, "line_num": 3, "categ": ""},
    ]

    context = get_context_window(rows, center_idx=1, window=1)
    assert "<target_line> >>> [P1 L2] Line 2 </target_line>" in context
    assert "[P1 L1] Line 1" in context
    assert "[P1 L3] Line 3" in context
