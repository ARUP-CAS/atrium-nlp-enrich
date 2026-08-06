"""
tests/test_doc_id_parity.py
===========================
One doc_id per document, whichever entry point derives it (atrium-project#10 D3).

This repo used to hand-roll the derivation in five places —
``api_util/build_manifest_row.py`` (``os.path.splitext(basename)[0]``),
``api_util/summarize_nt_udp.py`` twice (``conllu_path.stem``),
``run_pipeline.py::_pipeline_doc_id`` (``matches[0].stem``),
``api_util/teitok_alto.py::parse_and_align_conllu``'s fallback
(``Path(conllu_path).stem``) — plus ``api_util/teitok_read.doc_id_from_path``,
which stripped ``.teitok.xml`` / ``.conllu`` as literal-length slices. All six
agreed on the conventional single-dot names and forked on everything else, which
is exactly why nobody noticed: a fork does not fail, it silently keys the record
under a name no other stage ever writes to, and the baseline's blocks are then
dropped by rule 3.

``.conllu`` is this repo's working currency, so the load-bearing case is
``X.udpipe.conllu``: ``Path.stem`` yields ``X.udpipe`` where
``canonical_doc_id()`` yields ``X`` (``KNOWN_PIPELINE_SUFFIXES`` matches the
longer suffix first — deliberately). Every derivation now routes through
``atrium_document.canonical_doc_id()``, and the tests below pin THAT rather than
each implementation's private shape, so the parity survives a future refactor of
any single site.
"""

from unittest.mock import patch

import pytest

from api_util.build_manifest_row import main as build_manifest_row_main
from api_util.teitok_alto import parse_and_align_conllu
from api_util.teitok_read import doc_id_from_path
from atrium_document import canonical_doc_id
from run_pipeline import _pipeline_doc_id

#: The multi-dot names the ecosystem actually passes around. Every one is a real
#: pipeline artefact name: alto-postprocess hands ``.alto.xml`` in, nlp-enrich
#: writes ``.teitok.xml`` and reads ``.udpipe.conllu``, and ``.document.json``
#: travels the whole chain — so a fork here orphans the record for every stage
#: downstream, not just for this repo.
_MULTI_DOT_NAMES = [
    "CTX000000001.alto.xml",
    "CTX000000001.teitok.xml",
    "CTX000000001.udpipe.conllu",
    "CTX000000001.conllu",
    "CTX000000001.document.json",
    "CTX000000001.v2.csv",
    "CTX000000001.csv",
    # A doc_id that legitimately contains dots — `split(".")[0]` truncated it to
    # "sbn", inventing a document shared with every other 2019 volume.
    "sbn.2019.conllu",
]

#: The one case that motivated D3 in this repo specifically, spelled out so a
#: regression is legible in the test name rather than buried in a parametrisation.
_UDPIPE_CONLLU = "CTX000000001.udpipe.conllu"

_CONLLU_BODY = (
    "# sent_id = 1\n"
    "# text = Praha\n"
    "1\tPraha\tPraha\tPROPN\t_\t_\t0\troot\t_\tNE=B-LOC\n"
    "\n"
)


def test_canonical_strips_the_udpipe_conllu_pair_not_just_the_last_dot():
    """The premise every assertion below rests on. If this ever fails, the hub's
    KNOWN_PIPELINE_SUFFIXES ordering changed and the fix is there, not here."""
    assert canonical_doc_id(_UDPIPE_CONLLU) == "CTX000000001"
    # What the four hand-rolled `.stem` derivations produced instead:
    assert _UDPIPE_CONLLU[: -len(".conllu")] == "CTX000000001.udpipe"


@pytest.mark.parametrize("name", _MULTI_DOT_NAMES)
def test_teitok_read_doc_id_from_path_matches_canonical(name):
    """``doc_id_from_path`` is public and documented for ``.conllu``; keywords.py,
    llm_utils.py and llm_run.py all key their per-document outputs off it."""
    assert doc_id_from_path(name) == canonical_doc_id(name)
    assert doc_id_from_path(f"/some/dir/{name}") == canonical_doc_id(name)


@pytest.mark.parametrize("name", [n for n in _MULTI_DOT_NAMES if n.endswith(".csv")])
def test_pipeline_doc_id_matches_canonical(name, tmp_path):
    """The orchestrator predicts the id its own stats stage will compute, seeds
    ``<doc_id>.document.json`` under it and collects the same filename back — so
    a mismatch means the seeded baseline is orphaned and every upstream block is
    silently dropped (see ``_prepare_document_json_bridge``)."""
    (tmp_path / name).write_text("text,page_num,line_num\nx,1,1\n", encoding="utf-8")
    assert _pipeline_doc_id(str(tmp_path)) == canonical_doc_id(name)


@pytest.mark.parametrize("name", [n for n in _MULTI_DOT_NAMES if n.endswith(".csv")])
def test_build_manifest_row_doc_id_matches_canonical(name, tmp_path, capsys, monkeypatch):
    """The manifest row IS the id every later stage inherits: api_2_udp.sh names
    ``UDP/<file>.conllu`` from column 1, api_3_nt.sh keys ``NE/<file>`` off that,
    and the document record ends up under the same name. Parametrised over the
    ``.csv`` names only: api_1_manifest.sh globs ``*.csv``/``*.xlsx``, and the CSV
    reader is the one this entry point exercises without openpyxl."""
    src = tmp_path / name
    src.write_text("text,page_num,line_num\nPraha,1,1\n", encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        ["build_manifest_row.py", str(src), "--text-dir", str(tmp_path / "txt")],
    )
    build_manifest_row_main()

    expected = canonical_doc_id(name)
    # Column 1 of the TSV row is the doc_id; column 3 is the extracted text file,
    # which is named from the same derivation and must not fork from it either.
    row = capsys.readouterr().out.strip().split("\t")
    assert row[0] == expected
    assert row[2].endswith(f"{expected}.txt")
    assert (tmp_path / "txt" / f"{expected}.txt").exists()


@pytest.mark.parametrize("name", [n for n in _MULTI_DOT_NAMES if n.endswith(".conllu")])
def test_parse_and_align_conllu_fallback_doc_id_matches_canonical(name, tmp_path):
    """The fallback id lands in the TEITOK ``@xml:id``/``<title>`` and in the
    ``teitok_ref`` strings the record's ``entities[]`` carry, so it must name the
    same document the rest of the pipeline does."""
    conllu = tmp_path / name
    conllu.write_text(_CONLLU_BODY, encoding="utf-8")
    parsed = parse_and_align_conllu(str(conllu), alto_path=None, doc_id=None)
    assert parsed is not None
    assert parsed["doc_id"] == canonical_doc_id(name)


@pytest.mark.parametrize("name", [n for n in _MULTI_DOT_NAMES if n.endswith(".conllu")])
def test_summarize_single_document_names_outputs_by_canonical(name, tmp_path):
    """The stats stage is the site that actually writes the record: its
    ``doc_name`` names ``<doc_name>.conllu``/``.csv``/``.teitok.xml`` AND the
    ``<doc_name>.document.json`` the bridge collects. All four must agree."""
    from api_util.summarize_nt_udp import process_single_document

    conllu = tmp_path / name
    conllu.write_text(_CONLLU_BODY, encoding="utf-8")
    ne_dir = tmp_path / "ne"
    ne_dir.mkdir()
    (ne_dir / "page-1.tsv").write_text("Word\tTag\tNE\nPraha\tPROPN\tB-LOC\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()  # api_4_stats.sh mkdir -p's the per-document dir before calling in

    with patch("api_util.summarize_nt_udp.write_teitok_merged") as mock_write:
        process_single_document(
            conllu_file=str(conllu),
            ne_dir=str(ne_dir),
            output_dir=str(out_dir),
            save_csv=True,
            save_teitok=True,
            teitok_out=str(tmp_path / "TEITOK"),
        )

    expected = canonical_doc_id(name)
    assert (out_dir / f"{expected}.conllu").exists()
    assert (out_dir / f"{expected}.csv").exists()
    assert mock_write.call_args.kwargs.get("doc_id") == expected
