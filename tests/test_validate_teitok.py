"""Tests for api_util/validate_teitok_xml.py (issue #28).

Run from the repo root: pytest tests/test_validate_teitok_xml.py -v

Two layers here, and the second is the load-bearing one:

* Curated fixtures in ``tests/fixtures/teitok/`` pin the gate's behaviour
  (accept / reject / diagnostics / exit codes).
* ``TestRealWriterRoundTrip`` runs the actual generator,
  ``api_util/teitok_alto.py::write_teitok_merged``, and validates its output.
  The first version of this gate passed every fixture test while rejecting
  100% of real output, because the fixtures were hand-authored with
  ``xmlns=`` while the writer emits ``xmlnsoff=``. Only a round-trip against
  the real writer catches that class of drift, so it must stay.
"""

import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "api_util"))

pytest.importorskip("lxml", reason="lxml is required for TEITOK XSD validation")

from api_util.validate_teitok_xml import (  # noqa: E402
    DEFAULT_SCHEMA,
    validate_directory,
    validate_document,
)

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "teitok"
REAL_SAMPLES = REPO_ROOT / "data_samples" / "TEITOK"


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


# ═════════════════════════════════════════════════════════════════════════════
# Empty runs — the gate must not pre-empt the runner's FAIL_ON_EMPTY decision
# ═════════════════════════════════════════════════════════════════════════════
class TestAllowEmpty:
    def test_missing_directory_is_ok_with_allow_empty(self, tmp_path):
        assert validate_directory(tmp_path / "never_created", allow_empty=True) is True

    def test_empty_directory_is_ok_with_allow_empty(self, tmp_path):
        assert validate_directory(tmp_path, allow_empty=True) is True

    def test_allow_empty_still_rejects_a_bad_document(self, tmp_path):
        """allow_empty relaxes "nothing found", never "found something bad"."""
        shutil.copy(FIXTURES / "CTX_invalid.teitok.xml", tmp_path)
        assert validate_directory(tmp_path, allow_empty=True) is False


# ═════════════════════════════════════════════════════════════════════════════
# Namespace normalization — both TEITOK conventions must validate
# ═════════════════════════════════════════════════════════════════════════════
class TestNamespaceConventions:
    """``teitok_alto.py`` writes ``xmlnsoff=``/``lang=`` (no namespace);
    older exports and ``service/rescale.py`` output carry the real TEI
    namespace with ``xml:lang``. One schema, both accepted."""

    WRITER_ROOT = '<TEI xmlnsoff="http://www.tei-c.org/ns/1.0" lang="cs">'
    TEI_NS_ROOT = '<TEI xmlns="http://www.tei-c.org/ns/1.0" xml:lang="cs">'

    def _as_writer_shaped(self, src: Path, dest_dir: Path) -> Path:
        text = src.read_text(encoding="utf-8").replace(self.TEI_NS_ROOT, self.WRITER_ROOT)
        assert self.WRITER_ROOT in text, "fixture root did not match the TEI-namespace form"
        out = dest_dir / src.name
        out.write_text(text, encoding="utf-8")
        return out

    def test_namespaced_document_passes(self, tmp_path):
        shutil.copy(FIXTURES / "CTX_valid.teitok.xml", tmp_path)
        assert validate_directory(tmp_path) is True

    def test_writer_shaped_no_namespace_document_passes(self, tmp_path):
        self._as_writer_shaped(FIXTURES / "CTX_valid.teitok.xml", tmp_path)
        assert validate_directory(tmp_path) is True

    def test_writer_shaped_invalid_document_still_fails(self, tmp_path):
        """Namespace tolerance must not become blanket tolerance."""
        self._as_writer_shaped(FIXTURES / "CTX_invalid.teitok.xml", tmp_path)
        assert validate_directory(tmp_path) is False

    def test_foreign_root_element_is_rejected(self, tmp_path):
        (tmp_path / "bogus.teitok.xml").write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n<alto><Layout/></alto>\n',
            encoding="utf-8",
        )
        assert validate_directory(tmp_path) is False


# ═════════════════════════════════════════════════════════════════════════════
# Well-formedness-only mode (the flexiconv tier) and the </n> quirk
# ═════════════════════════════════════════════════════════════════════════════
class TestWellformedOnlyMode:
    def test_schema_invalid_but_well_formed_passes_in_wellformed_mode(self, tmp_path):
        """CTX_invalid is well-formed XML that violates the XSD: rejected by
        the full gate, accepted by the well-formedness tier."""
        shutil.copy(FIXTURES / "CTX_invalid.teitok.xml", tmp_path)
        assert validate_directory(tmp_path) is False
        assert validate_directory(tmp_path, wellformed_only=True) is True

    def test_broken_xml_fails_even_in_wellformed_mode(self, tmp_path):
        (tmp_path / "broken.teitok.xml").write_text(
            '<?xml version="1.0"?>\n<TEI><text><body></TEI>\n', encoding="utf-8"
        )
        assert validate_directory(tmp_path, wellformed_only=True) is False

    def test_name_close_quirk_is_reported_with_a_repair_hint(self, tmp_path, capsys):
        """The `<name>...</n>` quirk must fail loudly and point at the fix —
        the gate diagnoses, it never silently repairs."""
        text = (FIXTURES / "CTX_valid.teitok.xml").read_text(encoding="utf-8")
        assert "</name>" in text
        (tmp_path / "quirk.teitok.xml").write_text(
            text.replace("</name>", "</n>"), encoding="utf-8"
        )
        assert validate_directory(tmp_path) is False
        captured = capsys.readouterr()
        assert "not well-formed XML" in captured.err
        assert "fix_teitok_bboxes.py" in captured.err


# ═════════════════════════════════════════════════════════════════════════════
# Single-document entry point (used by the service layer)
# ═════════════════════════════════════════════════════════════════════════════
class TestValidateDocument:
    def test_valid_document_returns_no_diagnostics(self):
        assert validate_document(FIXTURES / "CTX_valid.teitok.xml") == []

    def test_invalid_document_returns_diagnostics(self):
        errors = validate_document(FIXTURES / "CTX_invalid.teitok.xml")
        assert errors
        assert any("unexpectedElement" in e for e in errors)


# ═════════════════════════════════════════════════════════════════════════════
# The published example outputs must satisfy the contract they illustrate
# ═════════════════════════════════════════════════════════════════════════════
def test_committed_data_samples_are_conformant():
    """README.md advertises data_samples/TEITOK/ as the example output
    directory. Three of those files were once not even well-formed XML
    (`<name>` closed with `</n>`); this keeps them honest."""
    assert list(REAL_SAMPLES.glob("*.teitok.xml")), "no committed TEITOK samples found"
    assert validate_directory(REAL_SAMPLES) is True


# ═════════════════════════════════════════════════════════════════════════════
# Round-trip against the real generator — the test that catches writer drift
# ═════════════════════════════════════════════════════════════════════════════
_CONLLU = (
    "# sent_id = 1\n"
    "# text = Jan Novotný v Praze .\n"
    "1\tJan\tJan\tPROPN\t_\t_\t0\troot\t_\tNER=B-P\n"
    "2\tNovotný\tNovotný\tPROPN\t_\t_\t1\tflat\t_\tNER=I-P\n"
    "3\tv\tv\tADP\t_\t_\t4\tcase\t_\tNER=O\n"
    "4\tPraze\tPraha\tPROPN\t_\t_\t1\tobl\t_\tNER=B-gu\n"
    "5\t.\t.\tPUNCT\t_\t_\t1\tpunct\t_\tNER=O\n"
    "\n"
)

_ALTO = """<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns="http://www.loc.gov/standards/alto/ns-v3#">
    <Description><MeasurementUnit>pixel</MeasurementUnit></Description>
    <Layout>
        <Page ID="Page1" PHYSICAL_IMG_NR="1" HEIGHT="3500" WIDTH="2400">
            <PrintSpace HEIGHT="3000" WIDTH="2000" HPOS="0" VPOS="0">
                <TextBlock ID="block_1" HPOS="100" VPOS="100" WIDTH="500" HEIGHT="50">
                    <TextLine ID="line_1" HPOS="100" VPOS="100" WIDTH="500" HEIGHT="50">
                        <String ID="s1" CONTENT="Jan" HPOS="100" VPOS="100" WIDTH="80" HEIGHT="40"/>
                        <String ID="s2" CONTENT="Novotný" HPOS="200" VPOS="100" WIDTH="180" HEIGHT="40"/>
                        <String ID="s3" CONTENT="v" HPOS="400" VPOS="100" WIDTH="20" HEIGHT="40"/>
                        <String ID="s4" CONTENT="Praze" HPOS="440" VPOS="100" WIDTH="140" HEIGHT="40"/>
                        <String ID="s5" CONTENT="." HPOS="590" VPOS="100" WIDTH="10" HEIGHT="40"/>
                    </TextLine>
                </TextBlock>
            </PrintSpace>
        </Page>
    </Layout>
</alto>
"""


class TestRealWriterRoundTrip:
    """Generate TEITOK with the production writer, then validate it against
    the pinned schema. If ``teitok_alto.py``'s output shape drifts — a new
    element, a renamed attribute, a changed root — these fail, which is the
    whole point of calling the schema an output *contract*."""

    def _generate(self, tmp_path, *, with_alto: bool) -> Path:
        from teitok_alto import write_teitok_merged

        conllu = tmp_path / "doc.conllu"
        conllu.write_text(_CONLLU, encoding="utf-8")
        out = tmp_path / "doc.teitok.xml"

        alto_arg = None
        if with_alto:
            alto = tmp_path / "doc.alto.xml"
            alto.write_text(_ALTO, encoding="utf-8")
            alto_arg = str(alto)

        assert write_teitok_merged(str(conllu), str(out), alto_path=alto_arg) is True
        assert out.is_file(), "writer reported success but produced no file"
        return out

    def test_generated_output_with_alto_is_conformant(self, tmp_path):
        out = self._generate(tmp_path, with_alto=True)
        errors = validate_document(out)
        assert errors == [], "real writer output (ALTO/bbox path) violates the XSD:\n" + "\n".join(
            errors
        )

    def test_generated_output_without_alto_is_conformant(self, tmp_path):
        """The text-only fallback branch (no <facsimile>, no bboxes)."""
        out = self._generate(tmp_path, with_alto=False)
        errors = validate_document(out)
        assert errors == [], "real writer output (no-ALTO path) violates the XSD:\n" + "\n".join(
            errors
        )

    def test_generated_output_passes_the_directory_gate(self, tmp_path):
        """End-to-end shape of what api_4_stats.sh actually runs."""
        self._generate(tmp_path, with_alto=True)
        assert validate_directory(tmp_path) is True

    def test_writer_still_emits_the_xmlnsoff_convention(self, tmp_path):
        """Documents the coupling explicitly: the schema has no
        targetNamespace *because* the writer emits `xmlnsoff`. If this
        assertion ever fails, teitok.xsd and _strip_tei_namespace need
        revisiting together — not a one-line schema patch."""
        out = self._generate(tmp_path, with_alto=True)
        head = out.read_text(encoding="utf-8").splitlines()[1]
        assert 'xmlnsoff="http://www.tei-c.org/ns/1.0"' in head
        assert 'lang="cs"' in head

    def test_named_entities_survive_into_conformant_name_elements(self, tmp_path):
        """The NER spans in _CONLLU must become schema-valid <name> wrappers
        (a PER span of two tokens and a LOC span of one)."""
        import xml.etree.ElementTree as ET

        out = self._generate(tmp_path, with_alto=True)
        assert validate_document(out) == []
        names = list(ET.parse(str(out)).getroot().iter("name"))
        assert [n.get("type") for n in names] == ["PER", "LOC"]
        assert [len(list(n.iter("tok"))) for n in names] == [2, 1]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
