# `schemas/teitok/` — Pinned TEITOK output contract (issue #28)

This directory vendors the XSD schema that `.teitok.xml` files must satisfy
before the pipeline packages them for the LINDAT dataset release.

## Files

| File         | Purpose                                                                                                            |
|--------------|--------------------------------------------------------------------------------------------------------------------|
| `teitok.xsd` | The output contract itself.                                                                                        |
| `xml.xsd`    | Local, trimmed copy of the W3C `xml.xsd`, providing `xml:lang`. Vendored so validation never needs network access. |

## Provenance

`teitok.xsd` is **not** copied from teitok.org or any upstream TEI schema
release. There is no single canonical upstream `.xsd` that describes this
pipeline's exact TEITOK profile (a constrained TEI subset with ATRIUM-specific
conventions such as `<div type="MarginTextZone-P">` and the `@cnec` NER
attribute). Instead:

- **Source of truth:** `api_util/teitok_alto.py::write_teitok_merged()`, the
  writer behind the `stats` stage and every document in `data_samples/TEITOK/`.
- **Method:** hand-authored to encode the element/attribute shapes that writer
  produces, on both its code paths (with a source ALTO file, i.e. bbox-annotated
  and `<facsimile>`-bearing; and the text-only fallback without one).
- **Verified by:** `tests/test_validate_teitok.py::TestRealWriterRoundTrip`,
  which *runs* the writer and validates its output. That round-trip is the only
  thing that keeps this schema honest — see "How this schema went wrong once".
- **`xml.xsd`:** trimmed subset of `https://www.w3.org/2001/xml.xsd`,
  retaining only `xml:lang` (plus `xml:space`, `xml:base`, `xml:id` for
  forward compatibility), vendored for network-free validation per issue
  #28's schema-management decision.

## No target namespace — deliberate

`teitok.xsd` declares **no `targetNamespace`**. That is not an oversight.

`write_teitok_merged()` opens every document with:

```xml
<TEI xmlnsoff="http://www.tei-c.org/ns/1.0" lang="cs">
```

`xmlnsoff`, not `xmlns`; `lang`, not `xml:lang`. This is TEITOK's own
convention for keeping documents *out* of the TEI namespace so its tooling can
address elements unprefixed, and it has been the writer's output since
`api_util/teitok_alto.py` was added. So in real documents `xmlnsoff` and `lang`
are ordinary attributes — both are declared on `<TEI>` in the schema, because an
undeclared attribute is itself a validation error.

Documents that *do* carry the real TEI namespace also exist: older exports (the
`CTX00000000*` samples), and anything through `POST /rescale`, since
`service/rescale.py` is a regex transform that preserves whatever namespace its
input declared. `api_util/validate_teitok_xml.py::_strip_tei_namespace` strips
the TEI namespace before validating, so one schema covers both conventions
without duplicating it.

### Caveat: `xmllint` is only a partial cross-check

Because the namespace normalization lives in the Python validator, a bare

```bash
xmllint --noout --schema schemas/teitok/teitok.xsd <file>
```

agrees with the gate only for **no-namespace** documents — i.e. anything the
writer produces. Point it at a namespaced document (`data_samples/TEITOK/*`) and
it reports `No matching global declaration available for the validation root`,
which is an artefact of the missing normalization step, *not* a real violation.
Use `python3 api_util/validate_teitok_xml.py <dir>` as the authority; reach for
`xmllint` as an independent second opinion on writer-shaped output only.

## Which emitters are gated, and how

Three code paths in this repo write `*.teitok.xml`. They are covered to
different depths, on purpose:

| Emitter         | Entry point                                                                               | Gate                                                                |
|-----------------|-------------------------------------------------------------------------------------------|---------------------------------------------------------------------|
| `stats` stage   | `api_4_stats.sh` → `api_util/summarize_nt_udp.py` → `teitok_alto.py::write_teitok_merged` | **Full XSD**, hard fail before `atrium_paradata.py finish`          |
| flexiconv       | `api_flexiconv.sh` → `api_util/flexiconv_convert.py`                                      | **Well-formedness + `<TEI>` root** (`--wellformed-only`), hard fail |
| `POST /rescale` | `service/api.py` → `service/rescale.py`                                                   | **Advisory**: `schema_valid` / `schema_errors` in the response      |

flexiconv output comes from a third-party converter across 13 input formats
(`FLEXICONV_FORMATS` in `config_api.txt`) and lands in the *same*
`$TEITOK_OUTPUT_DIR`. Gating it against this schema would reject documents for
being a *different* TEITOK profile rather than a broken one, so today it is only
checked for well-formedness. To promote it to the full XSD: capture a genuine
flexiconv document, add it as a fixture, extend the schema to cover its shapes,
then drop `--wellformed-only` from `api_flexiconv.sh`.

`/rescale` reports rather than enforces because the endpoint faithfully
transforms whatever it is handed, including legacy documents that predate this
schema; failing them would break a working tool. `schema_valid` is `null` when
no verdict could be reached, so callers can tell "not conformant" from
"not checked".

## Keeping this schema current

If `teitok_alto.py`'s writer changes (new attributes, new element types, new
fallback branches), `teitok.xsd` must be updated in the same PR — this schema
describes the writer's actual contract, not an aspirational one.
`TestRealWriterRoundTrip` will fail first and point at the drift. Never fix such
a failure by loosening the schema without checking the writer diff: a genuinely
new element belongs in the schema, a *renamed* one is usually a bug.

Fixtures live in `tests/fixtures/teitok/` — **not** in `data_samples/TEITOK/`,
which is the default `TEITOK_OUTPUT_DIR` (`config_api.txt`). Keeping the
deliberately-invalid `CTX_invalid.teitok.xml` out of that directory is what
stops a default-config run from failing its own gate on a fixture.
`data_samples/TEITOK/` holds real example output only, and
`test_committed_data_samples_are_conformant` keeps it valid.

## How this schema went wrong once

Worth recording, because the failure mode is easy to repeat. The first version
of this gate shipped with a `targetNamespace` of `http://www.tei-c.org/ns/1.0`
and `elementFormDefault="qualified"`, and with three fixtures hand-authored
using `xmlns=`. Every fixture test passed. The gate nevertheless rejected
**100% of real pipeline output**, because the writer emits `xmlnsoff` — so
`api_4_stats.sh` would have halted on every run that produced TEITOK, and
`POST /enrich` would have failed every request.

Hand-authored fixtures validated against a hand-authored schema confirm each
other and nothing else. The fix that matters is not the namespace edit; it is
`TestRealWriterRoundTrip`, which makes the real writer the arbiter.

## Future direction

Per `teitok_alto.py`'s own in-repo note, resolution-independent/relative
bounding-box coordinates are the preferred long-term direction pending
TEITOK-team confirmation. If that lands, `bboxType` in `teitok.xsd` will need
to accept the new coordinate format alongside or instead of the current
`"x1 y1 x2 y2"` absolute-pixel pattern.
