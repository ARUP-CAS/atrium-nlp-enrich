from unittest.mock import MagicMock, patch

import pytest

from api_util.document_hook import run_document_hook


def _tok(form, lemma, ner, page_idx, line_id, left, top, right, bottom, space_after=True):
    return {
        "form": form,
        "lemma": lemma,
        "ner": ner,
        "space_after": space_after,
        "_bbox": {
            "left": left, "top": top, "right": right, "bottom": bottom,
            "page_idx": page_idx, "line_id": line_id,
            "block_id": "b1", "line_bbox": "0 0 100 30",
        },
    }


@pytest.fixture
def mock_document_record():
    with patch("api_util.document_hook.DocumentRecord") as mock_record:
        mock_instance = MagicMock()
        mock_record.open.return_value.__enter__.return_value = mock_instance
        yield mock_record, mock_instance


def _merged_entities(mock_doc_instance):
    for call in mock_doc_instance.merge_block.call_args_list:
        if call[0][0] == "entities":
            return call[0][1]
    raise AssertionError("merge_block('entities', ...) was never called")


@patch("api_util.document_hook.group_ner_spans")
@patch("api_util.document_hook.parse_and_align_conllu")
def test_document_hook_onto_extraction(mock_parse, mock_group, mock_document_record):
    mock_record_class, mock_doc_instance = mock_document_record

    # Mocking standard ONTO model output: parse_and_align_conllu() returns sentences of
    # tokens (each ALTO-aligned via "_bbox"), matching the real teitok_alto.py contract.
    tok = _tok("Prague", "Prague", "B-LOC", 1, "line_1", 10, 10, 50, 20)
    mock_parse.return_value = {"sentences": [{"tokens": [tok]}]}
    mock_group.return_value = [{"kind": "name", "code": "LOC", "tokens": [tok]}]

    run_document_hook(
        doc_id="CTX000000001",
        teitok_path="/fake/path/CTX000000001.teitok.xml",
        conllu_path="/fake/path/CTX000000001.conllu",
        baseline_json=None,
        out_json="out.json",
        run_id="test-run-123",
        paradata_ref="paradata.json",
        license_detail={"effective_license": "CC-BY"},
        alto_path="/fake/path/CTX000000001.alto.xml",
    )

    # Verify accretion initialization
    mock_record_class.open.assert_called_once_with(
        "CTX000000001", "nlp-enrich", baseline=None, run_id="test-run-123", paradata_ref="paradata.json"
    )

    entities = _merged_entities(mock_doc_instance)
    assert len(entities) == 1
    assert entities[0]["surface"] == "Prague"
    assert entities[0]["type_onto"] == "LOC"
    assert entities[0]["type_cnec"] is None
    assert entities[0]["type_teitok"] == "LOC"
    assert entities[0]["teitok_ref"] == "CTX000000001.name1"
    assert entities[0]["bbox"] == [10.0, 10.0, 50.0, 20.0]
    assert entities[0]["page"] == "1"
    assert entities[0]["line"] == 1
    assert entities[0]["char_span"] == [0, 6]


@patch("api_util.document_hook.group_ner_spans")
@patch("api_util.document_hook.parse_and_align_conllu")
def test_document_hook_cnec_extraction(mock_parse, mock_group, mock_document_record):
    mock_record_class, mock_doc_instance = mock_document_record

    # Mocking legacy CNEC model output: two tokens forming one span on the same line.
    tok1 = _tok("Karel", "Karel", "B-p", 2, "line_5", 10, 10, 40, 20)
    tok2 = _tok("Novák", "Novák", "I-p", 2, "line_5", 45, 10, 80, 20)
    mock_parse.return_value = {"sentences": [{"tokens": [tok1, tok2]}]}
    mock_group.return_value = [{"kind": "name", "code": "p", "tokens": [tok1, tok2]}]

    run_document_hook(
        doc_id="CTX000000002",
        teitok_path="/fake/path.xml",
        conllu_path="/fake/path.conllu",
        baseline_json="base.json",
        out_json="out.json",
        run_id="test-run-456",
        paradata_ref="para2.json",
        license_detail=None,
        alto_path="/fake/path.alto.xml",
    )

    entities = _merged_entities(mock_doc_instance)
    assert len(entities) == 1
    assert entities[0]["surface"] == "Karel Novák"
    assert entities[0]["type_onto"] is None
    assert entities[0]["type_cnec"] == "p"
    assert entities[0]["type_teitok"] == "PER"
    assert entities[0]["bbox"] == [10.0, 10.0, 80.0, 20.0]
    assert entities[0]["page"] == "2"
    assert entities[0]["char_span"] == [0, len("Karel Novák")]


@patch("api_util.document_hook.group_ner_spans")
@patch("api_util.document_hook.parse_and_align_conllu")
def test_document_hook_skips_plain_spans(mock_parse, mock_group, mock_document_record):
    """group_ner_spans groups every token, not just entities — "plain" spans must
    never turn into entity records."""
    mock_record_class, mock_doc_instance = mock_document_record

    plain_tok = _tok("byl", "být", "O", 1, "line_1", 5, 5, 20, 15)
    mock_parse.return_value = {"sentences": [{"tokens": [plain_tok]}]}
    mock_group.return_value = [{"kind": "plain", "tokens": [plain_tok]}]

    run_document_hook(
        doc_id="CTX000000003",
        teitok_path="",
        conllu_path="",
        baseline_json=None,
        out_json="",
        run_id="run",
        paradata_ref="ref",
        license_detail={},
    )

    assert _merged_entities(mock_doc_instance) == []


@patch("api_util.document_hook.parse_and_align_conllu")
def test_document_hook_omits_lines_by_default(mock_parse, mock_document_record):
    mock_record_class, mock_doc_instance = mock_document_record
    mock_parse.return_value = None  # e.g. unreadable CoNLL-U — degrade gracefully

    run_document_hook(
        doc_id="CTX000000004",
        teitok_path="",
        conllu_path="",
        baseline_json=None,
        out_json="",
        run_id="run",
        paradata_ref="ref",
        license_detail={},
        include_lines=False,
    )

    # Assert merge_block is called for "pages", but not for "lines"
    merged_blocks = [call[0][0] for call in mock_doc_instance.merge_block.call_args_list]
    assert "pages" in merged_blocks
    assert "lines" not in merged_blocks
