"""
tests/test_teitok_project.py — the opt-in record projection onto TEITOK (atrium-project#70
item 2, flexiconv#1): api_util/teitok_project.py, its pipeline stage and ``POST /project_record``.

What is pinned: the encoding (pb/@ana + classDecl, textClass keywords with @resp/@scheme/@corresp),
that nothing below the header changes except pb/@ana (so every reader of the text sees the same
document), idempotence, the refusals (another document's TEITOK, an invalid input), the page
resolution order, and that the switch is off unless asked.
"""

import copy
import json
import shutil
import sys
from pathlib import Path

import pytest

pytest.importorskip("lxml")

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from api_util import teitok_project as tp  # noqa: E402
from api_util.teitok_layout import parse_teitok_layout  # noqa: E402
from api_util.teitok_read import read_teitok_rows, read_teitok_tokens  # noqa: E402
from api_util.validate_teitok_xml import validate_xml_text  # noqa: E402

SAMPLES = REPO / "data_samples" / "TEITOK"
FIXTURES = REPO / "tests" / "fixtures" / "teitok"
PROJECTED_FIXTURE = FIXTURES / "CTX_projected.teitok.xml"
WHEN = "2026-09-30"

#: The record shape the AMČR chain leaves behind: alto-postprocess pages, nlp-enrich's
#: teitok_surface, page-classification's categories, llm-enrich's enrichment items.
RECORD_1 = {
    "schema_version": "1.0",
    "doc_id": "CTX000000001",
    "source": {"filename": "CTX000000001.alto.xml"},
    "pages": [
        {
            "page": "1",
            "teitok_surface": "facs-1",
            "category": "TEXT_P",
            "category_confidence": 0.97,
        },
        {"page": "2", "teitok_surface": "facs-2", "category": "DRAW", "category_confidence": 0.81},
    ],
    "enrichment": {
        "items": [
            {
                "locator": "Hradiště u Horní Mezí",
                "page": "1",
                "teater_category": "kostel",
                "confidence_score": 0.92,
                "teater_category_ids": [
                    {"source": "amcr", "id": "HES-000021"},
                    {"source": "teater", "id": "1333"},
                ],
                "extracted_keywords_cs": ["gotický kostel"],
                "extracted_keywords_en": ["gothic church"],
            },
            {
                "locator": "keramika",
                "page": "2",
                "teater_category": "kostel",
                "confidence_score": 0.7,
                "extracted_keywords_cs": ["gotický kostel", "hradiště"],
                "extracted_keywords_en": ["hillfort"],
            },
            {
                "locator": "obsah",
                "page": "9",
                "teater_category": tp.META_TERM,
                "confidence_score": 0.99,
            },
        ]
    },
}

STATISTICAL = {
    "method": "yake",
    "params": {"method": "yake", "top_n": 5},
    "document": [("Hradiště", 1.0), ("keramika", 0.5)],
    "pages": {"pb-1": [("Hradiště", 1.0)], "pb-2": [("keramika", 1.0), ("středověk", 0.25)]},
}


def _sample(name="CTX000000001"):
    return (SAMPLES / f"{name}.teitok.xml").read_text(encoding="utf-8")


def _project(xml, record=RECORD_1, **kwargs):
    kwargs.setdefault("when", WHEN)
    return tp.project_record(xml, copy.deepcopy(record), **kwargs)


def _tree(xml):
    from lxml import etree

    return etree.fromstring(xml.encode("utf-8"))


def _body(xml):
    """Everything from <text> on, with the projection's one body attribute removed."""
    import re

    body = xml[xml.index("<text>") :]
    return re.sub(r' ana="#pcat-[^"]*"', "", body)


# ── the encoding ───────────────────────────────────────────────────────────────────────────


def test_projection_encodes_categories_keywords_and_provenance():
    out, report = _project(_sample(), statistical=STATISTICAL)
    root = _tree(out)
    pbs = {pb.get("id"): pb.get("ana") for pb in root.iter("pb")}
    assert pbs == {"pb-1": "#pcat-TEXT_P", "pb-2": "#pcat-DRAW"}

    cats = {c.get("id"): c.get("corresp") for c in root.iter("category")}
    assert cats == {
        "pcat-TEXT_P": "https://w3id.org/atrium/page-category/TEXT_P",
        "pcat-DRAW": "https://w3id.org/atrium/page-category/DRAW",
    }
    assert all(c.findtext("catDesc") for c in root.iter("category"))

    lists = {
        (k.get("resp"), k.get("scheme"), k.get("lang"), k.get("corresp")): k
        for k in root.iter("keywords")
    }
    teater = lists[("#app-llm-enrich", "#tax-amcr-teater", None, None)]
    (term,) = list(teater)
    assert term.text == "kostel"
    assert term.get("ref") == "https://api.aiscr.cz/id/HES-000021 https://teater.aiscr.cz/id/1333"
    assert term.get("cert") == "0.92"  # the highest confidence of the items naming it
    assert term.get("corresp") == "#pb-1 #pb-2"

    cs = lists[("#app-llm-enrich", None, "cs", None)]
    assert [(t.text, t.get("corresp")) for t in cs] == [
        ("gotický kostel", "#pb-1 #pb-2"),
        ("hradiště", "#pb-2"),
    ]
    assert all(t.get("cert") is None for t in cs)

    doc_list = lists[("#app-kw", "#kw-yake", None, None)]
    assert [(t.text, t.get("n"), t.get("score")) for t in doc_list] == [
        ("Hradiště", "1", "1"),
        ("keramika", "2", "0.5"),
    ]
    page_2 = lists[("#app-kw", "#kw-yake", None, "#pb-2")]
    assert [t.text for t in page_2] == ["keramika", "středověk"]

    apps = {a.get("id"): a.get("ident") for a in root.iter("application") if a.get("id")}
    assert apps == {
        "app-pc": "atrium-page-classification",
        "app-llm-enrich": "atrium-llm-enrich",
        "app-kw": "atrium-nlp-enrich-keywords",
    }
    (change,) = [c for c in root.iter("change") if c.get("type") == "enriched"]
    assert change.get("when") == WHEN and change.get("who") == "atrium-nlp-enrich"
    assert report["page_categories"] == 2 and report["teater_categories"] == 1
    assert report["statistical_keywords"] == {"document": 2, "pages": 2}
    assert validate_xml_text(out, profile="contract") == []


def test_the_meta_sentinel_is_not_a_category():
    out, report = _project(_sample())
    assert tp.META_TERM not in out
    assert report["unresolved_pages"] == []  # its page 9 is never even looked up


def test_the_writer_header_lines_are_kept_byte_for_byte():
    """Only whitespace next to the new elements changes: every line of the writer's own
    header is still in the output, unchanged."""
    original = _sample()
    out, _ = _project(original, statistical=STATISTICAL)
    header_lines = original[: original.index("<text>")].splitlines()
    out_lines = set(out.splitlines())
    changed = [ln for ln in header_lines if ln.strip() and ln not in out_lines]
    # the one-line <profileDesc> opens up to take a second child; nothing else moves
    assert changed == [
        '    <profileDesc><langUsage><language ident="cs"/></langUsage></profileDesc>'
    ]
    assert '<note n="orgfile">CTX000000001.alto.xml</note>' in out


# ── what the readers of the text see ────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["CTX000000001", "CTX000000002"])
def test_the_body_is_unchanged_but_for_pb_ana(name, tmp_path):
    original = _sample(name)
    record = dict(copy.deepcopy(RECORD_1), doc_id=name, source={"filename": f"{name}.alto.xml"})
    out, _ = _project(original, record, statistical=STATISTICAL if name.endswith("1") else None)
    assert _body(out) == _body(original)

    before, after = tmp_path / f"a_{name}.teitok.xml", tmp_path / f"b_{name}.teitok.xml"
    before.write_text(original, encoding="utf-8")
    after.write_text(out, encoding="utf-8")
    assert read_teitok_rows(after) == read_teitok_rows(before)
    assert read_teitok_tokens(after) == read_teitok_tokens(before)
    assert parse_teitok_layout(str(after)) == parse_teitok_layout(str(before))


def test_a_pb_inside_a_sentence_takes_its_category():
    """CTX000000002: the writer puts <pb/> inside <s> where a sentence runs over a page."""
    record = {
        "doc_id": "CTX000000002",
        "pages": [
            {"page": str(p), "teitok_surface": f"facs-{p}", "category": "TEXT"}
            for p in (1, 2, 3, 4)
        ],
    }
    xml = _sample("CTX000000002")
    out, report = _project(xml, record)
    root = _tree(out)
    inside = [pb for s in root.iter("s") for pb in s.iter("pb")]
    assert inside, "the sample no longer has a <pb> inside <s>"
    assert all(pb.get("ana") == "#pcat-TEXT" for pb in root.iter("pb"))
    assert report["page_categories"] == 4
    assert validate_xml_text(out, profile="contract") == []


def test_a_text_only_document_resolves_pages_by_their_number():
    """CTX_no_alto: no <facsimile>, so no teitok_surface; the numeric key finds pb-1."""
    xml = (FIXTURES / "CTX_no_alto.teitok.xml").read_text(encoding="utf-8")
    out, report = _project(xml, {"doc_id": "CTX_no_alto", "page_categories": {"1": "TEXT_T"}})
    assert _tree(out).find(".//pb").get("ana") == "#pcat-TEXT_T"
    assert report["unresolved_pages"] == []


# ── page resolution ─────────────────────────────────────────────────────────────────────────


def _pages(xml):
    root = _tree(xml)
    return tp._PageIndex(root.find("text"))


def test_record_pages_resolve_surface_then_index_then_number_then_label():
    index = _pages(
        '<TEI><teiHeader/><text><body><pb n="I" id="pb-1" corresp="#facs-1"/>'
        '<pb n="II" id="pb-2"/><pb n="7" id="pb-3"/></body></text></TEI>'
    )
    assert index.record_page({"page": "9", "teitok_surface": "facs-1"}) == "pb-1"
    assert index.record_page({"page": "9", "page_index": 2}) == "pb-2"
    assert index.record_page({"page": "3"}) == "pb-3"  # numeric key = physical index
    assert index.record_page({"page": "II"}) == "pb-2"  # the <pb n> label, last
    assert index.record_page({"page": "IV"}) is None


def test_an_llm_page_label_goes_through_the_record_then_pb_n():
    index = _pages(
        '<TEI><teiHeader/><text><body><pb n="5" id="pb-1"/><pb n="6" id="pb-2"/></body></text></TEI>'
    )
    # "## Page 5" from xml_to_md is pb@n, not the physical index
    assert index.item_page("5", {}) == "pb-1"
    # a label the record knows resolves the way the record's page does
    assert index.item_page("2", {"2": {"page": "2", "page_index": 2}}) == "pb-2"


def test_unresolved_pages_are_reported_not_fatal():
    record = copy.deepcopy(RECORD_1)
    record["pages"].append({"page": "42", "category": "PHOTO"})
    record["enrichment"]["items"][1]["page"] = "XIV"
    out, report = _project(_sample(), record)
    assert {"from": "page category", "page": "42"} in report["unresolved_pages"]
    assert {"from": "enrichment", "page": "XIV"} in report["unresolved_pages"]
    assert "pcat-PHOTO" not in out
    assert validate_xml_text(out, profile="contract") == []


# ── contract: idempotence, refusals, nothing to do ──────────────────────────────────────────


def test_projection_is_idempotent():
    once, _ = _project(_sample(), statistical=STATISTICAL)
    twice, report = _project(once, statistical=STATISTICAL)
    assert twice == once
    assert report["changed"] is False


def test_a_new_projection_replaces_the_old_one():
    once, _ = _project(_sample(), statistical=STATISTICAL)
    record = copy.deepcopy(RECORD_1)
    record["pages"][1]["category"] = "PHOTO"
    del record["enrichment"]
    again, _ = _project(once, record)
    root = _tree(again)
    assert [pb.get("ana") for pb in root.iter("pb")] == ["#pcat-TEXT_P", "#pcat-PHOTO"]
    assert root.find(".//textClass") is None  # no enrichment, no statistical: nothing to list
    assert len([c for c in root.iter("change") if c.get("type") == "enriched"]) == 1
    assert "app-llm-enrich" not in again and "app-kw" not in again


def test_projecting_nothing_returns_the_input_unchanged():
    xml = _sample()
    out, report = _project(xml, {"doc_id": "CTX000000001"})
    assert out == xml
    assert "nothing to project" in report["notes"]


def test_another_documents_teitok_is_refused():
    with pytest.raises(tp.ProjectionError, match="not this record's document"):
        _project(_sample(), dict(RECORD_1, doc_id="CTX999", source={"filename": "CTX999.alto.xml"}))


def test_a_seed_id_is_accepted_through_source_filename():
    """#68: an AMČR seed keeps its own doc_id; the TEITOK is named after the upload."""
    record = dict(copy.deepcopy(RECORD_1), doc_id="C-202400123A")
    out, _ = _project(_sample(), record)
    assert "#pcat-TEXT_P" in out


def test_an_invalid_input_is_refused():
    xml = (FIXTURES / "CTX_invalid.teitok.xml").read_text(encoding="utf-8")
    title = _tree(xml).findtext(".//title")
    with pytest.raises(tp.ProjectionError):
        _project(xml, {"doc_id": title, "page_categories": {"1": "TEXT"}})


def test_a_foreign_teitok_is_refused_unless_forced():
    xml = (FIXTURES / "legacy" / "CTX_format1.teitok.xml").read_text(encoding="utf-8")
    title = _tree(xml).findtext(".//title") if _tree(xml).find(".//title") is not None else ""
    with pytest.raises(tp.ProjectionError, match="teitok-2"):
        _project(xml, {"doc_id": title, "page_categories": {"1": "TEXT"}})


# ── statistical keywords ────────────────────────────────────────────────────────────────────


def test_page_conllu_mirrors_udpipe_output():
    xml = (FIXTURES / "CTX_valid.teitok.xml").read_text(encoding="utf-8")
    pages = tp._page_conllu(_tree(xml).find("text"))
    lines = pages["pb-1"].splitlines()
    assert lines[1].split("\t")[:4] == ["1", "Vyrocni", "vyrocni", "ADJ"]
    # "abych" = aby + bych: a range line, then the two syntactic words
    rng = next(ln for ln in lines if ln.split("\t")[0] == "5-6")
    assert rng.split("\t")[1] == "abych"
    assert [ln.split("\t")[2] for ln in lines if ln.split("\t")[0] in ("5", "6")] == ["aby", "být"]
    assert any(ln.endswith("SpaceAfter=No") for ln in lines)


def test_page_keywords_with_the_legacy_method_need_no_extra_package():
    kws = tp.page_keywords(_sample(), "legacy", 3)
    assert set(kws) <= {"pb-1", "pb-2"} and kws
    assert all(1 <= len(v) <= tp.KW_MIN for v in kws.values())  # asked 3, clamped up to 5 max


def test_statistical_block_reads_the_per_document_csv(tmp_path):
    csv_path = tmp_path / "CTX000000001_keywords.csv"
    csv_path.write_text("keyword,score\nHradiště,1.0\nkeramika,0.5\n", encoding="utf-8")
    block = tp.statistical_block(_sample(), "legacy", 50, csv_path)
    assert block["params"]["top_n"] == tp.KW_MAX
    assert block["document"] == [("Hradiště", 1.0), ("keramika", 0.5)]


def test_a_missing_backend_skips_the_pages_and_keeps_the_document_list(tmp_path, monkeypatch):
    def broken(*_a, **_k):
        raise ImportError("no yake here")

    monkeypatch.setattr(tp, "page_keywords", broken)
    csv_path = tmp_path / "k.csv"
    csv_path.write_text("keyword,score\nHradiště,1.0\n", encoding="utf-8")
    block = tp.statistical_block(_sample(), "yake", 10, csv_path)
    assert block["pages"] == {} and "no yake here" in block["params"]["pages_skipped"]
    assert block["document"] == [("Hradiště", 1.0)]


# ── survives the other TEITOK tools ─────────────────────────────────────────────────────────


def test_projection_survives_rescale_and_fix_teitok_bboxes(tmp_path):
    pytest.importorskip("fastapi")
    from service.rescale import rescale_teitok

    out, _ = _project(_sample(), statistical=STATISTICAL)
    rescaled = rescale_teitok(out, scale=0.5)["teitok_xml"]
    assert "#pcat-TEXT_P" in rescaled and "<textClass>" in rescaled
    assert validate_xml_text(rescaled, profile="contract") == []

    import fix_teitok_bboxes

    path = tmp_path / "CTX000000001.teitok.xml"
    path.write_text(out, encoding="utf-8")
    fix_teitok_bboxes.main(["-i", str(path), "--dx", "-10", "--sx", "0.5", "--sy", "0.5"])
    fixed = path.read_text(encoding="utf-8")
    assert "#pcat-TEXT_P" in fixed and "<textClass>" in fixed


def test_flexiconv_still_reads_a_projected_document(tmp_path):
    pytest.importorskip("flexiconv")
    from flexiconv.io.teitok_xml import load_teitok

    before, after = tmp_path / "a.teitok.xml", tmp_path / "b.teitok.xml"
    before.write_text(_sample(), encoding="utf-8")
    after.write_text(_project(_sample(), statistical=STATISTICAL)[0], encoding="utf-8")

    def forms(path):
        doc = load_teitok(str(path))
        return [n.features["form"] for n in doc.layers["tokens"].nodes.values()]

    assert forms(after) == forms(before)


# ── the committed fixture and the validator ─────────────────────────────────────────────────


def test_the_committed_projected_fixture_is_what_the_projector_writes():
    """Regenerate with: python -m tests.test_teitok_project"""
    out, _ = _project(_sample(), statistical=STATISTICAL)
    assert PROJECTED_FIXTURE.read_text(encoding="utf-8") == out
    assert validate_xml_text(out, profile="contract") == []


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ('ana="#pcat-DRAW"', 'ana="#pcat-NOPE"', "does not resolve"),
        ('ana="#pcat-DRAW"', 'ana="#app-kw"', "expected a <category>"),
        ('cert="0.92"', 'cert="1.5"', "cert"),
        ('resp="#app-kw"', 'resp="#tax-amcr-teater"', "expected a <application>"),
        ('corresp="#pb-2"', 'corresp="#facs-2"', "expected a <pb>"),
    ],
)
def test_the_validator_rejects_a_broken_projection(old, new, message):
    xml = PROJECTED_FIXTURE.read_text(encoding="utf-8")
    assert old in xml
    errors = validate_xml_text(xml.replace(old, new, 1), profile="contract")
    assert any(message in e for e in errors), errors


# ── the switch: CLI, pipeline stage, service ────────────────────────────────────────────────


def test_cli_projects_one_file(tmp_path):
    teitok = tmp_path / "CTX000000001.teitok.xml"
    shutil.copy(SAMPLES / teitok.name, teitok)
    record = tmp_path / "CTX000000001.document.json"
    record.write_text(json.dumps(RECORD_1), encoding="utf-8")
    assert tp.main(["--teitok", str(teitok), "--record", str(record), "--in-place"]) == 0
    assert "#pcat-DRAW" in teitok.read_text(encoding="utf-8")


def test_cli_directory_mode_skips_a_format1_file_without_failing(tmp_path, capsys):
    """A resumed run can leave format-1 files beside new ones; the opt-in stage must not fail
    the pipeline over them."""
    shutil.copy(SAMPLES / "CTX000000001.teitok.xml", tmp_path)
    shutil.copy(FIXTURES / "legacy" / "CTX_format1.teitok.xml", tmp_path)
    records = tmp_path / "records"
    records.mkdir()
    (records / "CTX000000001.document.json").write_text(json.dumps(RECORD_1), encoding="utf-8")
    rc = tp.main(["--teitok-dir", str(tmp_path), "--record-dir", str(records), "--in-place"])
    assert rc == 0
    assert "#pcat-DRAW" in (tmp_path / "CTX000000001.teitok.xml").read_text(encoding="utf-8")
    assert "[SKIP] CTX_format1.teitok.xml" in capsys.readouterr().out


def test_cli_directory_mode_accepts_a_seed_paired_by_name(tmp_path):
    """The pipeline pairs <doc_id>.teitok.xml with <doc_id>.document.json; an AMČR seed keeps
    its own doc_id and a source.filename unlike the upload, and that pairing is the identity."""
    shutil.copy(SAMPLES / "CTX000000001.teitok.xml", tmp_path)
    records = tmp_path / "records"
    records.mkdir()
    seed = dict(copy.deepcopy(RECORD_1), doc_id="C-202400123A", source={"filename": "scan-17.pdf"})
    (records / "CTX000000001.document.json").write_text(json.dumps(seed), encoding="utf-8")
    rc = tp.main(["--teitok-dir", str(tmp_path), "--record-dir", str(records), "--in-place"])
    assert rc == 0
    assert "#pcat-DRAW" in (tmp_path / "CTX000000001.teitok.xml").read_text(encoding="utf-8")
    # the same pair given explicitly, without the pairing, is refused
    with pytest.raises(tp.ProjectionError):
        _project(_sample(), seed)


class TestPipelineStage:
    def _args(self, **kwargs):
        import argparse

        ns = argparse.Namespace(
            stages=["manifest", "udp", "nt", "stats"],
            config=Path("dummy"),
            kw=False,
            kw_method="yake",
            llm=False,
            llm_config="dummy_llm.txt",
            force=False,
            lang="cs",
            num_keywords=None,
        )
        for k, v in kwargs.items():
            setattr(ns, k, v)
        return ns

    _VALUES = {"OUTPUT_DIR": "./out", "TEITOK_OUTPUT_DIR": "./out/TEITOK"}

    def test_off_by_default(self):
        import run_pipeline as rp

        plan = rp._build_plan(self._args(kw=True), self._VALUES)
        assert "project" not in [s["name"] for s in plan["stage_plan"]]
        assert plan["teitok_enrichment"] is False

    @pytest.mark.parametrize("how", ["flag", "config"])
    def test_on_after_keywords_before_llm(self, how):
        import run_pipeline as rp

        args = self._args(kw=True, llm=True, teitok_enrichment=how == "flag")
        values = dict(self._VALUES, TEITOK_ENRICHMENT="true" if how == "config" else "false")
        names = [s["name"] for s in rp._build_plan(args, values)["stage_plan"]]
        assert names[-3:] == ["keywords", "project", "llm"]

    def test_start_from_project_skips_everything_before_it(self):
        import run_pipeline as rp

        args = self._args(kw=True, teitok_enrichment=True, start_from="project")
        plan = rp._build_plan(args, self._VALUES)
        assert {s["name"]: s["skip"] for s in plan["stage_plan"]}["project"] is False
        assert all(plan["skips"][s] for s in ("manifest", "udp", "nt", "stats", "keywords"))

    def test_the_stage_command(self, tmp_path):
        import run_pipeline as rp

        args = self._args(kw=True, teitok_enrichment=True, num_keywords=12)
        plan = rp._build_plan(args, self._VALUES)
        cmd = rp._project_command(plan, args, Path("pd"), tmp_path, "yake")
        assert cmd[1:3] == ["-m", "api_util.teitok_project"]
        joined = " ".join(cmd)
        assert "--teitok-dir ./out/TEITOK --in-place" in joined
        assert (
            "--exclude out/TEITOK/flexiconv" in joined
            or "--exclude ./out/TEITOK/flexiconv" in joined
        )
        assert f"--record-dir {tmp_path}" in joined
        assert "--kw-method yake --kw-per-doc-dir out/KW_PER_DOC_Y" in joined
        assert "-n 12" in joined
        no_kw = rp._project_command(plan, self._args(), Path("pd"), None, "yake")
        assert "--kw-method" not in no_kw and "--record-dir" not in no_kw

    def test_the_config_knob_ships_off_and_the_environment_turns_it_on(self, monkeypatch):
        import run_pipeline as rp

        monkeypatch.delenv("TEITOK_ENRICHMENT", raising=False)
        values = rp._parse_config(REPO / "config_api.txt")
        assert values.get("TEITOK_ENRICHMENT") == "false"
        assert rp._build_plan(self._args(), values)["teitok_enrichment"] is False
        monkeypatch.setenv("TEITOK_ENRICHMENT", "true")
        values = rp._parse_config(REPO / "config_api.txt")
        assert rp._build_plan(self._args(), values)["teitok_enrichment"] is True


class TestService:
    @pytest.fixture
    def client(self):
        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient

        from service.api import app

        return TestClient(app)

    def test_project_record_returns_the_projected_teitok(self, client):
        resp = client.post(
            "/project_record",
            files={
                "file": ("CTX000000001.teitok.xml", _sample().encode("utf-8"), "application/xml"),
                "document_json": (
                    "r.json",
                    json.dumps(RECORD_1).encode("utf-8"),
                    "application/json",
                ),
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["doc_id"] == "CTX000000001"
        assert body["schema_valid"] is True and body["schema_errors"] == []
        assert body["report"]["page_categories"] == 2
        assert 'ana="#pcat-DRAW"' in body["teitok_xml"]

    def test_project_record_as_xml(self, client):
        resp = client.post(
            "/project_record",
            data={"format": "xml"},
            files={
                "file": ("x.teitok.xml", _sample().encode("utf-8"), "application/xml"),
                "document_json": (
                    "r.json",
                    json.dumps(RECORD_1).encode("utf-8"),
                    "application/json",
                ),
            },
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/xml")
        assert 'filename="CTX000000001.teitok.xml"' in resp.headers["content-disposition"]

    def test_project_record_refuses_another_documents_teitok(self, client):
        record = dict(RECORD_1, doc_id="CTX999", source={"filename": "CTX999.alto.xml"})
        resp = client.post(
            "/project_record",
            files={
                "file": ("x.teitok.xml", _sample().encode("utf-8"), "application/xml"),
                "document_json": ("r.json", json.dumps(record).encode("utf-8"), "application/json"),
            },
        )
        assert resp.status_code == 422
        assert "not this record's document" in resp.json()["detail"]

    def test_the_environment_cannot_switch_it_on_for_every_request(self, tmp_path, monkeypatch):
        pytest.importorskip("fastapi")
        import run_pipeline as rp
        from service.enrichment import _derive_config

        monkeypatch.setenv("TEITOK_ENRICHMENT", "true")
        values = rp._parse_config(_derive_config(tmp_path))
        assert values["TEITOK_ENRICHMENT"] == "false"

    def test_teitok_enrichment_defaults_to_false_and_reaches_the_pipeline(self, monkeypatch):
        pytest.importorskip("fastapi")
        from service import api
        from service.enrichment import PipelineManager

        assert api.EnrichTextRequest(lines=["x"]).teitok_enrichment is False
        seen = {}

        def fake_bounded(cmd, job_id, timeout):
            seen["cmd"] = cmd
            raise RuntimeError("stop here")

        monkeypatch.setattr(PipelineManager, "_run_bounded", staticmethod(fake_bounded))
        manager = PipelineManager()
        for flag in (False, True):
            with pytest.raises(RuntimeError, match="stop here"):
                manager.enrich(
                    [{"text": "x", "page_num": 1, "line_num": 1}],
                    "d",
                    "none",
                    teitok_enrichment=flag,
                )
            assert ("--teitok-enrichment" in seen["cmd"]) is flag


if __name__ == "__main__":  # regenerate the committed fixture
    xml, _ = _project(_sample(), statistical=STATISTICAL)
    PROJECTED_FIXTURE.write_text(xml, encoding="utf-8")
    print(f"-> {PROJECTED_FIXTURE}")
