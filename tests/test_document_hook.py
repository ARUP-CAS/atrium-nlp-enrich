from unittest.mock import MagicMock, patch

import pytest

from api_util.document_hook import run_document_hook


@pytest.fixture
def mock_document_record():
    with patch("api_util.document_hook.DocumentRecord") as mock_record:
        mock_instance = MagicMock()
        mock_record.open.return_value.__enter__.return_value = mock_instance
        yield mock_record, mock_instance


@patch("api_util.document_hook.group_ner_spans")
@patch("api_util.document_hook.parse_and_align_conllu")
def test_document_hook_onto_extraction(mock_parse, mock_group, mock_document_record):
    mock_record_class, mock_doc_instance = mock_document_record

    # Mocking standard ONTO model output
    mock_parse.return_value = [
        {"form": "Prague", "lemma": "Prague", "misc": "NER=B-LOC", "page": "1", "line": 1, "bbox": [10, 10, 50, 20]}
    ]
    mock_group.return_value = [
        {
            "type": "LOC",
            "tokens": [{"form": "Prague", "lemma": "Prague", "page": "1", "line": 1, "bbox": [10, 10, 50, 20]}]
        }
    ]

    run_document_hook(
        doc_id="CTX000000001",
        teitok_path="/fake/path/CTX000000001.teitok.xml",
        conllu_path="/fake/path/CTX000000001.conllu",
        baseline_json=None,
        out_json="out.json",
        run_id="test-run-123",
        paradata_ref="paradata.json",
        license_detail={"effective_license": "CC-BY"},
        include_lines=False
    )

    # Verify accretion initialization
    mock_record_class.open.assert_called_once_with(
        "CTX000000001", "nlp-enrich", baseline=None, run_id="test-run-123", paradata_ref="paradata.json"
    )

    # Verify entity block payload
    mock_doc_instance.set_block.assert_called()
    call_args = mock_doc_instance.set_block.call_args[0]
    assert call_args[0] == "entities"

    entities = call_args[1]
    assert len(entities) == 1
    assert entities[0]["surface"] == "Prague"
    assert entities[0]["type_onto"] == "LOC"
    assert entities[0]["type_cnec"] is None
    assert entities[0]["type_teitok"] == "LOC"
    assert entities[0]["teitok_ref"] == "CTX000000001.name1"
    assert entities[0]["bbox"] == [10.0, 10.0, 50.0, 20.0]


@patch("api_util.document_hook.group_ner_spans")
@patch("api_util.document_hook.parse_and_align_conllu")
def test_document_hook_cnec_extraction(mock_parse, mock_group, mock_document_record):
    mock_record_class, mock_doc_instance = mock_document_record

    # Mocking legacy CNEC model output
    mock_parse.return_value = [
        {"form": "Karel", "lemma": "Karel", "misc": "NE=p|NER=B-p", "page": "2", "line": 5, "bbox": [10, 10, 40, 20]},
        {"form": "Novák", "lemma": "Novák", "misc": "NE=p|NER=I-p", "page": "2", "line": 5, "bbox": [45, 10, 80, 20]}
    ]
    mock_group.return_value = [
        {
            "type": "p",
            "tokens": [
                {"form": "Karel", "lemma": "Karel", "page": "2", "line": 5, "bbox": [10, 10, 40, 20]},
                {"form": "Novák", "lemma": "Novák", "page": "2", "line": 5, "bbox": [45, 10, 80, 20]}
            ]
        }
    ]

    run_document_hook(
        doc_id="CTX000000002",
        teitok_path="/fake/path.xml",
        conllu_path="/fake/path.conllu",
        baseline_json="base.json",
        out_json="out.json",
        run_id="test-run-456",
        paradata_ref="para2.json",
        license_detail=None,
        include_lines=False
    )

    mock_doc_instance.set_block.assert_called()
    entities = mock_doc_instance.set_block.call_args[0][1]

    assert len(entities) == 1
    assert entities[0]["surface"] == "Karel Novák"
    assert entities[0]["type_onto"] is None
    assert entities[0]["type_cnec"] == "p"
    assert entities[0]["type_teitok"] == "PER"
    assert entities[0]["bbox"] == [10.0, 10.0, 80.0, 20.0]


@patch("api_util.document_hook.parse_and_align_conllu")
def test_document_hook_omits_lines_by_default(mock_parse, mock_document_record):
    mock_record_class, mock_doc_instance = mock_document_record
    mock_parse.return_value = []

    run_document_hook(
        doc_id="CTX000000003",
        teitok_path="",
        conllu_path="",
        baseline_json=None,
        out_json="",
        run_id="run",
        paradata_ref="ref",
        license_detail={},
        include_lines=False
    )

    # Assert merge_block is called for "pages", but not for "lines"
    merged_blocks = [call[0][0] for call in mock_doc_instance.merge_block.call_args_list]
    assert "pages" in merged_blocks
    assert "lines" not in merged_blocks
