# `schemas/teitok/` — Pinned TEITOK output contract (issues #28, #38)

This directory vendors the XSD schema that `.teitok.xml` files must satisfy
before the pipeline packages them for the LINDAT dataset release.

It is the *contract* only. What the format is built from (TEI P5, TEITOK's conventions, the
annotation vocabularies), how each element is composed from the pipeline's inputs, which tools
write or read it, and the pitfalls a valid file can still hit are in the main README:
[The format and the standards it builds on](../../README.md#the-format-and-the-standards-it-builds-on),
[How a TEITOK document is composed](../../README.md#how-a-teitok-document-is-composed),
[Tools that generate or read TEITOK](../../README.md#tools-that-generate-or-read-teitok),
[Pitfalls](../../README.md#pitfalls).

## Files

| File         | Purpose                                                                                                            |
|--------------|--------------------------------------------------------------------------------------------------------------------|
| `teitok.xsd` | The output contract itself.                                                                                        |
| `xml.xsd`    | Local, trimmed copy of the W3C `xml.xsd`, providing `xml:lang`. Vendored so validation never needs network access. |

## What the writer emits ("TEITOK format 2")

`api_util/teitok_alto.py::write_teitok_merged()` stamps every document with
`<application ident="atrium-nlp-enrich" version="teitok-2">`. Since 2026-09 it follows the
conventions of the TEITOK tools themselves (flexiconv, flexipipe, teitok-tools,
xmltokenizer), not just a shape of its own:

| Aspect      | Format 2                                                                                                                                                                                                                                                                                                                                                                      |
|-------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Root        | `<TEI xmlnsoff="http://www.tei-c.org/ns/1.0" lang="cs">`. `lang` comes from ALTO `LANG` (majority), else the UDPipe model name, else it is omitted. It is mirrored in `profileDesc/langUsage/language@ident`.                                                                                                                                                                 |
| Spacing     | Text-faithful. Tokens are inline in `<s>`, whitespace between `</tok>` and the next `<tok>` is a space and none is `SpaceAfter=No`. This also holds across `<lb/>` and `</name>` (an entity's trailing space sits inside `</name>`). `join="right"` is kept as the TEI-P5 marker.                                                                                             |
| Tokens      | `<tok id type ord lemma upos xpos feats head deprel join bbox>`. `@head` is the head word's `@id`; `@ord` is the CoNLL-U ID.                                                                                                                                                                                                                                                  |
| MWT         | `<tok id bbox>abych<dtok id form ord lemma …/><dtok …/></tok>`. The surface token carries the text and bbox; `<dtok>` carries the words.                                                                                                                                                                                                                                      |
| Ids         | `w-N` (document-global), `w-N.K` (dtok), `s-N`, `n-N` (name), `facs-P` (surface), `pb-P`, `lb-P.L`, `b-P.K` (div), `fig-P.K`. The atrium_document record's `entities[].teitok_ref` and `pages[].teitok_surface` are these local ids.                                                                                                                                          |
| Entities    | `<name id type sameAs>`. `@type` is PER/ORG/LOC/MISC (`api_util/ner_types.py`), and the raw label goes in `@cnec`, `@onto`, `@archaeo` or `@label`.                                                                                                                                                                                                                           |
| Layout      | `<div type="TextBlock" [subtype=<ALTO TAGREFS label>] [lang]>`, or `type="text"` without ALTO. `<pb n id [facs corresp bbox]>`, `<figure>`. A `<div>` stays on the page it opens on.                                                                                                                                                                                          |
| Pages       | From the layout (ALTO, a converted file, else the input table's pages via stage 1's rows file), never from UDPipe's chunks (issue #38). `<pb/>` may sit inside `<s>` and `<name>` when a sentence or entity runs over a page break; pages only move forward; `@n` is the label (`"I"`, `"7a"`); `@facs`, `@corresp` and `@bbox="0 0 W H"` only for a page with a `<surface>`. |
| Coordinates | `bbox="x1 y1 x2 y2"`: non-negative page-image pixels, origin at the page's top-left corner (`BBOX_ORIGIN=page`, the TEITOK norm). `BBOX_ORIGIN=printspace` measures from the ALTO PrintSpace instead. `<surface lrx lry>` (and `pb@bbox`) is always the extent the boxes are measured in. Punctuation split off an OCR string has no bbox; the word keeps the string's.       |
| Header      | `notesStmt/note[@n="orgfile"]` (the ALTO file, the converted file's original, or the input table); `revisionDesc/change@type` = `converted`, `tagged` + `subtype="parsed"`, `ner` (the phases TEITOK/flexicorp detect); `rescaled` after `/rescale` or `fix_teitok_bboxes.py`.                                                                                                |
| Projection  | Opt-in (atrium-project#70, `api_util/teitok_project.py`): `encodingDesc/classDecl/taxonomy/category[@id @corresp]/catDesc`, `application@id` (`app-pc`, `app-llm-enrich`, `app-kw`), `profileDesc/textClass/keywords[@scheme @resp @lang @corresp]/term[@type @ref @cert @score @corresp @n]`, `pb@ana` (`#pcat-…`), and `change@type="enriched"`. Absent unless switched on. |

Format-1 documents (before 2026-09: TEI namespace, `CTX.s1.w1` ids, `MarginTextZone-P`,
one `<tok>` per line) still validate — `tests/fixtures/teitok/legacy/CTX_format1.teitok.xml`
keeps it that way — except for negative bbox coordinates, which the old PrintSpace shift
could produce. Set `REGENERATE_TEITOK=true` once to rewrite them.

The stamp stays `teitok-2` with the opt-in record projection (atrium-project#70) too: the
schema grew by the header elements in the table above and `pb@ana`, all optional, so a file
without a projection validates exactly as before; an older gate refuses a projected file, so
the projector and this XSD ship in the same release. `tests/fixtures/teitok/CTX_projected.teitok.xml`
is `data_samples/TEITOK/CTX000000001.teitok.xml` projected (regenerate with
`python -m tests.test_teitok_project`).

The stamp stayed `teitok-2` through issue #38: the schema only grew (`<pb>` inside `<s>` and
`<name>`, `pb@facs` optional, `pb@bbox`), so every earlier format-2 file still validates, and
readers that check the stamp (the hub's E2E assertion) keep working. What changed is where
`<pb>` goes: a format-2 file written before #38 may have page breaks at UDPipe chunk starts
and a sentence's second-page tokens on its first page. Rewrite such files with
`REGENERATE_TEITOK=true`.

### Deviations from upstream conventions (known, kept)

Checked against the upstream code (teitok.org itself is not reachable from the build
machines): **U1** split-off punctuation now follows flexiconv (no box); **U2** `pb@bbox`
now follows flexiconv's hOCR/xpdf output. Two remain, both about a TEITOK *project* rather
than the file: **U3** the file name (`<doc_id>.teitok.xml`; a project wants
`xmlfiles/<doc_id>.xml`) and a bare `orgfile` (a project keeps originals in `Originals/`);
**U4** the tag attribute is `xpos`, teitok-tools' default is `pos`, so a project declares
`xpos` in `settings.xml`. The README's "Importing into a TEITOK project" gives the steps.

## Provenance

`teitok.xsd` is **not** copied from teitok.org or any upstream release: no TEITOK tool
ships an XSD, RNG or DTD. It describes exactly the subset of TEI this writer emits:

- **Source of truth:** `api_util/teitok_alto.py::write_teitok_merged()`, the
  writer behind the `stats` stage and every document in `data_samples/TEITOK/`.
- **Method:** hand-authored to encode the element/attribute shapes that writer
  produces, on both its code paths (with a source ALTO file, i.e. bbox-annotated
  and `<facsimile>`-bearing; and the text-only fallback without one).
- **Verified by:** `tests/test_validate_teitok.py::TestRealWriterRoundTrip`,
  which *runs* the writer and validates its output, and
  `test_committed_samples_are_what_the_writer_produces_today`, which regenerates
  `data_samples/TEITOK/` and compares. The curated fixtures in
  `tests/fixtures/teitok/` are real writer output too. See "How this schema went wrong once".
- **`xml.xsd`:** trimmed subset of `https://www.w3.org/2001/xml.xsd`,
  retaining only `xml:lang` (plus `xml:space`, `xml:base`, `xml:id` for
  forward compatibility), vendored for network-free validation per issue
  #28's schema-management decision.

## Upstream conformance

A schema derived from the writer can only say that the output kept its shape. It said
nothing about whether the output meant the same to TEITOK: format 1 validated while
every upstream reader saw a space after every token. Conformance is therefore tested
separately, by reading the output the way the tools do:

- `tests/test_teitok_conformance.py` (stdlib, always runs) reconstructs the CoNLL-U from
  the XML the way flexiconv's `load_teitok` and flexipipe do: space = a space character in
  `tok.tail`, words from `<dtok>`, heads through `@id`. It then compares sentence texts,
  every `SpaceAfter`, MWT, heads, ids and entity attributes with the input.
- The same file's last test repeats the round-trip through the pinned flexiconv
  (`requirements_flexiconv.txt`, v0.3.10) when it is installed. The `teitok-schema`
  workflow installs it for that step.
- `api_util/validate_teitok_xml.py --profile core` checks rules that hold for *any*
  TEITOK document: `<TEI>` root, `<text>`, `@id` unique across all elements, resolvable
  `@head` and `#` references (`sameAs`, `corresp`, and the projection's `ana`, `resp`,
  `scheme`), no whitespace after a `join="right"` token, and non-negative bboxes.
- The default profile, `contract`, is what the stage-4 gate runs: the XSD for every file,
  plus `core` and `lint_writer` for files stamped `teitok-2`. `lint_writer` checks the page
  rules the XSD cannot state: `pb-K` strictly increasing, `lb-P.L`/`b-P.K`/`fig-P.K` on page P,
  `lb` numbers increasing on a page, `pb@corresp` naming a `<surface>`, and every `<surface>`
  with its `<pb>`. Before issue #38 the gate was the XSD alone, which types ids as plain
  strings: a duplicate `pb` id or a `sameAs` pointing nowhere passed it. `lint_projection`
  (also for `teitok-2` files) checks that each projection pointer names the right kind of
  element: `pb@ana` a `<category>`, `keywords@resp` an `<application>`, `keywords@scheme` a
  `<taxonomy>`, and a page reference (`term`/`keywords` `@corresp`) a `<pb>`.
- `tests/test_teitok_pages.py` also reads a `<pb/>` inside `<s>` and `<name>` through the pinned
  flexiconv. (xmltokenizer, too, treats `pb`/`lb`/`cb` as anchors that may sit inside `<s>`.)

Upstream references used: flexiconv `v0.3.10` (`a982b88`), flexipipe `73188c5`,
xmltokenizer `4b05623`, flexicorp `61f6543`, teitok-tools `2968265`.

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

Documents that *do* carry the real TEI namespace also exist: format-1 exports, and
anything through `POST /rescale` that came in namespaced, since `service/rescale.py` is a
regex transform that preserves whatever namespace its input declared.
`api_util/validate_teitok_xml.py::_strip_tei_namespace` strips the TEI namespace before
validating, so one schema covers both conventions without duplicating it.

### Caveat: `xmllint` is only a partial cross-check

Because the namespace normalization lives in the Python validator, a bare

```bash
xmllint --noout --schema schemas/teitok/teitok.xsd <file>
```

agrees with the gate only for **no-namespace** documents — i.e. anything the
writer produces, including `data_samples/TEITOK/*`. Point it at a namespaced document
(`tests/fixtures/teitok/legacy/`) and it reports `No matching global declaration
available for the validation root`, which is an artefact of the missing normalization
step, *not* a real violation. Use `python3 api_util/validate_teitok_xml.py <dir>` as the
authority.

## Which emitters are gated, and how

Four code paths in this repo write `*.teitok.xml`. They are covered to
different depths, on purpose:

| Emitter                | Entry point                                                                                                                                                                                                                                                  | Gate                                                                                                                                                                                                 |
|------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `stats` stage          | `api_4_stats.sh` → `api_util/summarize_nt_udp.py` → `teitok_alto.py::write_teitok_merged` (layout from ALTO, or with `FLEXICONV_ANNOTATE` from a converted file via `api_util/teitok_layout.py`, else the rows file); also behind `POST /enrich` and `/jobs` | **`contract`** over `$TEITOK_OUTPUT_DIR` minus `$TEITOK_FLEXICONV_DIR`, hard fail (exit 5) before `atrium_paradata.py finish`; the service answers 500, and reports `teitok_schema_valid` on success |
| flexiconv              | `api_flexiconv.sh` → `api_util/flexiconv_convert.py`                                                                                                                                                                                                         | **TEITOK-core** (`--profile core`) over `$TEITOK_FLEXICONV_DIR`, hard fail                                                                                                                           |
| `POST /rescale`        | `service/api.py` → `service/rescale.py`                                                                                                                                                                                                                      | **Advisory**: `schema_valid` / `schema_errors` in the response                                                                                                                                       |
| `fix_teitok_bboxes.py` | the CLI, rewriting files in place (`api_util/page_boxes.py`, like `/rescale`)                                                                                                                                                                                | **None of its own**: the next stage-4 run gates the directory; boxes are clamped to their page, so a shift cannot produce the negative values the gate rejects                                       |

flexiconv output comes from a third-party converter across the `FLEXICONV_FORMATS`
of `config_api.txt`. It is a *different* TEITOK profile: no `<s>`; `<p>` text for
txt/md/docx/pdf/html; `<tok bbox>` + `<lb/>` for PAGE XML/hOCR/ALTO; split punctuation
without `@id`. This schema would reject it for not being this writer's output rather
than for being broken. So it goes to its own `$TEITOK_FLEXICONV_DIR` (default
`$TEITOK_OUTPUT_DIR/flexiconv`), which `api_4_stats.sh` excludes (`--exclude`), and it is
checked with the core profile. Real flexiconv v0.3.10 output is committed as fixtures in
`tests/fixtures/teitok/flexiconv/`.

With `FLEXICONV_ANNOTATE=true` that output is an intermediate. Its text is annotated by stages
2–3, and stage 4 writes **this** format (full XSD) with the converted file as the layout source.
Only the header differs: `orgfile` names the original document, a
`<change who="flexiconv" type="converted">` replaces `altoconvert`, and flexiconv's own
`<application>` is listed. The page images keep flexiconv's names (`<graphic url>`, `<pb facs>`), and
blocks carry the source element as `subtype` (`p`, `head`, `item`, …). Fixtures:
`tests/fixtures/teitok/flexiconv/annotated/*.conllu`, which is hand-written NER-merged CoNLL-U for
the committed conversions (`tests/test_flexiconv_annotate.py`).

A converted file can also arrive through the service: a `*.teitok.xml` uploaded to
`POST /enrich` is pre-checked with `core` (422 with the diagnostics) and then takes the
`FLEXICONV_ANNOTATE` path above inside the service's workspace.

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
new element belongs in the schema, a *renamed* one is usually a bug. A change readers
can notice also bumps `WRITER_FORMAT` in `teitok_alto.py`.

After a writer change, regenerate the committed examples and the curated fixtures (the
rows files are what stage 2 keeps next to the CoNLL-U, and stage 4 passes them too):

```bash
python3 - <<'EOF'
import sys; sys.path[:0] = [".", "api_util"]
from api_util.page_rows import read_rows
from api_util.teitok_alto import write_teitok_merged
for d in ("CTX000000001", "CTX000000002", "CTX000000003"):
    write_teitok_merged(f"data_samples/UDP_NE/{d}/{d}.conllu", f"data_samples/TEITOK/{d}.teitok.xml",
                        f"data_samples/ALTO/{d}.alto.xml", doc_id=d,
                        model_nametag="nametag3-czech-cnec2.0-240830",  # as their paradata records
                        rows=read_rows(f"data_samples/UDP/{d}.rows.tsv"))
EOF
```

Fixtures live in `tests/fixtures/teitok/` — **not** in `data_samples/TEITOK/`,
which is the default `TEITOK_OUTPUT_DIR` (`config_api.txt`). Keeping the
deliberately-invalid `CTX_invalid.teitok.xml` out of that directory is what
stops a default-config run from failing its own gate on a fixture.
`data_samples/TEITOK/` holds real example output only.

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
`TestRealWriterRoundTrip`, which makes the real writer the arbiter. The same lesson
applied one level up in 2026-09: a writer-derived schema confirms the writer and nothing
else, which is why upstream conformance now has its own test lane.

## Future direction

Resolution-independent (relative, 0–1) bounding boxes were proposed to the TEITOK
maintainers (ufal/flexiconv#1); there has been no reply, and every TEITOK tool writes
absolute page-image pixels. If relative coordinates are ever adopted upstream,
`bboxType` will need to accept them alongside or instead of the current
`"x1 y1 x2 y2"` integer pattern.
