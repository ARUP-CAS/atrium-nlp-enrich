import json
from unittest.mock import MagicMock, patch

import pytest

import api_util.document_hook as hook_module
from api_util.document_hook import run_document_hook
from atrium_document import DocumentRecord


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


#: A minimal record that satisfies atrium_document.schema.json, used as the mocked
#: ``to_dict()`` payload below. The hook validates its own output before finalize()
#: (atrium-project#10, D4), so a bare MagicMock — which is not a JSON object at all —
#: would fail that gate for a reason that has nothing to do with the test.
_VALID_STUB_RECORD = {"schema_version": "1.0", "record_type": "atrium-document", "doc_id": "CTX0"}


@pytest.fixture
def mock_document_record():
    with patch("api_util.document_hook.DocumentRecord") as mock_record:
        # spec'd on the real class, NOT a bare MagicMock: unittest.mock refuses to
        # auto-create any attribute whose name starts with "assert" (its typo guard for
        # assert_called_once and friends), and the hook now calls
        # assert_fields_survived() after every merge_block() (atrium-project#10, D8).
        # A spec'd mock knows the name is a real method, so the guard steps aside — and
        # it additionally fails these tests if the hook ever calls a method
        # DocumentRecord does not have.
        mock_instance = MagicMock(spec=DocumentRecord)
        mock_instance.to_dict.return_value = dict(_VALID_STUB_RECORD)
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
    # ABSENT, not present-and-null: `entities[].type_cnec` is typed `{"type": "string"}`
    # in atrium_document.schema.json with no null member, so writing the tagset that does
    # not apply as an explicit null made every record with an entity fail the D4 gate.
    assert "type_cnec" not in entities[0]
    assert entities[0]["type_teitok"] == "LOC"
    # `pid` is llm-enrich's field (atrium-project#10, D10) — the hook must not hand it to
    # merge_block() at all. It was silently filtered out before, and now trips
    # assert_fields_survived() instead, so its absence here is load-bearing.
    assert "pid" not in entities[0]
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
    assert "type_onto" not in entities[0]  # see the ONTO test above (#10 D4)
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


@patch("api_util.document_hook.group_ner_spans")
@patch("api_util.document_hook.parse_and_align_conllu")
def test_every_merged_block_is_followed_by_a_survival_assertion(
    mock_parse, mock_group, mock_document_record
):
    """The D8 call sites, not just the mechanism (atrium-project#10).

    Deleting either ``assert_fields_survived()`` call would otherwise go unnoticed: with
    D10's ``pid`` placeholder gone the hook hands over no out-of-grant field, so the
    assertion is correctly silent on real output — and a guard that never speaks is one a
    future edit removes as dead code. This pins the pairing itself: every merge is
    immediately followed by its assertion, on the same block, over the same rows.
    """
    _, mock_doc_instance = mock_document_record
    tok = _tok("Prague", "Prague", "B-LOC", 1, "line_1", 10, 10, 50, 20)
    mock_parse.return_value = {"sentences": [{"tokens": [tok]}]}
    mock_group.return_value = [{"kind": "name", "code": "LOC", "tokens": [tok]}]

    run_document_hook(
        doc_id="CTX000000001",
        teitok_path="",
        conllu_path="",
        baseline_json=None,
        out_json="out.json",
        run_id="run",
        paradata_ref="ref",
        license_detail={},
    )

    sequence = [
        (call[0], call.args[0])
        for call in mock_doc_instance.method_calls
        if call[0] in ("merge_block", "assert_fields_survived")
    ]
    assert sequence == [
        ("merge_block", "entities"),
        ("assert_fields_survived", "entities"),
        ("merge_block", "pages"),
        ("assert_fields_survived", "pages"),
    ]

    # The same rows object both times — asserting survival of a different list would
    # check nothing at all.
    for block in ("entities", "pages"):
        merged = next(
            c.args[1] for c in mock_doc_instance.merge_block.call_args_list if c.args[0] == block
        )
        asserted = next(
            c.args[1]
            for c in mock_doc_instance.assert_fields_survived.call_args_list
            if c.args[0] == block
        )
        assert asserted is merged


# ── Layer D validation gate + field-survival assertion (atrium-project#10 D4/D8/D10) ──
#
# Everything above mocks DocumentRecord away, which is right for the entity-extraction
# logic and structurally blind to the two guarantees run_document_hook() now enforces: a
# MagicMock validates nothing and filters nothing, so "the call is there" is all such a
# test can ever say. D4 exists precisely because that distinction was never tested
# anywhere in the ecosystem — so the tests below drive the REAL DocumentRecord and the
# REAL atrium_document.schema.json against tmp_path.


@pytest.fixture(autouse=True)
def _reset_validation_warning_latch():
    """``_VALIDATION_UNAVAILABLE_WARNED`` is a module-global latch so a corpus run says
    "validation is disabled" once rather than once per document (D4). Reset it around
    every test, or whichever test happens to run first silently disarms the assertion in
    the one that counts the warning."""
    hook_module._VALIDATION_UNAVAILABLE_WARNED = False
    yield
    hook_module._VALIDATION_UNAVAILABLE_WARNED = False


_VALID_BASELINE = {
    "schema_version": "1.0",
    "record_type": "atrium-document",
    "doc_id": "CTX000000001",
    "pages": [{"page": "1", "page_index": 1, "quality_score": 0.9}],
    # An object keyed by page label, not a list of rows — page-classification's block, in
    # the shape the schema actually declares.
    "page_categories": {"1": "Drawing"},
}

#: `pages[].quality_score` is `{"minimum": 0, "maximum": 1}`, so 5.0 is a real schema
#: violation in a field that belongs to ANOTHER tool (alto-postprocess) — the inherited
#: defect D4's warn-not-refuse policy is written for.
_INVALID_BASELINE = {
    "schema_version": "1.0",
    "record_type": "atrium-document",
    "doc_id": "CTX000000001",
    "pages": [{"page": "1", "quality_score": 5.0}],
}


def _span_token(char_start=0, char_end=5):
    """One token as ``group_ner_spans`` hands it back, already positioned.

    Deliberately NOT one of the tokens ``parse_and_align_conllu`` returned, so
    ``_augment_tokens_with_position()`` never overwrites these offsets. That is how a
    positional field nlp-enrich OWNS can be driven to a value the schema rejects without
    mocking the validator away — the gate under test has to be the real one.
    """
    return {
        "form": "Praha",
        "lemma": "Praha",
        "ner": "B-LOC",
        "space_after": True,
        "_page": "1",
        "_line": 1,
        "_char_start": char_start,
        "_char_end": char_end,
    }


def _run_real_hook(tmp_path, span_token, baseline=None, doc_id="CTX000000001"):
    """Run the hook against the real DocumentRecord; return the out path (written or not)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    baseline_path = None
    if baseline is not None:
        baseline_path = tmp_path / f"baseline_{doc_id}.document.json"
        baseline_path.write_text(json.dumps(baseline), encoding="utf-8")

    out_json = tmp_path / "out" / f"{doc_id}.document.json"
    aligned = _tok("Praha", "Praha", "B-LOC", 1, "line_1", 10, 10, 50, 20)

    with (
        patch(
            "api_util.document_hook.parse_and_align_conllu",
            return_value={"sentences": [{"tokens": [aligned]}]},
        ),
        patch(
            "api_util.document_hook.group_ner_spans",
            return_value=[{"kind": "name", "code": "LOC", "tokens": [span_token]}],
        ),
    ):
        run_document_hook(
            doc_id=doc_id,
            teitok_path=f"TEITOK/{doc_id}.teitok.xml",
            conllu_path=f"UDP/{doc_id}.conllu",
            baseline_json=str(baseline_path) if baseline_path else None,
            out_json=str(out_json),
            run_id="260805-120000",
            paradata_ref="paradata/260805-120000_nlp-enrich.json",
            license_detail={"effective_license": "CC BY-NC-SA 4.0"},
        )
    return out_json


def test_valid_contribution_is_written_and_validates(tmp_path):
    """The happy path, and the premise of every gate test below: with the real schema in
    play, nlp-enrich's own output passes — so a red gate test means a real regression, not
    a hook that never validated cleanly in the first place."""
    jsonschema = pytest.importorskip("jsonschema")
    from atrium_document import load_schema

    out_json = _run_real_hook(tmp_path, _span_token(), baseline=_VALID_BASELINE)

    assert out_json.exists()
    record = json.loads(out_json.read_text(encoding="utf-8"))
    jsonschema.validate(record, load_schema())
    # Rule 2/6: an upstream block nlp-enrich does not own passes through untouched.
    assert record["page_categories"] == _VALID_BASELINE["page_categories"]
    assert record["entities"][0]["surface"] == "Praha"
    assert record["pages"][0]["teitok_surface"] == "CTX000000001.surface1"
    assert record["pages"][0]["quality_score"] == 0.9  # alto-postprocess's field survives


def test_invalid_own_output_raises_and_writes_nothing(tmp_path):
    """D4, the half that matters: a record nlp-enrich itself got wrong is REFUSED, and
    refused before it lands. ``char_span`` items are `{"minimum": 0}`, so a negative
    offset is exactly the arithmetic slip the gate exists to catch.

    ``DocumentRecord.__exit__`` only finalises when the body left without an exception,
    which is why the gate is called inside the ``with`` — assert the absence of the file,
    not merely the exception, because "raised after writing" would satisfy the latter.
    """
    jsonschema = pytest.importorskip("jsonschema")

    with pytest.raises(jsonschema.ValidationError):
        _run_real_hook(tmp_path, _span_token(char_start=-1))

    out_dir = tmp_path / "out"
    assert not (out_dir / "CTX000000001.document.json").exists()
    # No stray write-then-rename leftover either: finalize() was never entered at all.
    assert list(out_dir.glob("*.tmp")) == [] if out_dir.exists() else True


def test_invalid_baseline_only_warns_and_still_accretes(tmp_path, capsys):
    """D4, the other half: an upstream tool's invalid record must not stall this stage.
    Refusing here would turn one bad record into a stopped pipeline, and rule 6 already
    commits to carrying unknown content through — so this warns, names the schema error,
    and writes."""
    pytest.importorskip("jsonschema")

    out_json = _run_real_hook(tmp_path, _span_token(), baseline=_INVALID_BASELINE)

    assert out_json.exists()
    err = capsys.readouterr().err
    assert "inherited baseline" in err
    assert "does not validate" in err
    # The named schema error, not a bare "invalid": without it the operator cannot tell
    # which upstream tool to go and fix.
    assert "quality_score" in err or "5" in err


def test_invalid_baseline_downgrades_own_output_refusal_to_a_warning(tmp_path, capsys):
    """The interaction of the two halves. Once the baseline is known-invalid, refusing to
    emit would throw away this stage's work over a defect it inherited — so the own-output
    gate degrades to a warning that says so, and the record is still written."""
    pytest.importorskip("jsonschema")

    out_json = _run_real_hook(
        tmp_path, _span_token(char_start=-1), baseline=_INVALID_BASELINE
    )

    assert out_json.exists()
    err = capsys.readouterr().err
    assert "inherited baseline" in err
    assert "Emitting it anyway" in err


def test_missing_jsonschema_warns_once_loudly_and_keeps_going(tmp_path, capsys, monkeypatch):
    """``validate_document()`` raises RuntimeError rather than passing when ``jsonschema``
    is absent — deliberately, so a gate that became a no-op is distinguishable from a
    passing one. The hook must therefore say so LOUDLY and exactly ONCE per run (api_4_stats
    walks a whole corpus), and must still produce output: rule 3 does not let a missing
    optional dependency stop a standalone run.

    Patched rather than uninstalled — requirements.txt declares jsonschema, so this
    degraded branch is unreachable in a correctly-provisioned venv and testable only here.
    """

    def _no_jsonschema(record):
        raise RuntimeError("jsonschema is not installed, so the record cannot be validated.")

    monkeypatch.setattr(hook_module, "validate_document_record", _no_jsonschema)

    first = _run_real_hook(tmp_path, _span_token(), baseline=_VALID_BASELINE)
    second = _run_real_hook(
        tmp_path / "second", _span_token(), baseline=_VALID_BASELINE, doc_id="CTX000000002"
    )

    assert first.exists() and second.exists()
    err = capsys.readouterr().err
    assert err.count("schema validation is DISABLED") == 1
    assert "DEGRADED" in err
    assert "jsonschema" in err


def test_out_of_grant_field_raises_instead_of_vanishing(tmp_path):
    """D8 + D10 in one: ``pid`` is llm-enrich's field, so merge_block() filters it out in
    silence — no wrong value ever persisted, which is why nobody noticed the hook was
    handing it over. ``assert_fields_survived()`` turns that silent filtering into a
    failure at dev time, and this pins BOTH halves: the drop happens, and the assertion
    catches it."""
    rows = [
        {
            "page": "1",
            "line": 1,
            "char_span": [0, 5],
            "surface": "Praha",
            "pid": {"wikidata": None, "geonames": None, "aat": None, "amcr": None},
        }
    ]

    doc = DocumentRecord("CTX000000001", "nlp-enrich", out_dir=str(tmp_path))
    doc.merge_block("entities", rows)

    # Silent by design: the row is there, the unauthorised field is not.
    assert doc.get_block("entities")[0]["surface"] == "Praha"
    assert "pid" not in doc.get_block("entities")[0]
    assert doc.dropped_fields("entities") == {"entities": ["pid"]}

    with pytest.raises(RuntimeError, match="pid"):
        doc.assert_fields_survived("entities", rows)


def test_declared_grant_covers_every_field_the_hook_writes(tmp_path):
    """The assertion in the hook is only useful if it passes on real output — otherwise
    the first real run is a false alarm and somebody deletes it. Runs the real hook and
    re-asserts survival for both blocks it merges."""
    out_json = _run_real_hook(tmp_path, _span_token(), baseline=_VALID_BASELINE)
    record = json.loads(out_json.read_text(encoding="utf-8"))

    entity = record["entities"][0]
    for field in ("surface", "lemma", "type_onto", "type_teitok", "char_span", "teitok_ref"):
        assert field in entity, f"{field} was dropped by merge_block — grant is wrong"
    assert "teitok_surface" in record["pages"][0]
