"""
tests/test_document_hook.py — real (non-mocked) integration tests for
api_util/document_hook.py.

The previous version of this suite mocked both `parse_and_align_conllu` and
`group_ner_spans` with a *fictional* return shape (flat token dicts carrying
"page"/"line"/"bbox" keys directly, and span dicts carrying a "type" key) that
does not match what the real functions in teitok_alto.py actually return
(`{"sentences": [...]}` and `{"kind": "name"/"plain", "tokens": [...], "code": ...}`
respectively). Because of that, the mocked tests passed while the real hook
crashed on any real input (KeyError on `tokens[i]` for an int i against a dict,
then KeyError on `span["type"]` which is never set) and never actually wrote
entities anywhere. These tests exercise the real parse/align/group pipeline
against a small real CoNLL-U + ALTO pair, and read the record back off disk —
no DocumentRecord/parse mocking at all.
"""

import json

import pytest

from api_util.document_hook import run_document_hook
from atrium_document import load_document

ALTO_XML = """<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns="http://www.loc.gov/standards/alto/ns-v3#">
  <Description>
    <MeasurementUnit>pixel</MeasurementUnit>
  </Description>
  <Layout>
    <Page ID="Page1" WIDTH="1000" HEIGHT="1000">
      <PrintSpace HPOS="0" VPOS="0" WIDTH="1000" HEIGHT="1000">
        <TextBlock ID="block1" HPOS="0" VPOS="0" WIDTH="1000" HEIGHT="200">
          <TextLine ID="line1" HPOS="0" VPOS="0" WIDTH="900" HEIGHT="30">
            <String CONTENT="Karel" HPOS="0" VPOS="0" WIDTH="80" HEIGHT="30"/>
            <String CONTENT="Novák" HPOS="90" VPOS="0" WIDTH="80" HEIGHT="30"/>
            <String CONTENT="byl" HPOS="180" VPOS="0" WIDTH="50" HEIGHT="30"/>
            <String CONTENT="archeolog" HPOS="240" VPOS="0" WIDTH="120" HEIGHT="30"/>
            <String CONTENT="." HPOS="365" VPOS="0" WIDTH="10" HEIGHT="30"/>
          </TextLine>
          <TextLine ID="line2" HPOS="0" VPOS="40" WIDTH="900" HEIGHT="30">
            <String CONTENT="Praha" HPOS="0" VPOS="40" WIDTH="80" HEIGHT="30"/>
            <String CONTENT="a" HPOS="90" VPOS="40" WIDTH="20" HEIGHT="30"/>
            <String CONTENT="Brno" HPOS="120" VPOS="40" WIDTH="70" HEIGHT="30"/>
            <String CONTENT="jsou" HPOS="200" VPOS="40" WIDTH="60" HEIGHT="30"/>
            <String CONTENT="města" HPOS="270" VPOS="40" WIDTH="80" HEIGHT="30"/>
            <String CONTENT="." HPOS="360" VPOS="40" WIDTH="10" HEIGHT="30"/>
          </TextLine>
        </TextBlock>
      </PrintSpace>
    </Page>
  </Layout>
</alto>
"""

CONLLU = """# generator = UDPipe 2
# udpipe_model = czech-pdt-ud-2.15
# sent_id = 1
# text = Karel Novák byl archeolog.
1\tKarel\tKarel\tPROPN\t_\t_\t3\tnsubj\t_\tNER=B-p
2\tNovák\tNovák\tPROPN\t_\t_\t1\tflat\t_\tNER=I-p
3\tbyl\tbýt\tAUX\t_\t_\t0\troot\t_\t_
4\tarcheolog\tarcheolog\tNOUN\t_\t_\t3\txcomp\t_\tSpaceAfter=No
5\t.\t.\tPUNCT\t_\t_\t3\tpunct\t_\t_

# sent_id = 2
# text = Praha a Brno jsou města.
1\tPraha\tPraha\tPROPN\t_\t_\t4\tnsubj\t_\tNER=B-LOC
2\ta\ta\tCCONJ\t_\t_\t3\tcc\t_\t_
3\tBrno\tBrno\tPROPN\t_\t_\t1\tconj\t_\tNER=B-LOC
4\tjsou\tbýt\tAUX\t_\t_\t0\troot\t_\t_
5\tměsta\tměsto\tNOUN\t_\t_\t4\tobj\t_\tSpaceAfter=No
6\t.\t.\tPUNCT\t_\t_\t4\tpunct\t_\t_
"""


@pytest.fixture
def alto_conllu_pair(tmp_path):
    alto_path = tmp_path / "CTX01.alto.xml"
    conllu_path = tmp_path / "CTX01.conllu"
    alto_path.write_text(ALTO_XML, encoding="utf-8")
    conllu_path.write_text(CONLLU, encoding="utf-8")
    return alto_path, conllu_path


def test_document_hook_extracts_both_tagsets_no_crash(alto_conllu_pair, tmp_path):
    alto_path, conllu_path = alto_conllu_pair
    out_json = tmp_path / "CTX01.document.json"

    run_document_hook(
        doc_id="CTX01",
        teitok_path="TEITOK/CTX01.teitok.xml",
        conllu_path=str(conllu_path),
        baseline_json=None,
        out_json=str(out_json),
        run_id="260731-000000",
        paradata_ref="paradata/260731-000000_nlp-enrich.json",
        license_detail={"effective_license": "CC BY-NC 4.0"},
        alto_path=str(alto_path),
        include_lines=False,
    )

    # P0.1 — the record must land exactly where the caller asked (not the CWD).
    assert out_json.exists()
    record = load_document(str(out_json))
    entities = record["entities"]

    # One CNEC-style PER entity (Karel Novák, line 1) + two OntoNotes-style LOC
    # entities on the SAME line (Praha, Brno, line 2).
    assert len(entities) == 3

    per = next(e for e in entities if e["surface"] == "Karel Novák")
    assert per["type_cnec"] == "p"
    assert per["type_onto"] is None
    assert per["type_teitok"] == "PER"
    assert per["page"] == "1"
    assert per["line"] == 1

    locs = [e for e in entities if e["type_onto"] == "LOC"]
    assert {e["surface"] for e in locs} == {"Praha", "Brno"}
    for e in locs:
        assert e["type_cnec"] is None
        assert e["type_teitok"] == "LOC"
        assert e["page"] == "1"
        assert e["line"] == 2

    # P0.3 — Praha and Brno share (page, line); only distinct char_span keeps
    # both rows instead of collapsing to one.
    praha, brno = (e for e in locs if e["surface"] == "Praha"), (e for e in locs if e["surface"] == "Brno")
    praha, brno = next(praha), next(brno)
    assert praha["char_span"] != brno["char_span"]
    assert praha["char_span"][0] < brno["char_span"][0]

    # pid is llm-enrich's field, not nlp-enrich's — must not be emitted here.
    assert "pid" not in per

    # pages[] gets a teitok_surface entry for the one page touched.
    assert record["pages"] == [{"page": "1", "teitok_surface": "CTX01.surface1"}]
    assert record["derived_from"]["teitok"] == "TEITOK/CTX01.teitok.xml"


def test_document_hook_merges_without_erasing_other_owners_fields(alto_conllu_pair, tmp_path):
    """P0.2 — a re-run of nlp-enrich must not wipe translator's/llm-enrich's
    field-level contributions to the same entities[] rows."""
    alto_path, conllu_path = alto_conllu_pair
    out_json = tmp_path / "CTX01.document.json"

    run_document_hook(
        doc_id="CTX01",
        teitok_path="TEITOK/CTX01.teitok.xml",
        conllu_path=str(conllu_path),
        baseline_json=None,
        out_json=str(out_json),
        run_id="run-1",
        paradata_ref="",
        license_detail=None,
        alto_path=str(alto_path),
        include_lines=False,
    )

    # Simulate the translator contributing translation_en to the Karel Novák row.
    record = json.loads(out_json.read_text(encoding="utf-8"))
    for ent in record["entities"]:
        if ent["surface"] == "Karel Novák":
            ent["translation_en"] = "Karel Novak"
    out_json.write_text(json.dumps(record), encoding="utf-8")

    # nlp-enrich re-runs against the SAME baseline (e.g. a NER model re-tag).
    run_document_hook(
        doc_id="CTX01",
        teitok_path="TEITOK/CTX01.teitok.xml",
        conllu_path=str(conllu_path),
        baseline_json=str(out_json),
        out_json=str(out_json),
        run_id="run-2",
        paradata_ref="",
        license_detail=None,
        alto_path=str(alto_path),
        include_lines=False,
    )

    after = load_document(str(out_json))
    per = next(e for e in after["entities"] if e["surface"] == "Karel Novák")
    assert per["translation_en"] == "Karel Novak"


def test_document_hook_handles_missing_conllu_gracefully(tmp_path):
    """No CoNLL-U → parse_and_align_conllu returns None → empty entities, no crash."""
    out_json = tmp_path / "CTXEMPTY.document.json"
    run_document_hook(
        doc_id="CTXEMPTY",
        teitok_path="",
        conllu_path=str(tmp_path / "does_not_exist.conllu"),
        baseline_json=None,
        out_json=str(out_json),
        run_id="run",
        paradata_ref="ref",
        license_detail={},
        alto_path=None,
        include_lines=False,
    )
    record = load_document(str(out_json))
    assert record["entities"] == []
    assert record.get("pages", []) == []
