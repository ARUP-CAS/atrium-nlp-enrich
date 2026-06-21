# Adjust import path assuming pytest runs from the repository root
import sys
from pathlib import Path

import pytest

_api_util_path = str(Path(__file__).parent.parent / "api_util")
if _api_util_path not in sys.path:
    sys.path.insert(0, _api_util_path)

from api_util.teitok_read import (  # noqa: E402
    doc_id_from_path,
    read_teitok_rows,
    read_teitok_text,
    read_teitok_tokens,
)

TEITOK_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<teiCorpus>
    <text>
        <pb n="1"/>
        <s text="První věta na stránce.">
            <tok id="w-1" lemma="první" pos="ADJ" join="right">První</tok>
            <tok id="w-2" lemma="věta" pos="NOUN">věta</tok>
            <tok id="w-3" lemma="na" pos="ADP">na</tok>
            <tok id="w-4" lemma="stránka" pos="NOUN" spaceAfter="No">stránce</tok>
            <tok id="w-5" lemma="." pos="PUNCT">.</tok>
        </s>
        <lb/>
        <s>
            <tok id="w-6" lemma="druhý" pos="ADJ">Druhá</tok>
            <tok id="w-7" lemma="chybí" pos="VERB">chybí</tok>
            <tok id="w-8" lemma="text" type="attr">text</tok>
        </s>
        <pb n="2"/>
        <s text="Věta na druhé straně.">
            <tok id="w-9" lemma="věta" pos="NOUN">Věta</tok>
        </s>
    </text>
</teiCorpus>
"""


@pytest.fixture
def sample_teitok(tmp_path):
    p = tmp_path / "doc.teitok.xml"
    p.write_text(TEITOK_SAMPLE, encoding="utf-8")
    return p


def test_doc_id_from_path():
    assert doc_id_from_path("CTX001.conllu") == "CTX001"
    assert doc_id_from_path("CTX001.teitok.xml") == "CTX001"
    assert doc_id_from_path("/path/to/CTX001.txt") == "CTX001"


def test_read_teitok_rows(sample_teitok):
    rows = read_teitok_rows(sample_teitok)
    assert len(rows) == 3

    # Check page and line tracking
    assert rows[0] == {"page_num": 1, "line_num": 1, "text": "První věta na stránce."}

    # Check fallback text reconstruction from <tok> elements if @text is missing
    assert rows[1] == {"page_num": 1, "line_num": 2, "text": "Druhá chybí text"}

    assert rows[2] == {"page_num": 2, "line_num": 2, "text": "Věta na druhé straně."}


def test_read_teitok_text(sample_teitok):
    text = read_teitok_text(sample_teitok)
    assert text == "První věta na stránce.\nDruhá chybí text\nVěta na druhé straně."


def test_read_teitok_tokens(sample_teitok):
    tokens = read_teitok_tokens(sample_teitok)
    assert len(tokens) == 9

    # Check standard token attributes
    assert tokens[0] == {"form": "První", "lemma": "první", "upos": "ADJ", "space_after": False}
    assert tokens[1] == {"form": "věta", "lemma": "věta", "upos": "NOUN", "space_after": True}

    # Check spaceAfter="No" mapped properly
    assert tokens[3]["space_after"] is False

    # Check fallback to @type for UPOS if @pos is missing
    assert tokens[7] == {"form": "text", "lemma": "text", "upos": "attr", "space_after": True}
