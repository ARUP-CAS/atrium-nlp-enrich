"""
tests/test_rescale.py
=====================
Hermetic tests for the TEITOK ``/rescale`` transform and endpoint (issue #8).

Covers the pure :mod:`service.rescale` functions and the ``POST /rescale``
FastAPI route, including the key robustness requirement: real TEITOK exports are
*not* well-formed XML (named entities open ``<name>`` but close ``</n>``), so the
transform must rewrite only coordinates and leave every other byte intact.
"""

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("fastapi.testclient")

from fastapi.testclient import TestClient  # noqa: E402

from api_util.bbox_scale import detect_source_size, fix_name_close_tags  # noqa: E402
from service.api import app  # noqa: E402
from service.rescale import RescaleError, rescale_teitok  # noqa: E402

# A minimal single-page TEITOK that deliberately includes the malformed
# ``<name>...</n>`` entity quirk found in real exports (data_samples/TEITOK).
SAMPLE_TEITOK = """<?xml version="1.0" encoding="utf-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0" xml:lang="cs">
  <facsimile>
    <surface id="d.surface1" lrx="1000" lry="2000">
      <graphic url="d-1.png"/>
    </surface>
  </facsimile>
  <text><body>
    <pb n="1" id="d.pb1" facs="d-1.png"/>
    <div type="Zone" id="d.b1" bbox="100 200 300 400">
      <s id="d.s1" text="Praha">
        <name type="LOC" cnec="gu"><tok id="d.s1.w1" bbox="100 200 150 240">Praha</tok></n>
      </s>
    </div>
  </body></text>
</TEI>
"""

# No <surface> dims — forces the bbox-extent fallback.
NO_SURFACE_TEITOK = (
    '<TEI><text><body><div bbox="0 0 400 600"><tok bbox="10 20 30 40">x</tok>'
    "</div></body></text></TEI>"
)


@pytest.fixture
def test_client():
    return TestClient(app)


# ── detect_source_size ─────────────────────────────────────────────────────────


def test_detect_source_size_from_surface():
    assert detect_source_size(SAMPLE_TEITOK) == (1000, 2000, "surface")


def test_detect_source_size_bbox_extent_fallback():
    # max x2 = 400, max y2 = 600 across all bbox boxes
    assert detect_source_size(NO_SURFACE_TEITOK) == (400, 600, "bbox-extent")


def test_detect_source_size_returns_none_without_coords():
    """Now returns None so the caller orchestrator can raise a localized error."""
    w, h, kind = detect_source_size(
        "<TEI><text><body><p>no coordinates here</p></body></text></TEI>"
    )
    assert w is None
    assert h is None
    assert kind is None


# ── rescale_teitok (pure) ──────────────────────────────────────────────────────


def test_rescale_raises_without_coords():
    """Verify RescaleError is raised internally when dependencies fail to find coords."""
    with pytest.raises(RescaleError):
        rescale_teitok("<TEI><text><body><p>no coordinates here</p></body></text></TEI>", 500, 1000)


def test_rescale_halves_coordinates_and_surface():
    res = rescale_teitok(SAMPLE_TEITOK, 500, 1000)  # sx = sy = 0.5
    assert res["source"] == {"width": 1000, "height": 2000}
    assert res["target"] == {"width": 500, "height": 1000}
    assert res["scale"] == {"sx": 0.5, "sy": 0.5}
    assert res["source_kind"] == "surface"
    assert res["boxes_rescaled"] == 2  # div + tok

    out = res["teitok_xml"]
    assert 'lrx="500"' in out and 'lry="1000"' in out
    assert 'bbox="50 100 150 200"' in out  # 100 200 300 400 → halved
    assert 'bbox="50 100 75 120"' in out  # 100 200 150 240 → halved


def test_rescale_does_not_depend_on_name_quirk():
    """The coordinate transform itself must not depend on the <name>...</n> quirk."""
    out = rescale_teitok(SAMPLE_TEITOK, 250, 500, fix_name_tags=False)["teitok_xml"]
    assert out.count("<name") == SAMPLE_TEITOK.count("<name")
    assert out.count("</n>") == SAMPLE_TEITOK.count("</n>")  # left untouched when disabled
    # Non-coordinate markup is untouched.
    assert '<graphic url="d-1.png"/>' in out
    assert 'xmlns="http://www.tei-c.org/ns/1.0"' in out


def test_rescale_fixes_name_close_tags_by_default():
    """By default the malformed </n> closings are repaired to </name>."""
    res = rescale_teitok(SAMPLE_TEITOK, 250, 500)
    out = res["teitok_xml"]
    assert res["name_tags_fixed"] == 1
    assert "</n>" not in out
    assert out.count("</name>") == SAMPLE_TEITOK.count("<name")  # every entity now closed
    # The repair leaves a valid <name>...</name> entity.
    assert '<name type="LOC" cnec="gu">' in out and "</name>" in out


def test_fix_name_close_tags_helper():
    fixed, count = fix_name_close_tags("<name>x</n><name>y</n>")
    assert fixed == "<name>x</name><name>y</name>"
    assert count == 2
    # No-op on already-valid markup, and never corrupts a real </name>.
    clean = "<name>x</name>"
    assert fix_name_close_tags(clean) == (clean, 0)


def test_rescale_default_output_is_well_formed_xml():
    """The headline benefit: a quirky TEITOK becomes parseable XML after rescale."""
    import xml.etree.ElementTree as ET

    # The raw sample is NOT well-formed (mismatched <name>...</n>).
    with pytest.raises(ET.ParseError):
        ET.fromstring(SAMPLE_TEITOK)

    # Disabling the repair keeps it non-well-formed...
    raw = rescale_teitok(SAMPLE_TEITOK, 250, 500, fix_name_tags=False)["teitok_xml"]
    with pytest.raises(ET.ParseError):
        ET.fromstring(raw)

    # ...but the default repaired output parses cleanly.
    fixed = rescale_teitok(SAMPLE_TEITOK, 250, 500)["teitok_xml"]
    ET.fromstring(fixed)  # must not raise


def test_rescale_identity_when_target_equals_source():
    out = rescale_teitok(SAMPLE_TEITOK, 1000, 2000)["teitok_xml"]
    assert 'bbox="100 200 300 400"' in out
    assert 'lrx="1000"' in out and 'lry="2000"' in out


def test_rescale_uses_bbox_extent_fallback():
    res = rescale_teitok(NO_SURFACE_TEITOK, 800, 1200)  # 2x of 400x600
    assert res["source_kind"] == "bbox-extent"
    assert res["scale"] == {"sx": 2.0, "sy": 2.0}
    assert 'bbox="20 40 60 80"' in res["teitok_xml"]  # 10 20 30 40 doubled


def test_rescale_rejects_nonpositive_target():
    with pytest.raises(RescaleError):
        rescale_teitok(SAMPLE_TEITOK, 0, 100)


# ── POST /rescale endpoint ─────────────────────────────────────────────────────


def _post(client, *, width=500, height=1000, fmt=None, fix_names=None, content=SAMPLE_TEITOK):
    data = {"width": str(width), "height": str(height)}
    if fmt is not None:
        data["format"] = fmt
    if fix_names is not None:
        data["fix_names"] = str(fix_names).lower()
    return client.post(
        "/rescale",
        files={"file": ("d.teitok.xml", content.encode("utf-8"), "application/xml")},
        data=data,
    )


def test_endpoint_json_default(test_client):
    r = _post(test_client)
    assert r.status_code == 200
    body = r.json()
    assert body["scale"] == {"sx": 0.5, "sy": 0.5}
    assert body["source"] == {"width": 1000, "height": 2000}
    assert body["name_tags_fixed"] == 1
    assert 'lrx="500"' in body["teitok_xml"]
    assert 'bbox="50 100 150 200"' in body["teitok_xml"]
    assert "</n>" not in body["teitok_xml"]


def test_endpoint_fix_names_can_be_disabled(test_client):
    r = _post(test_client, fix_names=False)
    assert r.status_code == 200
    body = r.json()
    assert body["name_tags_fixed"] == 0
    assert "</n>" in body["teitok_xml"]  # quirk left intact on request


def test_endpoint_xml_format_returns_file(test_client):
    r = _post(test_client, fmt="xml")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    assert "attachment" in r.headers.get("content-disposition", "")
    assert r.headers["content-disposition"].endswith('.rescaled.teitok.xml"')
    assert 'bbox="50 100 150 200"' in r.text


def test_endpoint_rejects_bad_dimensions(test_client):
    r = _post(test_client, width=0)
    assert r.status_code == 422


def test_endpoint_rejects_non_teitok_input(test_client):
    r = _post(test_client, content="just some plain text, not xml")
    assert r.status_code == 422


def test_endpoint_rejects_input_without_source_size(test_client):
    # Looks like TEITOK (has a bbox token) but no usable coordinates → 422.
    r = _post(test_client, content='<TEI><tok bbox="bad">x</tok></TEI>')
    assert r.status_code == 422
