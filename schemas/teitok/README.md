# `schemas/teitok/` — Pinned TEITOK output contract (issue #28)

This directory vendors the XSD schema that `.teitok.xml` files must satisfy
before the pipeline packages them for the LINDAT dataset release.

## Files

| File | Purpose |
|---|---|
| `teitok.xsd` | The output contract itself. |
| `xml.xsd` | Local, trimmed copy of the W3C `xml.xsd`, providing `xml:lang`. Vendored so validation never needs network access. |

## Provenance

`teitok.xsd` is **not** copied from teitok.org or any upstream TEI schema
release. There is no single canonical upstream `.xsd` that describes this
pipeline's exact TEITOK profile (a constrained TEI subset with ATRIUM-specific
conventions such as `<div type="MarginTextZone-P">` and the `@cnec` NER
attribute). Instead:

- **Source of truth:** `api_util/teitok_alto.py::write_teitok_merged()`, the
  single writer that produces every `.teitok.xml` document in this pipeline.
- **Method:** the schema was hand-authored by running the real writer against
  representative inputs (with and without a source ALTO file, i.e. both the
  bbox-annotated and text-only fallback code paths) and encoding exactly the
  element/attribute shapes it produces.
- **Pinned as of:** the paired `atrium_document` accretion-model integration,
  2026-08-03 (commit corresponding to issue #28's resolution).
- **`xml.xsd`:** trimmed subset of `https://www.w3.org/2001/xml.xsd`,
  retaining only `xml:lang` (plus `xml:space`, `xml:base`, `xml:id` for
  forward compatibility), vendored for network-free validation per issue
  #28's schema-management decision.

## Keeping this schema current

If `teitok_alto.py`'s writer changes (new attributes, new element types,
new fallback branches), `teitok.xsd` must be updated in the same PR — this
schema describes the writer's actual contract, not an aspirational one.
`tests/test_validate_teitok_xml.py` and the `data_samples/TEITOK/CTX_valid.teitok.xml`
/ `CTX_no_alto.teitok.xml` fixtures should be regenerated from the real
writer (not hand-edited) whenever the writer's output shape changes, so the
schema is always validated against genuine output rather than a stale
hand-crafted sample.

## Future direction

Per `teitok_alto.py`'s own in-repo note, resolution-independent/relative
bounding-box coordinates are the preferred long-term direction pending
TEITOK-team confirmation. If that lands, `bboxType` in `teitok.xsd` will need
to accept the new coordinate format alongside or instead of the current
`"x1 y1 x2 y2"` absolute-pixel pattern.
