"""
tests/test_teitok_preservation.py
=================================
Content-preservation tests for ``api_util/teitok_alto.write_teitok_merged``
(atrium-project issue #14, intermediate-data checks).

These guard the invariant that converting a NER-enriched CoNLL-U file into
TEITOK XML never drops, merges, or silently alters meaningful input text.
The conversion legitimately *reshapes* text (tokens become <tok> elements,
NER spans gain <name> wrappers, XML special characters are escaped) — the
tests are written to survive those legitimate reshapes but fail the moment a
future change loses a token, reorders tokens, or corrupts content.

When ``alto_path=None`` the function is a pure CoNLL-U -> XML transform: no
ALTO, no images, no bboxes, no network, no models. All tests run in the
default (``not slow``) lane.

CoNLL-U eligibility rule (mirrors the production parser): a token row counts
only when it has >= 10 tab-separated columns AND column 0 contains neither
'-' (multi-word-token range) nor '.' (empty/ellipsis node).
"""

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from teitok_alto import write_teitok_merged

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

# Add this mock ALTO string near your other constants
_ALTO_MARGIN_XML = """<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns="http://www.loc.gov/standards/alto/ns-v3#">
    <Layout>
        <Page ID="Page1" PHYSICAL_IMG_NR="1" HEIGHT="3500" WIDTH="2400">
            <PrintSpace HEIGHT="3000" WIDTH="2000" HPOS="200" VPOS="100">
                <TextBlock ID="block_1" HPOS="250" VPOS="150" WIDTH="500" HEIGHT="50">
                    <TextLine ID="line_1" HPOS="250" VPOS="150" WIDTH="500" HEIGHT="50">
                        <String CONTENT="Test" HPOS="250" VPOS="150" WIDTH="500" HEIGHT="50"/>
                    </TextLine>
                </TextBlock>
            </PrintSpace>
        </Page>
    </Layout>
</alto>
"""

_ALTO_CONLLU = (
    "# sent_id = 1\n"
    "# text = Test\n"
    "1\tTest\tTest\tNOUN\t_\t_\t0\troot\t_\t_\n\n"
)

# ═════════════════════════════════════════════════════════════════════════════
# Spatial Bounding Box Math (Issues #4 & #9)
# ═════════════════════════════════════════════════════════════════════════════
class TestSpatialAlignment:

    def test_printspace_margins_are_subtracted(self, tmp_path):
        """Ensures that HPOS/VPOS from <PrintSpace> are subtracted from
        absolute bounding boxes to fix margin displacement."""

        conllu_file = _write_conllu(tmp_path, _ALTO_CONLLU, "test.conllu")

        alto_file = Path(tmp_path) / "test.alto.xml"
        alto_file.write_text(_ALTO_MARGIN_XML, encoding="utf-8")

        out = Path(tmp_path) / "test.teitok.xml"

        # Run conversion WITH the mock ALTO file, NO images (sx=1.0, sy=1.0)
        write_teitok_merged(str(conllu_file), str(out), alto_path=str(alto_file))

        root = ET.parse(str(out)).getroot()
        tok = next(root.iter("tok"))

        bbox_str = tok.get("bbox")
        assert bbox_str is not None, "Bounding box was not assigned to token"

        # The absolute coordinate of "Test" is HPOS=250, VPOS=150, Width=500, Height=50
        # PrintSpace offset is HPOS=200, VPOS=100
        # Expected X1 = 250 - 200 = 50
        # Expected Y1 = 150 - 100 = 50
        # Expected X2 = (250+500) - 200 = 550
        # Expected Y2 = (150+50) - 100 = 100
        assert bbox_str == "50 50 550 100", f"BBox displacement failed. Got: {bbox_str}"


def _eligible_tokens(conllu_path):
    """Return the ordered list of token dicts the converter should emit.

    Applies the same skip rule as ``write_teitok_merged``: >=10 columns and
    no '-'/'.' in column 0.
    """
    tokens = []
    with open(conllu_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line.strip() or line.startswith("#"):
                continue
            cols = line.split("\t")
            if len(cols) < 10 or "-" in cols[0] or "." in cols[0]:
                continue
            misc = cols[9]
            tokens.append({
                "form": cols[1],
                "ner": _ner_from_misc(misc),
                "space_after": "SpaceAfter=No" not in misc,
            })
    return tokens


def _ner_from_misc(misc):
    if misc in ("_", ""):
        return ""
    for item in misc.split("|"):
        if item.startswith("NER="):
            return item[len("NER="):]
    return ""


def _convert(conllu_path, tmp_path, name="out.teitok.xml"):
    """Run the pure CoNLL-U -> TEITOK conversion (no ALTO) and return out path."""
    out = Path(tmp_path) / name
    ok = write_teitok_merged(str(conllu_path), str(out), alto_path=None)
    assert ok is True, "write_teitok_merged returned False"
    assert out.exists(), "no TEITOK output produced"
    return out


def _tok_texts(root):
    """All <tok> element texts in document order."""
    return [el.text or "" for el in root.iter("tok")]


def _nonspace(chars_iterable):
    return "".join("".join(s.split()) for s in chars_iterable)


def _write_conllu(tmp_path, body, name="crafted.conllu"):
    p = Path(tmp_path) / name
    p.write_text(body, encoding="utf-8")
    return str(p)


# A NER-tagged single-sentence document: "Jan Novotný v Praze ."
# Jan/Novotný -> PER span (B-P, I-P), Praze -> LOC span (B-gu).
_NER_CONLLU = (
    "# sent_id = 1\n"
    "# text = Jan Novotný v Praze .\n"
    "1\tJan\tJan\tPROPN\t_\t_\t0\troot\t_\tNER=B-P\n"
    "2\tNovotný\tNovotný\tPROPN\t_\t_\t1\tflat\t_\tNER=I-P\n"
    "3\tv\tv\tADP\t_\t_\t4\tcase\t_\tNER=O\n"
    "4\tPraze\tPraha\tPROPN\t_\t_\t1\tobl\t_\tNER=B-gu\n"
    "5\t.\t.\tPUNCT\t_\t_\t1\tpunct\t_\tNER=O\n"
    "\n"
)

# Special-character forms that must survive XML escaping unchanged.
_SPECIAL_CONLLU = (
    "# sent_id = 1\n"
    "# text = a < b & c \" d\n"
    "1\ta<b\ta\tNOUN\t_\t_\t0\troot\t_\t_\n"
    "2\tc&d\tc\tNOUN\t_\t_\t1\tnmod\t_\t_\n"
    '3\te"f\te\tNOUN\t_\t_\t1\tnmod\t_\t_\n'
    "\n"
)


# ═════════════════════════════════════════════════════════════════════════════
# Well-formedness
# ═════════════════════════════════════════════════════════════════════════════
class TestWellFormed:

    def test_output_is_well_formed_tei(self, sample_conllu, tmp_path):
        out = _convert(sample_conllu, tmp_path)
        root = ET.parse(str(out)).getroot()
        assert root.tag == "TEI"

    def test_ner_document_is_well_formed(self, tmp_path):
        cp = _write_conllu(tmp_path, _NER_CONLLU)
        out = _convert(cp, tmp_path, "ner.teitok.xml")
        root = ET.parse(str(out)).getroot()
        assert root.tag == "TEI"


# ═════════════════════════════════════════════════════════════════════════════
# Token presence / order / count
# ═════════════════════════════════════════════════════════════════════════════
class TestTokenPreservation:

    def test_every_form_present_as_tok(self, sample_conllu, tmp_path):
        out = _convert(sample_conllu, tmp_path)
        root = ET.parse(str(out)).getroot()
        emitted = _tok_texts(root)
        expected = [t["form"] for t in _eligible_tokens(sample_conllu)]
        assert emitted == expected  # same content, same order

    def test_tok_count_matches_eligible_tokens(self, sample_conllu, tmp_path):
        out = _convert(sample_conllu, tmp_path)
        root = ET.parse(str(out)).getroot()
        assert len(_tok_texts(root)) == len(_eligible_tokens(sample_conllu))

    def test_no_meaningful_text_dropped(self, sample_conllu, tmp_path):
        """Strong char-conservation: non-whitespace chars of all <tok> texts
        equal the non-whitespace chars of all eligible FORM values."""
        out = _convert(sample_conllu, tmp_path)
        root = ET.parse(str(out)).getroot()
        got = _nonspace(_tok_texts(root))
        exp = _nonspace(t["form"] for t in _eligible_tokens(sample_conllu))
        assert got == exp

    def test_sentence_text_reconstructs_from_tokens(self, sample_conllu, tmp_path):
        """Rebuilding a sentence from its child <tok>s (honouring join="right"
        / SpaceAfter=No) reproduces the <s text="..."> attribute."""
        out = _convert(sample_conllu, tmp_path)
        root = ET.parse(str(out)).getroot()
        sentences = list(root.iter("s"))
        assert sentences, "no <s> elements emitted"
        checked = 0
        for s in sentences:
            text_attr = s.get("text")
            if not text_attr:
                continue
            rebuilt = ""
            for tok in s.iter("tok"):
                rebuilt += (tok.text or "")
                rebuilt += "" if tok.get("join") == "right" else " "
            assert "".join(rebuilt.split()) == "".join(text_attr.split())
            checked += 1
        assert checked > 0


# ═════════════════════════════════════════════════════════════════════════════
# Special-character escaping round-trip
# ═════════════════════════════════════════════════════════════════════════════
class TestSpecialChars:

    def test_special_chars_escaped_and_preserved(self, tmp_path):
        cp = _write_conllu(tmp_path, _SPECIAL_CONLLU, "special.conllu")
        out = _convert(cp, tmp_path, "special.teitok.xml")
        root = ET.parse(str(out)).getroot()        # must parse (escaping valid)
        emitted = _tok_texts(root)
        assert emitted == ["a<b", "c&d", 'e"f']     # parsed back to originals


# ═════════════════════════════════════════════════════════════════════════════
# NER spans
# ═════════════════════════════════════════════════════════════════════════════
class TestNerSpans:

    def test_ner_span_tokens_all_preserved(self, tmp_path):
        cp = _write_conllu(tmp_path, _NER_CONLLU)
        out = _convert(cp, tmp_path, "ner.teitok.xml")
        root = ET.parse(str(out)).getroot()

        # No token lost overall, order intact.
        assert _tok_texts(root) == ["Jan", "Novotný", "v", "Praze", "."]

        # Entity tokens live inside <name> wrappers.
        name_tok_texts = []
        for name in root.iter("name"):
            name_tok_texts.extend(t.text for t in name.iter("tok"))
        assert "Jan" in name_tok_texts
        assert "Novotný" in name_tok_texts      # I-P continues the PER span
        assert "Praze" in name_tok_texts        # separate LOC span
        # Non-entity tokens must NOT be wrapped.
        assert "v" not in name_tok_texts
        assert "." not in name_tok_texts

    def test_name_wrapper_types_mapped(self, tmp_path):
        cp = _write_conllu(tmp_path, _NER_CONLLU)
        out = _convert(cp, tmp_path, "ner.teitok.xml")
        root = ET.parse(str(out)).getroot()
        types = {n.get("type") for n in root.iter("name")}
        assert "PER" in types
        assert "LOC" in types


# ═════════════════════════════════════════════════════════════════════════════
# Page boundaries
# ═════════════════════════════════════════════════════════════════════════════
class TestPageBoundaries:

    def test_all_tokens_present_across_two_page_reset(self, two_page_conllu, tmp_path):
        out = _convert(two_page_conllu, tmp_path, "twopage.teitok.xml")
        root = ET.parse(str(out)).getroot()
        assert _tok_texts(root) == [t["form"] for t in _eligible_tokens(two_page_conllu)]
        # The sent_id reset must have produced two pages, no token swallowed.
        assert len(list(root.iter("pb"))) == 2

    def test_all_tokens_present_across_page_break_marker(self, page_break_conllu, tmp_path):
        out = _convert(page_break_conllu, tmp_path, "pb.teitok.xml")
        root = ET.parse(str(out)).getroot()
        assert _tok_texts(root) == [t["form"] for t in _eligible_tokens(page_break_conllu)]
        assert len(list(root.iter("pb"))) == 2