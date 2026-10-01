<p align="center">
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.8+-blue.svg" title="Python Version"></a>
  <a href="https://lindat.mff.cuni.cz/services/udpipe/api-reference.php"><img src="https://img.shields.io/badge/API-UDPipe%202-0055A4.svg" title="UDPipe 2 API (Lindat)"></a>
  <a href="https://lindat.mff.cuni.cz/services/nametag/api-reference.php"><img src="https://img.shields.io/badge/API-NameTag%203-0055A4.svg" title="NameTag 3 API (Lindat)"></a>
  <a href="https://github.com/ufal/ker"><img src="https://img.shields.io/badge/dep-KER-lightgrey.svg" title="KER Keyword Extraction"></a>
  <a href="https://opensource.org/license/mit/"><img src="https://img.shields.io/github/license/ufal/atrium-nlp-enrich" title="MIT License"></a>
  <a href="https://atrium-research.eu/"><img src="https://img.shields.io/badge/funded%20by-ATRIUM-8A2BE2.svg" title="ATRIUM Project"></a>
</p>

---

# 📦 ALTO XML Files Postprocessing Pipeline - NLP Enrichment of text

This project provides a workflow for processing text stored in CSV (XLSX) with NLP services. It takes ordered text
and extracts high-level linguistic features like Named Entities (NER) with tags and CONLL-U files with
lemmas & part-of-sentence tags, and keywords (KER) per page/document.

---

> [!CAUTION]
> This repository is a follow-up to main ALTO XML postprocessing [GitHub repository](https://github.com/ufal/atrium-alto-postprocess),
> a part of ATRIUM project dedicated to ALTO-2-TXT workflow and collection of statistics and from text content
> of the documents (text and bounding boxes ordered by LayoutReader) recorder in CSV (XLSX) tables as a `text` column [^2].

## Table of contents

- [TEITOK XML — Unified Output Format](#teitok-xml--unified-output-format)
  - [The format and the standards it builds on](#the-format-and-the-standards-it-builds-on)
  - [How a TEITOK document is composed](#how-a-teitok-document-is-composed)
  - [Importing into a TEITOK project](#importing-into-a-teitok-project)
  - [Tools that generate or read TEITOK](#tools-that-generate-or-read-teitok) · [Pitfalls](#pitfalls)
- [ ⚙️ Setup](#-setup)
- [Workflow Stages](#workflow-stages)
  - [Step 1: Prepare CSVs with texts from Page-Specific ALTOs](#-step-1-prepare-csvs-with-texts-from-page-specific-altos)
  - [Step 2: Extract NER and CONLL-U](#-step-2-extract-ner-and-conll-u)
    - [Configuration ⚙️](#configuration-)
    - [Execution Pipeline](#execution-pipeline)
      - [I. Generate Manifest](#1-generate-manifest)
      - [II. UDPipe Processing (Morphology & Syntax)](#2-udpipe-processing-morphology--syntax)
      - [III. NameTag Processing (NER tags)](#3-nametag-processing-ner-tags)
      - [IV. Generate Statistics](#4-generate-statistics)
- [Output Structure](#output-structure)
- [EXTRA: Extract Keywords (KER / YAKE / KeyBERT)](#extra-extract-keywords-ker--yake--keybert)
- [EXTRA: Converting Other Input Formats with flexiconv](#extra-converting-other-input-formats-with-flexiconv)
- [EXTRA: LLM Semantic Enrichment (Vocabulary Mapping)](#extra-llm-semantic-enrichment-vocabulary-mapping)
  - [Reviewing the vocabulary](#reviewing-the-vocabulary) · runbooks: [vocabulary](data_samples/vocab/RUNBOOK.md) · [prompt & output](prompts/RUNBOOK.md)
- [EXTRA: REST API Service](#extra-rest-api-service)
- [Paradata Logs](#paradata-logs)
  - [`<OUTPUT_DIR>/paradata/` — structured run logs 📂](#output_dirparadata--structured-run-logs-)
  - [`<OUTPUT_DIR>/processing.log` — human-readable runtime log 📄](#output_dirprocessinglog--human-readable-runtime-log-)
  - [`TEMP/` — intermediate working files 📂](#temp--intermediate-working-files-)
  - [Document record schema 📑](#document-record-schema-)
  - [One-command pipeline run (`run_pipeline.py`)](#one-command-pipeline-run-run_pipelinepy)
- [Acknowledgements](#acknowledgements-)

## TEITOK XML — Unified Output Format

**TEITOK XML** (`.teitok.xml`) is the primary enriched output format of this pipeline. It is the
[TEI](https://tei-c.org/)-based XML format of the [TEITOK](https://www.teitok.org/) corpus
platform, carrying spatially-grounded linguistic and NER annotations produced by UDPipe and
NameTag. The files follow the conventions the TEITOK tools themselves read and write
([flexiconv](https://github.com/ufal/flexiconv), [flexipipe](https://github.com/ufal/flexipipe),
teitok-tools), so they can go straight into a TEITOK project (see
[Importing into a TEITOK project](#importing-into-a-teitok-project)) and be read by flexiconv
and flexipipe. What each tool keeps on a round trip is listed there: spacing survives all of
them; two upstream readers still lose something (multi-word tokens in flexiconv, dependency
heads in flexipipe).

Each document in the collection is serialised as a single `.teitok.xml` file that integrates four
layers of information in a consistent, machine-readable structure:

| Layer                   | Content                                                                                                                                                                                                                     |
|-------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Layout**              | Page, text-block, and line boundaries with pixel bounding boxes from the source ALTO XML (page origin), scaled to the stored page images when those are available; without a layout, the pages and lines of the input table |
| **Morphology & Syntax** | Per-token lemma, UPOS/XPOS tags, morphological features, and dependency relations produced by UDPipe 2; multi-word tokens ("abych" = aby + bych) as one surface `<tok>` with `<dtok>` words                                 |
| **Named Entities**      | Entity spans with a coarse category (`PER`, `ORG`, `LOC`, `MISC`) and the raw NameTag label in `@onto` (OntoNotes, the default model), `@cnec` (CNEC 2.0) or `@archaeo` (the archaeological model)                          |
| **Facsimile links**     | `<surface>` elements in `<facsimile>` that tie each page to its companion image, enabling TEITOK's side-by-side text/image view                                                                                             |

### Why TEITOK XML?

Storing all enrichment layers in a single interoperable format offers several practical advantages
over keeping CoNLL-U, TSV, and image files in separate silos:

- 🔍 **Full-text and attribute search** — TEITOK's built-in CQL/XPATH query engine lets users
  search across lemmas, NER types, POS tags, and raw text simultaneously.
- 🏷 **Named entity access** — entity spans (`<name type="PER" onto="PERSON">`) are first-class XML
  elements: queryable, stylable, and exportable independently of the surrounding tokens.
- 🖱 **Mouseover information** — hovering over any token in the TEITOK GUI surfaces its lemma,
  morphological features, and dependency relation without leaving the page view.
- 🖼 **Page visualisation with spatial overlays** — bounding box coordinates on every `<tok>`,
  `<lb>`, and `<div>` are used by TEITOK's facsimile viewer to overlay text highlights directly
  onto the scanned page image, making OCR quality immediately visible.
- 📐 **Layout-aware structure** — text blocks (`<div type="TextBlock">`), lines (`<lb>`),
  and graphical elements (`<figure>`) preserve the physical layout of the original document.
- 🔗 **Interoperability** — TEI/XML is a widely adopted standard in digital humanities; the files
  can be ingested by other TEI-aware tools (e.g. eXist-db, Oxygen XML Editor) without conversion.

### The format and the standards it builds on

TEITOK XML is not a schema of its own; it layers three sets of conventions, and this repository
pins one profile of them:

- **TEI P5** ([Guidelines](https://tei-c.org/guidelines/p5/)) gives the document skeleton:
  `teiHeader` (file, encoding and revision description), `facsimile`/`surface`/`graphic` for the
  page images, `text`/`body`/`div` for text blocks, `pb`/`lb` for page and line beginnings, `s`
  for sentences, `name` for named entities and `figure` for graphical regions.
- **TEITOK** (the corpus platform, [teitok.org](https://www.teitok.org/)) adds the tokenized
  layer it searches and displays: every token is a `<tok>` element inline in the running text, a
  multi-word token keeps its surface form with one `<dtok>` per syntactic word, the annotation
  lives in token attributes, and any element can carry `@bbox="x1 y1 x2 y2"` in the pixels of its
  page image. TEITOK files carry no TEI namespace (`xmlnsoff` keeps the URI without declaring it)
  and use plain `@id`s.
- **Annotation vocabularies** are those of the tools that produced them: Universal Dependencies
  (CoNLL-U) for `upos`, `feats`, `deprel` and `head`; the UDPipe model's own tag set for `xpos`;
  the NameTag model's entity labels (OntoNotes 5 by default, CNEC 2.0, or the archaeological types
  of #7) in `@onto`/`@cnec`/`@archaeo`, mapped to four coarse types in `@type` (`PER`, `ORG`,
  `LOC`, `MISC`) by [api_util/ner_types.py](api_util/ner_types.py) 📎; a label no table knows
  becomes `type="MISC"` with the raw label in `@label`.
- **Format 2** is this repository's profile: [schemas/teitok/teitok.xsd](schemas/teitok/teitok.xsd) 📎
  plus the contract checks below, stamped `version="teitok-2"` in `appInfo`. The hub's document
  record ([atrium_document.schema.json](atrium_document.schema.json) 📎) points into it by id.

### TEITOK XML structure at a glance

An excerpt of [data_samples/TEITOK/CTX000000001.teitok.xml](data_samples/TEITOK/CTX000000001.teitok.xml) 📎
(attributes shortened; the header is described below):

```xml
<TEI xmlnsoff="http://www.tei-c.org/ns/1.0" lang="cs">
  <teiHeader> ... </teiHeader>
  <facsimile>
    <surface id="facs-1" lrx="1654" lry="2339">
      <graphic url="CTX000000001-1.png"/>
    </surface>
  </facsimile>
  <text><body>
    <pb n="1" id="pb-1" facs="CTX000000001-1.png" corresp="#facs-1" bbox="0 0 1654 2339"/>
    <div type="TextBlock" id="b-1.1" bbox="220 160 1420 280">
      <s id="s-1" text="Výzkumná zpráva č. 1/2024 — Hradiště u Horní Mezí ...">
        <lb id="lb-1.1" bbox="220 160 1320 200"/><tok id="w-1" type="w" ord="1" lemma="výzkumný" upos="ADJ" head="w-2" deprel="amod" bbox="220 160 420 194">Výzkumná</tok> <tok id="w-2" ...>zpráva</tok> <tok id="w-3" ... join="right" bbox="...">č</tok><tok id="w-4" type="pc" ...>.</tok> ...
        <name id="n-3" type="LOC" cnec="gu" sameAs="#w-9 #w-10 #w-11 #w-12"><tok id="w-9" ...>Hradiště</tok> ... <tok id="w-12" ...>Mezí</tok>
          </name><lb id="lb-1.2" bbox="220 210 1220 250"/><tok id="w-13" ...>Terénní</tok> ...
      </s>
    </div>
  </body></text>
</TEI>
```

The conventions, which are the same as the TEITOK tools' (full list:
[schemas/teitok/README.md](schemas/teitok/README.md) 📎):

- **Namespace-off root** (`xmlnsoff`, plain `@id`). `lang` comes from the ALTO `LANG`
  attributes, else from the UDPipe model name; it is omitted when neither says.
- **Spacing is text-faithful**: tokens sit inline, a space between `</tok>` and the next `<tok>`
  is a space in the text, and no whitespace means `SpaceAfter=No` ("č." above).
  `join="right"` marks the same thing for TEI-P5 tools.
- **Ids** are local to the document: `w-N` tokens (`w-N.K` for the `<dtok>` words of a
  multi-word token), `s-N`, `n-N` entities, `facs-P` surfaces, `pb-P`, `lb-P.L`, `b-P.K`. `@head`
  points at the head word's id. The document record (`--document-json`) uses the same ids:
  `entities[].teitok_ref` = `n-N` and `pages[].teitok_surface` = `facs-P`.
- **Bounding boxes** are `x1 y1 x2 y2` pixels measured from the page's top-left corner, and
  `<surface lrx lry>` (and `pb@bbox="0 0 W H"`, as flexiconv writes it) gives the extent they
  are measured in (see
  [ALTO-to-TEITOK XML Generation and Coordinate Alignment](#alto-to-teitok-xml-generation-and-coordinate-alignment)).
  Punctuation that UDPipe splits off an OCR word ("č." → "č" + ".") has no box of its own;
  the word keeps the OCR string's box, as in flexiconv's ALTO and hOCR import.
- **Pages come from the layout** (ALTO, a converted file, or the table's `page_num`), never
  from UDPipe's chunks. A sentence (or an entity) that runs over a page break keeps its
  tokens together and gets the `<pb/>` inside it, before the first token of the new page —
  the anchor xmltokenizer and flexiconv allow there. `pb@n` is the page label (`"I"`, `"7a"`);
  `pb@facs`/`@corresp` appear only for a page that has an image (a `<surface>`).
- **Header**: `notesStmt/note[@n="orgfile"]` names the source ALTO file, `appInfo` names the
  writer (`atrium-nlp-enrich`, format `teitok-2`) and the UDPipe/NameTag models, and
  `revisionDesc/change@type` records the phases (`converted`, `tagged`/`parsed`, `ner`).

Every file is checked before the stage finishes: against
[schemas/teitok/teitok.xsd](schemas/teitok/teitok.xsd) 📎, and — for files in the current format —
for unique ids, resolvable `sameAs`/`corresp`/`head` references and the page rules (`pb-K`
increasing, `lb-P.L` on page P). A file that fails stops the stage with exit code `5`.
`tests/test_teitok_conformance.py` reads the writer's output the way flexiconv does to keep it
TEITOK-conformant.

> [!NOTE]
> TEITOK XML is generated by Step 4 of this pipeline (`api_4_stats.sh`) when
> `SAVE_TEITOK=true`. The source ALTO XML files must be present in `INPUT_ALTO_DIR`
> for spatial coordinates to be included. Without a layout, TEITOK XML is still produced:
> its pages and lines are those of the input table (`<pb>` per page, `<lb>` per line), without
> bounding boxes and without a facsimile. If your documents are not in
> ALTO format, see [EXTRA: Converting Other Input Formats with flexiconv](#extra-converting-other-input-formats-with-flexiconv).

> [!IMPORTANT]
> The format changed in 2026-09 ("format 2": inline spacing, `<dtok>`, `w-N` ids, page-origin
> bboxes). A resumed run keeps existing `.teitok.xml` files. Rewrite them once, so that old and new
> files do not mix: `REGENERATE_TEITOK=true bash api_4_stats.sh`, or set it in `config_api.txt`.
>
> **Pages changed after v0.21.0** (issue #38): runs up to v0.21.0 counted UDPipe's ~900-word chunks
> as pages in `NE/<doc>-N.tsv`, `summary_ne_counts.csv`, `UDP_NE/<doc>.csv` and the document record,
> and left a sentence that runs over a page on its first page in the TEITOK. To correct a collection:
> re-run it from stage 1 (which writes the rows files pages now come from); or, keeping the
> CoNLL-U and NE files, rebuild the rows files with stage 1, copy each `TEMP_TXT_DIR/<doc>.rows.tsv`
> to `UDP/`, re-split the NE files by page without calling NameTag
> (`python3 api_util/page_rows.py resplit --conllu-dir <OUTPUT_DIR>/UDP --ne-dir <OUTPUT_DIR>/NE`),
> delete `UDP_NE/<doc>/<doc>.csv` and run stage 4 with `REGENERATE_TEITOK=true`. In-place document
> records keep entities under their old pages: re-seed them from the record before nlp-enrich.

### How a TEITOK document is composed

Nothing in the default pipeline edits TEITOK in place. Stage 4 builds each file in one pass from
the formats the earlier stages leave on disk, so every element can be traced to one of them (the
opt-in [record projection](#record-projection-onto-teitok-opt-in) is the one later writer, and
it touches only the header and `pb/@ana`):

| Source (stage)                                                                                                                                                       | Format on disk                                                                                                                           | Becomes in the TEITOK file                                                                                                                                                                                                                                        |
|----------------------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Line table: alto-postprocess `DOC_LINE_CATEG/<doc>.csv`, or any CSV/XLSX with the same columns, or the rows of a flexiconv conversion (stage 1, `api_1_manifest.sh`) | `file`, `page_num`, `line_num`, `text` → `TEMP_TXT_DIR/<doc>.txt` (one line per row) and `<doc>.rows.tsv` (page, line, page label, text) | the text itself; the pages (`<pb n>` = the table's `page_label`, when it has that column) and lines of every token; without a layout, also the `<pb>`/`<lb>` structure                                                                                            |
| UDPipe 2 (stage 2, `api_2_udp.sh`)                                                                                                                                   | `UDP/<doc>.conllu` (+ the rows file kept beside it; `# chunk_start` marks UDPipe's ~900-word request chunks, which are not pages)        | `<s id text>` per sentence; `<tok>` per token with `lemma`, `upos`, `xpos`, `feats`, `head`, `deprel`, `ord`; `<dtok>` for the words of a multi-word token; `SpaceAfter=No` as no whitespace (and `join="right"`)                                                 |
| NameTag 3 (stage 3, `api_3_nt.sh`)                                                                                                                                   | IOB TSV per page, `NE/<doc>/<doc>-P.tsv`, merged by stage 4 into `UDP_NE/<doc>/<doc>.conllu`                                             | `<name id type sameAs>` around the entity's tokens, the raw label in `@onto`/`@cnec`/`@archaeo`                                                                                                                                                                   |
| Layout (stage 4, `api_4_stats.sh`): the ALTO file in `INPUT_ALTO_DIR`; else a flexiconv conversion in `TEITOK_FLEXICONV_DIR` (`FLEXICONV_ANNOTATE=true`); else none  | ALTO `Page`/`PrintSpace`/`TextBlock`/`TextLine`/`String` (+ `Illustration`, `GraphicalElement`); or TEITOK `pb`/`lb`/`tok@bbox`          | `<facsimile>` with one `<surface lrx lry>` and `<graphic url>` per page; `<pb n id facs corresp bbox>`; `<div type="TextBlock" bbox>`; `<lb bbox>`; `<figure type bbox>`; `@bbox` on every token aligned to an OCR string (tokens are matched to strings by text) |
| Page images (`INPUT_PAGES_DIR`), or `IMAGE_DPI`                                                                                                                      | PNG, JPEG or TIFF named `<doc_id>-<N>.<ext>`                                                                                             | the scale from layout units to image pixels, `<surface lrx lry>` and the `graphic@url`/`pb@facs` names                                                                                                                                                            |
| Provenance                                                                                                                                                           | ALTO `Description`, the models in `config_api.txt`, the run date                                                                         | `teiHeader`: `note[@n="orgfile"]`, `appInfo` (writer + format, UDPipe/NameTag models, OCR software), `revisionDesc/change` (`converted`, `tagged`/`parsed`, `ner`)                                                                                                |
| Document record (`--document-json`)                                                                                                                                  | `atrium_document` JSON                                                                                                                   | nothing by default: the record points into the file (`entities[].teitok_ref` = `n-N`, `pages[].teitok_surface` = `facs-P`); the opt-in projection adds page categories and keywords to the header |

The line table can come from any input alto-postprocess reads: ALTO, the other OCR formats (PAGE
XML, hOCR, ABBYY FineReader XML, DjVuXML, Tesseract TSV, OCR JSON), PDF, office and text files (its
[input formats reference](https://github.com/ufal/atrium-alto-postprocess/blob/master/docs/text_inputs.md#formats-and-their-standards)).
For every input but ALTO the table carries text and pages only. The boxes then come from a flexiconv
conversion of the same document (PAGE XML, hOCR), or there are none.

The writer is [api_util/teitok_alto.py](api_util/teitok_alto.py) 📎 (`write_teitok_merged`, called by
`summarize_nt_udp.py`); the gate is [api_util/validate_teitok_xml.py](api_util/validate_teitok_xml.py) 📎
(`contract` profile). Keywords, TEATER topics, page categories and line quality are not written
into TEITOK by default: they live in the document record (the three-format decision recorded on
ufal/atrium-project#24, 2026-08-01; the AMČR storage contract keeps page classification and line
quality "only in the record"). The opt-in projection below writes page categories, TEATER
categories and keywords, never line quality.

### Record projection onto TEITOK (opt-in)

For TEITOK users who want the record's page-level facts in the file itself (ufal/flexiconv#1:
keywords per page and the page category; the LINDAT dataset), atrium-project#70 item 2 adds a
projection, **off by default**. [api_util/teitok_project.py](api_util/teitok_project.py) 📎
writes, in the header and `pb/@ana` only:

| From                                                          | Into the TEITOK file                                                                                                                                                                                                                                            |
|---------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| page category (`pages[].category`, else `page_categories[P]`) | `<pb ana="#pcat-DRAW"/>`, and `encodingDesc/classDecl/taxonomy[@id="tax-page-category"]/category[@id="pcat-DRAW"]` with `@corresp` = the `atrium_vocab` concept URI and the definition in `catDesc`                                                             |
| TEATER/AMČR category (llm-enrich `enrichment.items[]`)        | `profileDesc/textClass/keywords[@scheme="#tax-amcr-teater"][@resp="#app-llm-enrich"]/term[@type="teater-category"]`: `@ref` = the concept URIs of `teater_category_ids`, `@cert` = the highest confidence, `@corresp` = its pages; the meta sentinel is skipped |
| controlled keywords, cs and en                                | `keywords[@resp="#app-llm-enrich"][@lang]/term[@type="extracted-keyword"][@corresp]`                                                                                                                                                                            |
| statistical keywords (this run's `keywords.py` method)        | `keywords[@resp="#app-kw"][@scheme="#kw-<method>"]/term[@type="statistical-keyword"][@n=rank][@score]` for the document, and one more such list per page with `@corresp="#pb-K"` (top N clamped to 5-20)                                                        |
| provenance                                                    | `appInfo/application` with `@id` `app-pc`, `app-llm-enrich` or `app-kw` after the writer's own, and `revisionDesc/change[@type="enriched"]`                                                                                                                     |

Pages resolve through `pages[].teitok_surface`, then `page_index`, then a numeric page key (`pb-K`),
then the `<pb n>` label; an llm-enrich item's page (its `## Page` label) through the record's page of
that label, then `<pb n>`, then as a number. A page that resolves to nothing is reported, not fatal.
Nothing inside `<s>`, `<tok>` or `<name>` changes, so `teitok_read`, `teitok_layout`, flexiconv's
reader and alto-postprocess read the same text, and the writer's own header lines stay as they were.
The projection is idempotent (it replaces its own earlier output), refuses another document's
TEITOK (the `<title>` must be the record's `doc_id` or the id of its `source.filename`), and
validates its output with the `contract` profile before writing. Per-page keywords are computed from
the TEITOK file's own tokens, page by page, with the same backend as the document's.

```bash
# in the pipeline: a `project` stage after `keywords` (reachable with --start-from project)
python3 run_pipeline.py --kw --kw-method yake --teitok-enrichment \
    --document-json CTX.document.json --document-json-out CTX.document.json
TEITOK_ENRICHMENT=true python3 run_pipeline.py --kw      # or switch it on in config_api.txt

# after llm-enrich, on a finished record
python3 -m api_util.teitok_project --teitok TEITOK/CTX.teitok.xml --record CTX.document.json --in-place
curl -F file=@TEITOK/CTX.teitok.xml -F document_json=@CTX.document.json http://localhost:8000/project_record
```

The service takes `teitok_enrichment=true` on `/enrich`, `/enrich_text` and `/jobs`. A TEITOK
regeneration (`REGENERATE_TEITOK`, a stage-4 re-run) writes the file anew without the projection;
project again after it. Line-level anchoring (a keyword on its `<lb>`) is not done: llm-enrich's
line numbers and the writer's `lb` numbering are not the same count.

### Importing into a TEITOK project

The files follow TEITOK's conventions, but a TEITOK project has a few of its own
(`xmlfiles/`, `Originals/`, `settings.xml`), and the upstream readers differ in what they keep:

- **File name.** A project keeps each document as `xmlfiles/<name>.xml`: copy
  `TEITOK/<doc_id>.teitok.xml` to `xmlfiles/<doc_id>.xml` (the `.teitok.xml` suffix is this
  pipeline's, not TEITOK's).
- **Original.** `note[@n="orgfile"]` names the file the document came from (the ALTO file, the
  input table, or the original a converted document was made from), without a directory. Put
  that file in the project's `Originals/`.
- **Attributes.** Tokens carry `lemma`, `upos`, `xpos`, `feats`, `head`, `deprel` and `ord`.
  teitok-tools' default tag attribute is `pos`: declare `xpos` (and the others you want to query
  or show) in the project's `settings.xml`.
- **Page images.** `pb@facs` and `<graphic url>` name the images (`<doc_id>-N.png` for ALTO
  documents, the converted file's own names otherwise); place them where the project's
  facsimile settings look.

What the TEITOK readers keep (flexiconv v0.3.10, the pinned version, is tested in
`tests/test_teitok_conformance.py` and `tests/test_teitok_pages.py`; flexipipe `73188c5` was
checked by hand on the committed samples, 2026-09-24):

| Reader                                  | Spacing (`SpaceAfter`) | Sentences, `<pb/>` inside `<s>` | Multi-word tokens (`<dtok>`)                | Dependency heads                                                           |
|-----------------------------------------|------------------------|---------------------------------|---------------------------------------------|----------------------------------------------------------------------------|
| flexiconv `load_teitok`                 | ✅                      | ✅                               | ❌ the surface token only, without its words | kept as the raw `@head` id                                                 |
| flexipipe `load_teitok` (Python loader) | ✅                      | ✅                               | ✅                                           | ❌ `head="w-N"` becomes token N of the *document*: wrong from sentence 2 on |
| this repo's `teitok_read.py`            | ✅                      | ✅ one row per page part         | ✅ surface form                              | not read                                                                   |

The two ❌ are upstream defects, recorded in issue #38 for an upstream report; `@ord` gives each word's
sentence-local number for a reader that needs it.

### Tools that generate or read TEITOK

| Tool                                                            | Role here                                                                                                                                    | Licence                         | Notes                                                                                                                                                                                  |
|-----------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| this pipeline's writer (`api_util/teitok_alto.py`)              | **generates** format 2 from the line table, CoNLL-U, NameTag output and a layout                                                             | MIT                             | the only writer in ATRIUM                                                                                                                                                              |
| [flexiconv](https://github.com/ufal/flexiconv) (pinned v0.3.10) | **converts** PDF, DOCX, ODT, RTF, HTML, Markdown, TXT, EPUB, PAGE XML, hOCR, ALTO, TEI … into TEITOK (`api_flexiconv.sh`); reads TEITOK back | GPL-3.0-or-later                | no `<s>`, no annotation; boxes only from layout formats (PAGE XML, hOCR, ALTO; PDF only in its `pdf=bbox` mode, which needs Poppler's `pdftotext`); CLI only, never in a service image |
| [flexipipe](https://github.com/ufal/flexipipe)                  | a UD pipeline (UDPipe and other backends) that can write and read TEITOK; not used                                                           | MIT                             | its Python TEITOK loader misreads `@head` (table above)                                                                                                                                |
| [xmltokenizer](https://github.com/ufal/xmltokenizer)            | tokenizes existing TEI/XML inline, keeping every element and character; not used (our text comes from OCR lines, not from existing markup)   | MIT                             | the library flexipipe uses for TEI input                                                                                                                                               |
| [teitok-tools](https://github.com/ufal/teitok-tools)            | the TEITOK platform's scripts, e.g. `udpipe2teitok.pl` (UDPipe-parsed TEITOK from raw text); used only to check conventions                  | none declared in the repository | what a project expects is under [Importing into a TEITOK project](#importing-into-a-teitok-project)                                                                                    |
| [flexicorp](https://github.com/ufal/flexicorp)                  | corpus query interface over TEITOK XML, CQP/CWB, Manatee, BlackLab …; a possible consumer                                                    | none declared in the repository | —                                                                                                                                                                                      |
| atrium-llm-enrich `api_util/teitok_read.py`, `xml_to_md.py`     | **reads** TEITOK as line-level input and renders it to annotated Markdown for the LLM                                                        | MIT                             | `teitok_read.py` and `flexiconv_convert.py` are vendored from here, pinned by hash                                                                                                     |
| atrium-alto-postprocess `text_formats.read_tei`                 | **reads** TEITOK (and TEI) as a text input: every `<pb/>` a page, `<lb/>` lines                                                              | MIT                             | —                                                                                                                                                                                      |
| atrium-project `tools/e2e/e2e_assert.py --teitok-dir`           | **checks** that a document record's references resolve to the right elements and pages                                                       | none declared in the repository | strict for `teitok-2` files                                                                                                                                                            |

### Pitfalls

Each of these leaves a *valid* file that is wrong for its purpose — showing the text over the
page image — so the gate cannot catch them:

1. **Page-image names.** Images must be `<doc_id>-<N>.<png|jpg|jpeg|tif|tiff>` in
   `INPUT_PAGES_DIR` (a converted file's own `pb@facs` names also work). A page without one keeps
   its boxes in layout units and gets a guessed `<doc_id>-<N>.png` name; the writer warns
   (`[Warn] <doc>: no page image …`).
2. **ALTO units.** ALTO in `mm10` or `inch1200` needs `INPUT_PAGES_DIR` or `IMAGE_DPI`; without
   either the boxes stay in ALTO units (warned: `MeasurementUnit is mm10 …`).
3. **Origin.** `BBOX_ORIGIN=printspace` is only for page images cropped to the print space.
4. **Rescaling.** `/rescale` and `fix_teitok_bboxes.py` must target the size of the image the
   viewer shows, page by page.
5. **Stale files.** A resumed run keeps existing `.teitok.xml` files (`REGENERATE_TEITOK=false`),
   and files written before `8003051` (the page fix, after v0.21.0) carry the same `teitok-2`
   stamp: tell them apart by `<change type="converted" when="…">`, or regenerate everything.
6. **Two readers.** When the text comes from one reader (e.g. alto-postprocess's table) and the
   layout from another (flexiconv), tokens get boxes only as far as the two agree; below 90 %
   aligned tokens the writer warns, and unaligned tokens have no box.
7. **flexiconv PDFs** carry no boxes and no page images by default, so no facsimile.
8. **Licence.** flexiconv is GPL-3.0-or-later: it runs as a CLI step; the REST service accepts its
   `.teitok.xml` output but never runs it.
9. **Vendored readers.** After changing `api_util/teitok_read.py` or `api_util/flexiconv_convert.py`,
   re-vendor them into atrium-llm-enrich (its `tests/test_vendored_teitok_parity.py` pins them).
10. **Upstream readers** lose `<dtok>` words (flexiconv) or misplace heads (flexipipe) — see the
    table in [Importing into a TEITOK project](#importing-into-a-teitok-project).
11. **No TEI namespace.** Tools that expect `http://www.tei-c.org/ns/1.0` need it added
    (`xmlnsoff` → `xmlns`) on their copy.
12. **New NE models.** Add their labels to `api_util/ner_types.py`; otherwise every entity is
    `type="MISC"`.
13. **Record references.** `lines[].teitok_ref` is not written yet; the hub E2E checks references
    strictly only for `teitok-2` files.
14. **Page numbers.** The writer numbers pages by their order in the ALTO file (`pb-1`, `facs-1`,
    the page-image name `<doc_id>-1`, the record's `pages[].teitok_surface`), and `pb@n` is the
    table's `page_label`, else that number. alto-postprocess's ALTO methods number pages by
    `PHYSICAL_IMG_NR`, and its `DOC_LINE_CATEG` table has no `page_label`. When an ALTO file's
    `PHYSICAL_IMG_NR`s are not 1, 2, 3 … in order, the TEITOK file shows the ordinals, and the
    record gets this repo's `pages[]` rows under the ordinals beside alto-postprocess's rows under
    the ALTO numbers. Name page images by the ordinal.

---

## ⚙️ Setup

Before you begin, set up your environment.

1. Create and activate a new virtual environment in the project directory 🖥.
2. Install the required Python packages:
```bash
pip install -r requirements.txt
```

For keyword extraction, install the backend(s) you intend to use:
```bash
# YAKE — unsupervised statistical extraction, CPU-only
pip install yake

# KeyBERT — embedding-based extraction, GPU-accelerated when available
pip install keybert sentence-transformers
pip install torch          # optional — enables CUDA GPU acceleration
```

The original **legacy KER** backend requires no additional packages.
For the LLM Semantic Enrichment pipeline, install the inference backend you intend to use:
```bash
# Transformers backend — single GPU, models ≤ 31 B (BnB 4-bit / AWQ / GGUF)
pip install -r requirements_llm.txt

# vLLM backend — multi-GPU, large models (≥ 70 B), Automatic Prefix Caching
# Replaces lmformatenforcer; uses xgrammar for native guided JSON decoding
pip install vllm
```

*(Optional) To run the REST API service, install additional requirements:*
```bash
pip install -r service/requirements.txt
```

3. Review and update the [config_api.txt](config_api.txt) 📎 file with your specific paths and API configurations.
You are now ready to start the workflow.

---

## Workflow Stages

The process is divided into sequential steps, each responsible for a specific part of the NLP enrichment pipeline.

### ▶ Step 1: Prepare CSVs with texts from Page-Specific ALTOs

> [!IMPORTANT]
> If you already have a directory of CSV (XLSX) tables with `text` column containing extracted text
> files from ALTO XMLs, you can skip Step 1 and proceed directly to Step 2.

The `../CSVS_with_TEXT/` directory mentioned later is the result of ALTO XML postprocessing pipeline described
in the separate repository [^2]. It contains document-specific CSV (XLSX) files with the `text` column containing
extracted textual content from the ALTO XML files. Each CSV (XLSX) file corresponds to a document and contains rows
for each page with a line number column for the proper ordering (`page_num` and `line_num`).

```
CSVS_with_TEXT/
├── document1.csv
├── document2.csv
└── ...
```
with the structure of each CSV (XLSX) file like:
```
file,page_num,line_num,text,split_ws,split_we,lang,lang_score,perplex,categ
CTX201504033,1,8,2012,,,N/A,0,0,Non-text
CTX201504033,2,2,1,,,N/A,0,0,Non-text
CTX201504033,3,2,2,,,N/A,0,0,Non-text
...
```
Where `split_ws` and `split_we` are the start and end character offsets of the words split in the original ALTO XML.
The `lang` and `lang_score` columns indicate the detected language and its confidence score,
while `perplex` and `categ` provide additional metadata about the text classification.

If the script detects an `.xlsx` file, it will iterate over all sheet names, verify if a `text` column exists
in each sheet, and extract the content safely for Excel tables with multiple sheets.

### ▶ Step 2: Extract NER and CONLL-U

This stage performs advanced NLP analysis using external APIs (Lindat/CLARIAH-CZ)
to generate Universal Dependencies (CoNLL-U) and Named Entity Recognition (NER) data.

Unlike previous steps, this process is split into modular shell scripts to handle large-scale
processing, text chunking, and API rate limiting.

#### Configuration ⚙️

Before running the pipeline, review the [api_config.txt](config_api.txt) 📎 file. This file controls
directory paths, API endpoints, and model selection.

```bash
# config_api.txt
OUTPUT_DIR="../../ARUB"                          # Destination for results
INPUT_TABLES_DIR="$OUTPUT_DIR/DOC_LINE_LR_CLS"  # Input tables from Step 1

WORK_DIR="./TEMP"                                # Working directory for intermediate files

LOG_FILE="$OUTPUT_DIR/processing.log"
CONLLU_INPUT_DIR="$OUTPUT_DIR/UDP"
TEMP_TXT_DIR="./TEMP/TXT_EXTRACT"
CHUNK_DIR="./TEMP/CHUNKS"

TSV_INPUT_DIR="$OUTPUT_DIR/NE"
SUMMARY_OUTPUT_DIR="$OUTPUT_DIR/UDP_NE"

TEITOK_OUTPUT_DIR="$OUTPUT_DIR/TEITOK"
INPUT_ALTO_DIR="$OUTPUT_DIR/ALTO"               # Source ALTO XML files - for TEITOK conversion

# Backing services. Environment wins, this file is the default — see
# "Backing Service Endpoints" below for why the ${VAR:-...} form is required.
UDPIPE_URL="${UDPIPE_URL:-https://lindat.mff.cuni.cz/services/udpipe/api/process}"
NAMETAG_URL="${NAMETAG_URL:-https://lindat.mff.cuni.cz/services/nametag/api/recognize}"

MODEL_UDPIPE="czech-pdt-ud-2.15-241121"
MODEL_NAMETAG="nametag3-multilingual-onto-260521"

TIMEOUT=60                     # API call timeout in seconds
MAX_RETRIES=5                  # Number of retries for failed API calls
BACKOFF_FACTOR=1.5
WORD_CHUNK_LIMIT=900           # Word limit per API call

SAVE_CSV=true                  # write token-level summary CSV
SAVE_CONLLU_NE=true            # keep merged CoNLL-U with NER in MISC
SAVE_TEITOK=true               # write TEITOK XML (see "TEITOK XML — Unified Output Format")

# ── Image Options (TEITOK coordinates) ──────────────────────────────────────────
INPUT_PAGES_DIR=""             # page images <doc_id>-<N>.<png|jpg|tif>: scale bboxes to them
IMAGE_DPI=""                   # no images: scale from the ALTO MeasurementUnit to this DPI
ALTO_DPI=""                    # ... and for pixel-unit ALTO, the DPI the ALTO was made at
BBOX_ORIGIN="page"             # page (TEITOK norm) | printspace (images cropped to PrintSpace)
REGENERATE_TEITOK="${REGENERATE_TEITOK:-false}"  # true: rewrite existing .teitok.xml instead of resuming
```

`TIMEOUT`, `MAX_RETRIES` and `WORD_CHUNK_LIMIT` are limits (atrium-project#53). For an API job
each is also an environment setting — `LINDAT_TIMEOUT_S`, `LINDAT_MAX_RETRIES` and
`WORD_CHUNK_LIMIT`, which win over this file — and `GET /info` reports the value in force; see
[service/README.md § Limits](service/README.md#limits) for every limit of the service.

#### Backing Service Endpoints

`UDPIPE_URL` and `NAMETAG_URL` are **attachable** (12-factor IV): point them at a
self-hosted UDPipe 2 / NameTag 3 instance, or a local stub, without touching code.
Precedence, highest first:

1. **`--url`** on `call_udpipe.py` / `call_nametag.py`.
2. **The environment** — `UDPIPE_URL` / `NAMETAG_URL` exported by docker compose
   `environment:`, a k8s `env:` block, or the shell.
3. **[config_api.txt](config_api.txt) 📎**, which supplies the LINDAT defaults.

Both keys must be written in the `${UDPIPE_URL:-https://…}` form shown above.
A bare assignment breaks the first two tiers: every stage script begins with
`source config_api.txt`, and bash preserves the export attribute of an
already-exported variable, so a plain `UDPIPE_URL="https://…"` silently
overwrites *and re-exports* the value the deployment set — the helper scripts
would then read this file's endpoint rather than the operator's.

The `/info` endpoint and the `?deep=true` health probe resolve the same
precedence, so they always report and probe the endpoint the pipeline uses.

#### Execution Pipeline

Run the following scripts in sequence. Each script sources [config_api.txt](config_api.txt) 📎
directly for configuration. Retry logic and per-attempt error handling are implemented inside
the Python helper scripts ([call_udpipe.py](api_util/call_udpipe.py),
[call_nametag.py](api_util/call_nametag.py)) using exponential back-off controlled by the
`MAX_RETRIES` and `BACKOFF_FACTOR` variables. [api_util/api_common.sh](api_util/api_common.sh) 📎
is a standalone utility module that exposes a `log()` helper and an `api_call_with_retry()`
shell function for any custom scripts that choose to source it; the four main pipeline scripts
(`api_1_manifest.sh` … `api_4_stats.sh`) do not source it. Additionally, [api_util/](api_util/) 📁
contains helper Python scripts for chunking and analysis

##### 1. Generate Manifest

Maps input text files to document IDs and page numbers to ensure correct processing order.

```bash
./api_1_manifest.sh
```

* **Input:** `../CSVS_with_TEXT/` (raw text files in subdirectories from Step 1).
* **Output:** `OUTPUT_DIR/manifest.tsv`, and per document `TEMP_TXT_DIR/<doc_id>.txt` (one line per
  table row) with `TEMP_TXT_DIR/<doc_id>.rows.tsv` next to it: the page, line, page label and text
  of every line. That file is where stages 3 and 4 take page numbers from
  ([api_util/page_rows.py](api_util/page_rows.py) 📎).

Example output file [manifest.tsv](data_samples/manifest.tsv) 📎 with **file**, **page**
number, and **path** columns. It lists all text files to be processed in the next steps.
Run the following command to see how many documents will be processed:

```bash
tail -n +2 OUTPUT_DIR/manifest.tsv | wc -l
```
which returns the total number of document rows in the manifest, excluding the header line.

##### 2. UDPipe Processing (Morphology & Syntax)

Sends text to the UDPipe API [^5]. Large documents are automatically split into chunks (default 900 words) using
[chunk.py](api_util/chunk.py) 📎 to respect API limits, then merged back into valid CoNLL-U files.

```bash
./api_2_udp.sh
```

* **Input 1:** `OUTPUT_DIR/manifest.tsv` (mapping of text files to document IDs and page numbers).
* **Input 2:** `../CSVS_with_TEXT/` (raw text files in subdirectories from Step 1).
* **Output:** `OUTPUT_DIR/UDP/*.conllu` (Intermediate per-document CoNLL-U files), each with its
  `<doc_id>.rows.tsv` copied next to it, so the text and its page provenance stay together on
  resumed runs and in containers (where `TEMP/` is not kept).

Run the following command to see how many documents have been processed into CoNLL-U files:

```bash
ls -l <OUTPUT_DIR>/UDP/ | wc -l
```
which returns the total number of CoNLL-U files created (each file corresponds to a document).

Example output directory [UDP](data_samples/UDP) 📁 contains per-document CoNLL-U files.

> [!NOTE]
> **Chunking and page boundaries.** [chunk.py](api_util/chunk.py)📎 splits text on OCR line boundaries (not raw whitespace),
> preserving the newline-separated structure of the source CSV so that UDPipe receives proper
> sentence-boundary hints between lines. A chunk is about `WORD_CHUNK_LIMIT` words, **not a page**:
> when a document spans several chunks, [call_udpipe.py](api_util/call_udpipe.py)📎 merges them into
> one CoNLL-U and marks where each chunk starts with `# chunk_start = K`. Pages come from the rows
> file instead: UDPipe marks every line end (`SpacesAfter=\n`), so counting those marks — checked
> against the text of every row — places each token on its line and page
> ([page_rows.py](api_util/page_rows.py)📎); if the check fails, the characters are aligned instead.
> Up to v0.21.0 the chunk marker was `# page_break = true` and every stage read it as a page;
> it is now read as a chunk start. Without a rows file, only an old per-page CoNLL-U (whose
> `# sent_id` restarts at 1) has more than one page.

> [!TIP]
> You can launch the next step when a portion of CoNLL-U files are ready,
> without waiting for the entire input collection to finish. You will have to relaunch
> the next step after all CoNLL-U files are ready to process the files created after the previous
> run began.

##### 3. NameTag Processing (NER tags)

Takes the valid CoNLL-U files and passes them through the NameTag API [^6] to annotate Named Entities
(NE) directly into the syntax trees.

```bash
./api_3_nt.sh
```

* **Input:** `OUTPUT_DIR/UDP/*.conllu` (Intermediate per-document CoNLL-U files) and their `.rows.tsv`.
* **Output:** `OUTPUT_DIR/NE/*/*.tsv` (NE annotated per-page files: `<doc_id>-P.tsv` holds the tokens
  of page P; a sentence that runs over a page is split between two files, in order)

Run the following command to see how many documents have been processed into TSV files:

```bash
ls -l OUTPUT_DIR/NE | wc -l
```
which returns the total number of directories created (each subfolder corresponds to a document).

Example output directory [NE](data_samples/NE) 📁 contains per-page TSV files with NE annotations. The tags are
the labels of the configured NameTag model (`MODEL_NAMETAG`): OntoNotes 5 for the default multilingual model,
CNEC 2.0 [^3] for the Czech one. The committed `NE/` files carry OntoNotes labels, while `UDP_NE/` and `TEITOK/`
were produced with the Czech CNEC model before the default changed; a sample refresh on LINDAT aligns them.


##### 4. Generate Statistics

This stage consolidates the linguistic data from UDPipe (CoNLL-U) and the NER data from
NameTag (TSV) into final per-document formats. It also generates a master summary of
entity counts across the entire collection and can optionally produce TEITOK-compatible
XML files that merge linguistic tokens with original ALTO layout coordinates.

The process utilizes [summarize_nt_udp.py](api_util/summarize_nt_udp.py) 📎 to merge these
layers, map complex CNEC 2.0 tags (e.g., `g`, `pf`, `if`) into human-readable categories
(e.g., "Geographical name", "First name", "Company/Firm"), and write all output formats.
Optionally, TEITOK-related functionality is implemented in
[teitok_alto.py](api_util/teitok_alto.py) 📎.

```bash
./api_4_stats.sh
```

#### Inputs and Outputs

* **Input 1:** `OUTPUT_DIR/UDP/*.conllu` — Per-document CoNLL-U files containing morphology and syntax.
* **Input 2:** `OUTPUT_DIR/NE/*/*.tsv` — Per-page TSV files containing Named Entity annotations.
* **Input 2b:** `OUTPUT_DIR/UDP/<doc_id>.rows.tsv` (else `TEMP_TXT_DIR/<doc_id>.rows.tsv`) — the page and line of
every token; `page_id` in the CSVs, the summary's pages, entity pages and the TEITOK pages come from it
(or from the layout, where there is one).
* **Input 3 (Optional):** `INPUT_ALTO_DIR/*.alto.xml` — Source ALTO XML files used during TEITOK conversion to provide spatial bounding box coordinates for each token.
* **Input 4 (Optional):** `INPUT_PAGES_DIR/<doc_id>-N.png` — Per-page facsimile images. When specified, the pipeline reads
their pixel size from the file headers to compute the scale factors (sx, sy). If omitted, the ALTO coordinates are written
as they are (scale 1.0), or scaled by `IMAGE_DPI`.
* **Output 1:** `OUTPUT_DIR/summary_ne_counts.csv` — Global table of aggregated Named Entity statistics across all documents.
* **Output 2:** `OUTPUT_DIR/UDP_NE/<doc_id>/<doc_id>.csv` — Per-document CSV tables with tokens, lemmas, and human-readable NE explanations.
* **Output 3 (Optional):** `OUTPUT_DIR/UDP_NE/<doc_id>/<doc_id>.conllu` — Final CoNLL-U files with NER tags enriched in the `MISC` column.
* **Output 4 (Optional):** `OUTPUT_DIR/TEITOK/<doc_id>.teitok.xml` — TEITOK XML files for a TEITOK project (`xmlfiles/<doc_id>.xml`), flexiconv/flexipipe and facsimile viewing (see [TEITOK XML — Unified Output Format](#teitok-xml--unified-output-format)).

The behavior of this step is controlled by boolean flags in your [config_api.txt](config_api.txt):

| Variable            | Description                                                                                                                                                                                                                                                                        | Default   |
|---------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-----------|
| `SAVE_CONLLU_NE`    | Keep the enriched CoNLL-U with NER in the `MISC` field.                                                                                                                                                                                                                            | `true`    |
| `SAVE_CSV`          | Write the token-level summary CSV per document.                                                                                                                                                                                                                                    | `true`    |
| `SAVE_TEITOK`       | Write TEITOK-style TEI XML with bounding boxes and NER spans. Without a layout (`INPUT_ALTO_DIR` unset, no converted file) TEITOK XML is still produced, with the table's pages and lines but no bboxes. A file that fails the output contract stops the stage with exit code `5`. | `true`    |
| `INPUT_PAGES_DIR`   | Directory of per-page images (`<doc_id>-N.png`). When set, bbox coordinates are scaled to match the actual PNG resolution. Leave empty to write raw ALTO pixel values.                                                                                                             | *(empty)* |
| `BBOX_ORIGIN`       | `page`: bboxes measured from the page's top-left corner (the TEITOK norm). `printspace`: measured from the ALTO PrintSpace, with a PrintSpace-sized `<surface>` — only for page images cropped to the print area.                                                                  | `page`    |
| `REGENERATE_TEITOK` | `true`: delete and rewrite each document's `.teitok.xml` instead of skipping it on a resumed run (use once after a TEITOK format change).                                                                                                                                          | `false`   |

#### ALTO-to-TEITOK XML Generation and Coordinate Alignment

When `SAVE_TEITOK=true`, [teitok_alto.py](api_util/teitok_alto.py) 📎 reads and processes the internal spatial
hierarchy of your ALTO source specifications.

**Coordinate origin (`BBOX_ORIGIN`):**

ALTO positions (`HPOS`, `VPOS`) are measured from the top-left corner of the page image, and
TEITOK expects exactly that: every TEITOK tool (flexiconv's ALTO/PAGE/hOCR import,
teitok-tools) writes `bbox` as page-image pixels. So by default (`BBOX_ORIGIN=page`) the
coordinates are taken as they are, and `<surface lrx lry>` declares the page size.

Some collections publish page images **cropped to the print area**. For those, set
`BBOX_ORIGIN=printspace`: the page's `<PrintSpace>` origin is subtracted from every
coordinate, and `<surface>` declares the PrintSpace size instead.

```xml
<PrintSpace HEIGHT="2039" WIDTH="1254" VPOS="150" HPOS="200">
```

With the sample above, a block at `HPOS=220 VPOS=160` becomes `bbox="20 10 …"` on a
`1254 × 2039` surface instead of `bbox="220 160 …"` on a `1654 × 2339` one. Content outside
the PrintSpace (e.g. marginalia) would get negative coordinates; they are clamped to 0 and
counted in a warning.

$$\text{Written Coordinate} = \text{round}((\text{ALTO Coordinate} - \text{Origin}) \times \text{Scale Factor})$$

where Origin is 0 for `page`, and the PrintSpace `HPOS`/`VPOS` for `printspace`.

**Scale factor (three tiers, per page):**

* **Companion Image Present (Tier 1):** If `INPUT_PAGES_DIR` is set and matching images exist, the tool
reads their pixel size from the file headers (PNG, JPEG, TIFF; no imaging library needed) and scales
the reference extent to it: `sx = image width / Page WIDTH` (`/ PrintSpace WIDTH` under `printspace`).
  Worked example: [`data_samples/pages/CTX000000001-1.png`](data_samples/pages/CTX000000001-1.png) is
  827×1170, half the ALTO page. With `INPUT_PAGES_DIR=data_samples/pages`, page 1 of `CTX000000001` gets
  `<surface lrx="827" lry="1170">` and the first block moves from `220 160 1420 280` to `110 80 710 140`.
  Page 2 has no image and stays at tier 3. The image draws the ALTO boxes at half scale, so the overlay
  can be checked by eye (`tests/test_teitok_integration.py`).
* **User-set DPI (Tier 2):** If no image is available, scale is derived directly from the ALTO `<MeasurementUnit>`
(`inch1200`, `mm10`, or `pixel`) mapped against the `IMAGE_DPI` and `ALTO_DPI` settings.
* **Native Processing (Fallback):** If no image and no DPI is provided, the scale factor is `1.0`.

Two of these fall-backs are easy to miss, because the file stays valid, so the writer reports them
on stderr (once per document): a page with no `<doc_id>-<N>.<png|jpg|jpeg|tif|tiff>` in the image
folder (`[Warn] <doc>: no page image in … for page(s) …` — its boxes stay unscaled and its `facs`
name is a guess, e.g. page 2 in the worked example above), and tier 3 with an ALTO
`MeasurementUnit` other than `pixel` (`MeasurementUnit is mm10 …` — the boxes are then in
1/10 mm, not pixels; set `INPUT_PAGES_DIR` or `IMAGE_DPI`).

In every tier `<surface lrx lry>` is the extent of the space the bboxes are measured in, so
`POST /rescale` and `fix_teitok_bboxes.py` can rescale a document to any other image size.

> **Relative coordinates:** resolution-independent (0–1) bounding boxes were proposed upstream
> ([ufal/flexiconv#1](https://github.com/ufal/flexiconv/issues/1)) and have not been answered or
> adopted; TEITOK uses absolute pixels.

#### Fixing Bounding Box Alignments (Practical Guide)

If you are a new user approaching this pipeline—perhaps a researcher who just digitized a batch of archival documents—your 
primary goal might be making sure the semantic annotations actually line up with your page images in a web viewer.

Let's say your original document was processed at a massive archival resolution, but the image you are serving to your 
web frontend is exactly 1200 pixels wide and 1800 pixels high. Currently, your TEITOK XML bounding boxes are completely 
misaligned.

Here is exactly how you would use the integrated tools to solve this problem.

**Method 1: The Quick API Fix (Best for single files or web integrations)**

Since the pipeline now includes a dedicated FastAPI service, you don't even need to write a script. You can just send 
your misaligned XML to the `/rescale` endpoint.

Open your terminal and run a simple curl command, explicitly telling the API the exact dimensions of your target image 
and requesting the output as an XML file instead of the default JSON metadata:

```bash
curl -X POST "http://localhost:8000/rescale" \
     -F "file=@CTX000000001.teitok.xml" \
     -F "width=1200" \
     -F "height=1800" \
     -F "format=xml" \
     -o CTX000000001.rescaled.teitok.xml
```

*What happens behind the scenes:* The API detects each page's coordinate space from its `<surface>` tag 
in your XML and scales the bounding boxes (`bbox`) of that page to the new 1200x1800 dimensions; boxes are clamped
to the page, and a `<change type="rescaled">` in the header records what was done. As a bonus, it also repairs any
malformed named-entity tags (like `<name>...</n>`) in the document. When the pages of a document differ in size,
width and height would distort some of them: give a factor instead (`-F "scale=0.5"`), and every page image is
taken to be that fraction of its surface.

**Method 2: The Command-Line Batch Process (Best for whole directories)**

If you have hundreds of XML files in a folder and you know exactly what scale ratio or DPI conversion you need, using 
the REST API file-by-file would be tedious. Instead, use the dedicated CLI tool, `fix_teitok_bboxes.py`.

If you know your web images are exactly 50% the size of your original scans (a scale factor of 0.5), you can process 
the entire directory at once:

```bash
python3 fix_teitok_bboxes.py -i /path/to/my/teitok_folder/ --sx 0.5 --sy 0.5
```

Alternatively, if your original ALTO OCR data was in millimeters (`mm10`) and you need to target a standard 72 DPI 
screen resolution, the script can handle that math directly:

```bash
python3 fix_teitok_bboxes.py -i my_document.teitok.xml --unit mm10 --dpi 72
```

If the original scans included a scanner bed margin (e.g., 50 pixels on the left and 20 on the top) that was cropped 
out of the final web image, you can strip that out by shifting everything left and up:

```bash
python3 fix_teitok_bboxes.py -i my_document.teitok.xml --dx -50 --dy -20
```

A shift moves content near the edge off the page; such coordinates are clamped to the page (0, or the
page's width/height) and counted, rather than written as negative values the stage-4 gate would reject.
Each `<surface>` is rescaled by its own size, and a `<change type="rescaled">` records the rewrite.

Both methods directly address the historical pain point of facsimile alignment, allowing you to flawlessly overlay
the NLP enrichments onto the visual documents without needing to re-run the entire pipeline.

> [!NOTE]
> When a token's matched ALTO strings span more than one page (a rare OCR edge case near page
> boundaries), a warning is printed to stderr identifying the token and the conflicting page
> indices. The first matched page is used for the bbox assignment in that case.

The structural and spatial hierarchy from the ALTO file is strictly preserved in the generated TEITOK XML:

* **Tokens:** Matched coordinates are written to each `<tok>` element as `@bbox="x1 y1 x2 y2"` (page-image
pixels, the TEITOK convention). Each token also carries `@type="w"` (word) or `@type="pc"` (punctuation
character) derived from UDPipe's UPOS tag. Alignment runs on the *surface* tokens — for a multi-word token such
as "abych" the one `<tok>` gets the bbox, its `<dtok>` words none.
* **Lines:** ALTO `<TextLine>` elements are preserved via `<lb id="lb-P.L">` (line break) tags, which also include
their own `@bbox` spatial coordinates.
* **Blocks:** ALTO `<TextBlock>` elements become `<div type="TextBlock" id="b-P.K">` containers. Their
`@subtype` is the label of the block's ALTO `TAGREFS` tag (e.g. `paragraph`, `heading`), and their
`@lang` is set where the block's ALTO `LANG` differs from the document's. Without ALTO there is one
`<div type="text">` per page.
* **Graphics:** Non-text elements like `Illustration` and `GraphicalElement` blocks are parsed and
appended to their respective pages as `<figure id="fig-P.K">` tags with strict bounding boxes.
* **Pages:** Page boundaries are marked with `<pb n="N" id="pb-N" facs="..." corresp="#facs-N" bbox="0 0 W H"/>`
elements pointing to the page's `<surface>`. A token is on the page of the ALTO string it aligned to; a token
that did not align is on the page of its line in the rows file. Pages only move forward, so every page gets
exactly one `<pb>` (pages without text included). A sentence running over a page break gets its `<pb/>`
inside the `<s>` — inside the `<name>`, too, when an entity runs over — and the figures of that page follow
the sentence. A `<div>` stays on the page it opens on.

Named entity spans are wrapped in `<name>` elements grouping their constituent `<tok>` nodes, with
`@sameAs` listing the token ids. `@type` holds the coarse category (`PER`, `ORG`, `LOC`, or `MISC`,
from [api_util/ner_types.py](api_util/ner_types.py) 📎 — the same map the document record's
`entities[].type_teitok` uses), and the raw NameTag label goes in the attribute of its tagset:
`@onto` for the default multilingual OntoNotes model (`<name type="PER" onto="PERSON">`), `@cnec` for
the Czech CNEC 2.0 model (`<name type="PER" cnec="pf">`), `@archaeo` for the archaeological model.

> [!NOTE]
> Thanks to the sequence matching approach, the script achieves near-perfect spatial alignment between
> NLP tokens and OCR coordinates, drastically improving upon older greedy matching methods that would
> break on minor character variations. Alignment statistics (matched vs. total tokens) are printed to
> the console per document; below 90 % a warning asks whether text and layout come from the same
> document version (e.g. alto-postprocess's text with a flexiconv layout). A token that aligned to
> a page out of reading order loses its box (the longest in-order run of aligned pages is kept).

```bash
ls OUTPUT_DIR/UDP_NE | wc -l
```

which returns the total number of created files, both `.csv` and `.conllu` corresponding
to specific documents.

```bash
ls OUTPUT_DIR/UDP_NE/*/*.csv | wc -l
```

returns number of documents processed into tables

```bash
ls OUTPUT_DIR/TEITOK/*.xml | wc -l
```

returns number of recorded `.teitok.xml` documents.

Example summary table: `summary_ne_counts.csv` (produced by a real run; not committed —
see the note below).

Example output directory [UDP_NE](data_samples/UDP_NE) 📁 contains per-document CSV
tables with NE tags and UDPipe feature columns, plus CoNLL-U files with NE annotations in
per-document manner.

Example output directory [TEITOK](data_samples/TEITOK) 📁 contains per-document TEITOK
XML files combining UD linguistic annotations and NER spans with bounding boxes aligned
from the source ALTO XML.

#### Output Structure

After completing the pipeline, your working and output directories will be organized as follows:

```
TEMP/
├── CHUNKS/
│   └── ...
├── nametag_response_docname1.conllu.json
└── ...
```

AND

```
<OUTPUT_DIR>
├── UDP_NE/
│   ├── <doc_id>
│   │   ├── <doc_id>.csv
│   │   └── <doc_id>.conllu
│   ├── <doc_id>
│   │   ├── <doc_id>.csv
│   │   └── <doc_id>.conllu
│   └── ...
├── UDP/
│   ├── <doc_id>.conllu
│   ├── <doc_id>.rows.tsv
│   └── ...
├── TEITOK/
│   ├── <doc_id>.teitok.xml
│   ├── <doc_id>.teitok.xml
│   └── ...
├── NE/
│   ├── <doc_id>
│   │   ├── <doc_id>-<page_num>.tsv
│   │   └── ...
│   ├── <doc_id>
│   │   ├── <doc_id>-<page_num>.tsv
│   │   └── ...
│   └── ...
├── altos/
│   ├── <doc_id>.alto.xml
│   └── ...
├── pages/
│   ├── <doc_id>-1.png
│   ├── <doc_id>-2.png
│   └── ...
├── processing.log
├── summary_ne_counts.csv
└── manifest.tsv
```

The combined output `summary_ne_counts.csv` contains aggregated Named Entity
statistics across all processed pages. This repository's `data_samples/` only ships the three
synthetic demo documents (`CTX00000000{1,2,3}`), so no `summary_ne_counts.csv` is
committed — the file is real output of a real `api_5_summary_ne.sh` run, not a sample bundled here.

> [!NOTE]
> Now you can delete `UDP/` from `<OUTPUT_DIR>/` if you no longer need the raw CoNLL-U files.
> The final CoNLL-U files with NER features are in `<OUTPUT_DIR>/UDP_NE/`.

If you do not plan to rerun any part of the pipeline, you can also delete
the entire `TEMP/` directory including [manifest.tsv](data_samples/manifest.tsv) 📎.

---

## EXTRA: Extract Keywords (KER / YAKE / KeyBERT)

> [!NOTE]
> This is an optional step in NLP enrichment of your data. It can give a fast
> thematic overview of the whole collection and works best when UDPipe lemmas
> (output of Step 2) are available. Three extraction backends are provided;
> choose the one that best fits your environment and quality requirements.

Extract keywords 🔎 from your documents by running `keywords.py` on a directory of CoNLL-U files produced by Step 2.

### Configuration Priority

The keyword extraction script uses a three-tier configuration hierarchy (from highest to lowest priority):

1. **Command-line flags** (e.g., `-m yake`, `-w 3`) always override everything else.
2. **`kw_config.txt`** (the `[DEFAULTS]` section) is read automatically if placed next to the script.
3. **Hardcoded fallbacks** are used if no config file or flags are provided.

This means if you configure your settings in `kw_config.txt`, you can simply run:

```bash
python3 keywords.py
```

### Backends

| Flag value         | Method                                        | Dependencies                                | Score semantics                       | Best for                                  |
|--------------------|-----------------------------------------------|---------------------------------------------|---------------------------------------|-------------------------------------------|
| `legacy`           | Original KER — NOUN/PROPN/ADJ lemma frequency | none (stdlib only)                          | raw occurrence count                  | reproducing original ATRIUM results       |
| `yake` *(default)* | YAKE — unsupervised statistical, CPU-only     | `pip install yake`                          | normalised inverse YAKE score, [0, 1] | fast CPU runs, no model download          |
| `keybert`          | KeyBERT — embedding-based, GPU-accelerated    | `pip install keybert sentence-transformers` | cosine similarity, [0, 1]             | highest semantic quality, GPU recommended |

You can override any `kw_config.txt` setting via the command line:

```bash
python3 keywords.py -i <input_dir> -m <method> -l <lang> -w <integer> \
                    -n <integer> -d <output_dir> -o <output_file>.csv
```

All available flags:

| Flag | Long form           | Default in `kw_config.txt`              | Description                                                                               |
|------|---------------------|-----------------------------------------|-------------------------------------------------------------------------------------------|
| `-i` | `--input_dir`       | `data_samples/UDP`                      | CoNLL-U directory to process                                                              |
| `-m` | `--method`          | `yake`                                  | Backend: `legacy`, `yake`, or `keybert`                                                   |
| `-l` | `--lang`            | `cs`                                    | Language code for YAKE stopwords (`cs`, `en`, `de`, …). Ignored by `legacy` and `keybert` |
| `-w` | `--max_words`       | `3`                                     | Maximum words per keyword phrase (n-gram upper bound)                                     |
| `-n` | `--num_keywords`    | `20`                                    | Number of keywords to extract per document                                                |
| `-d` | `--per_doc_out_dir` | `data_samples/KW_PER_DOC`               | Output directory for per-document CSV files                                               |
| `-o` | `--output_file`     | `keywords_summary.csv`                  | Master keywords CSV                                                                       |
|      | `--keybert-model`   | `paraphrase-multilingual-MiniLM-L12-v2` | Sentence-Transformer model name (KeyBERT only)                                            |
|      | `--no-mmr`          | *(False)*                               | Disable Maximal Marginal Relevance diversification (KeyBERT only)                         |
|      | `--diversity`       | `0.5`                                   | MMR diversity parameter, 0 = max relevance → 1 = max diversity (KeyBERT only)             |
|      | `--workers`         | `0` *(Auto / CPU count)*                | Parallel worker processes. Auto-forced to 1 for KeyBERT + GPU                             |

Examples:

**YAKE** — Czech, up to 3-word phrases, 20 keywords per document (default)

```bash
python3 keywords.py -i OUTPUT_DIR/UDP -m yake -l cs -w 3 -n 20 \
        -o keywords_summary.csv -d KW_PER_DOC
```

**KeyBERT** — multilingual model, GPU-accelerated

```bash
python3 keywords.py -i OUTPUT_DIR/UDP -m keybert -w 3 -n 20 \
        --keybert-model paraphrase-multilingual-MiniLM-L12-v2 \
        -o keywords_summary.csv -d KW_PER_DOC
```

**Legacy KER** — (English/Czech) original ATRIUM lemma-frequency approach, no extra dependencies

```bash
python3 keywords.py -i OUTPUT_DIR/UDP -m legacy -n 20 \
        -o keywords_summary.csv -d KW_PER_DOC
```

> [!WARNING]
> For **KeyBERT with a GPU**, the script automatically forces `--workers 1` to
> prevent competing CUDA context initialisation across subprocesses.  On CPU,
> any worker count is safe.

### Inputs and outputs

* **Input:** Directory of per-document CoNLL-U files from Step 2.
* **Output 1:** Master table with keywords per document (e.g., `keywords_summary.csv`).
* **Output 2:** Per-document CSV files (e.g., `KW_PER_DOC/`).

```
KW_PER_DOC/
├── <docname1>_keywords.csv
├── <docname2>_keywords.csv
└── ...
```

Each per-document file contains two columns — **keyword** and **score** — sorted
by score in descending order.  The master summary uses the same column structure
as the original pipeline (`document_id`, `kw-1`, `score-1`, `kw-2`, `score-2`, …).

### Score interpretation by backend

**`legacy`** — raw lemma count; higher = more frequent in the document. Examples in directory: [KW_PER_DOC_L](data_samples/KW_PER_DOC_L) 📂 and summary file
[kw_summary_l.csv](data_samples/keywords_summary_l.csv) 📎.

| Score range | Interpretation                                           |
|-------------|----------------------------------------------------------|
| 1–5         | Common functional nouns, low informativeness             |
| 5–20        | Topic-representative vocabulary                          |
| > 20        | Dominant terms, likely named entities or domain headings |

**`yake`** — normalised inverse YAKE score, [0, 1] per document. Examples in directory: [KW_PER_DOC_Y](data_samples/KW_PER_DOC_Y) 📂 and summary file
[kw_summary_y.csv](data_samples/keywords_summary_y.csv) 📎.

| Score range | Semantic category | Interpretation                               |
|-------------|-------------------|----------------------------------------------|
| 0.0–0.2     | Noise floor       | Common words, low local relevance            |
| 0.2–0.6     | Context layer     | General vocabulary defining the broad topic  |
| 0.6–0.9     | Topic layer       | Specific nouns and verbs central to the text |
| 0.9–1.0     | Entity layer      | Rare terms, neologisms, named entities       |

**`keybert`** — cosine similarity to document centroid, [0, 1]. Examples in directory: [KW_PER_DOC_KB](data_samples/KW_PER_DOC_KB) 📂 and summary file
[kw_summary_kb.csv](data_samples/keywords_summary_kb.csv) 📎.

| Score range | Interpretation                   |
|-------------|----------------------------------|
| < 0.3       | Weakly related phrases           |
| 0.3–0.6     | Contextually relevant terms      |
| > 0.6       | Highly representative keyphrases |

---

## EXTRA: Converting Other Input Formats with flexiconv

> [!NOTE]
> This section is relevant when your documents originate from an OCR or digitisation
> pipeline that does **not** produce ALTO XML — for example, PAGE XML, hOCR, plain-text
> exports, or office/PDF documents. If you already have ALTO XML, the pipeline generates
> TEITOK XML natively via `api_4_stats.sh` (see above).

### What is flexiconv?

**[flexiconv](https://github.com/ufal/flexiconv)** [^9](https://github.com/ufal/flexiconv) is a format converter
by the TEITOK author (UFAL) that translates OCR, layout and document formats into **TEITOK
XML**. Its output can be loaded straight into a TEITOK project, and the readers of this repository
(`keywords.py`, `llm_run.py`, via [api_util/teitok_read.py](api_util/teitok_read.py) 📎) accept it.

```
  Your input format           flexiconv                     Output
  ─────────────────    ──────────────────────   ──────────────────────────────────
  PAGE XML, hOCR    ─┐                           .teitok.xml   ──► TEITOK platform
  txt, md, html     ─┤──► api_flexiconv.sh ────► (flexiconv    ──► keywords.py, llm_run.py
  docx, odt, pdf    ─┘                            profile)
```

By default flexiconv output is used as it is, without UDPipe/NameTag
([annotating it](#annotating-converted-documents-flexiconv_annotate) is opt-in). It contains
what the source contains:

| Source                                 | flexiconv TEITOK                                                        | Rows seen by `teitok_read.py`      |
|----------------------------------------|-------------------------------------------------------------------------|------------------------------------|
| PAGE XML, hOCR, ALTO (`.xml`, `.hocr`) | `<tok bbox>` words with `<lb/>` lines and `<facsimile>` zones, no `<s>` | one row per line                   |
| txt, md, html, docx, odt, rtf, pdf, …  | `<p>`, `<head>`, `<item>` text, not tokenized                           | one row per paragraph/heading/item |

So YAKE/KeyBERT keywords and LLM enrichment work on it. The lemma-based `legacy` keyword method
does not, because there are no lemmas.

A PDF converted with flexiconv's default reader (`pdf=smart`) has no word boxes and no page
images, so its TEITOK has no facsimile; flexiconv's `pdf=bbox` reader gives word boxes but needs
Poppler's `pdftotext` (not wired into `api_flexiconv.sh`). See also [Pitfalls](#pitfalls) 6–8.

### How to use it in this pipeline

1. **Install** the pinned flexiconv and the format extras:

```bash
pip install -r requirements_flexiconv.txt   # flexiconv v0.3.10 + docx/odt/md/pdf/rtf extras
```

flexiconv is **GPL-3.0-or-later** (declared in its `pyproject.toml`) and optional: nothing else
in the pipeline imports it, and `para_config.txt` records it as a conditional component.

2. **Put the documents** in `INPUT_DOCS_DIR` (`config_api.txt`, default `$OUTPUT_DIR/DOCS`) and
list the extensions to convert in `FLEXICONV_FORMATS`. `xml` covers PAGE XML, ALTO and TEI:
flexiconv detects them by content.

3. **Convert:**

```bash
bash api_flexiconv.sh
```

Each file becomes `<stem>.teitok.xml` in `TEITOK_FLEXICONV_DIR` (default
`$TEITOK_OUTPUT_DIR/flexiconv`). Two inputs with the same stem (`report.txt` + `report.md`)
become `report.txt.teitok.xml` and `report.md.teitok.xml`. Existing outputs are kept unless
`FLEXICONV_FORCE=true`. The script calls flexiconv's library (`flexiconv.api.run_convert`), or
its CLI (`flexiconv --no-auto-install -t teitok IN OUT`) when only the CLI is installed. Either
way it never `pip install`s missing extras in the middle of a run. Without flexiconv installed the
stage stops at once with exit code `3`. A file that cannot be converted is reported in one line and
recorded, with its reason, in the paradata record of the run (`--program nlp-enrich`, component
`flexiconv`); the stage converts the rest and then exits `1`, so `run_pipeline.py --with-flexiconv`
does not end green with documents missing. The output directory is then checked with
`validate_teitok_xml.py --profile core`, the rules any TEITOK document must meet. This is not
this writer's XSD, and `api_4_stats.sh` leaves this directory out of its own gate.

4. **Use the result** with the readers: `python3 keywords.py -i "$TEITOK_FLEXICONV_DIR" -m yake`,
or `INPUT_DIR` in `llm_config.txt`.

For a single file, the CLI directly: `flexiconv -t teitok input.page.xml output.teitok.xml`
(`flexiconv --list-formats` lists every input format).

`run_pipeline.py --with-flexiconv` runs `api_flexiconv.sh` as its first stage (next section;
`--start-from` any later stage skips it). The REST service never runs flexiconv (no GPL code in its
image), but it takes flexiconv's output: upload a converted `.teitok.xml` to `/enrich` and it is
annotated with its own layout ([service/README.md](service/README.md) 📎).

### Annotating converted documents (`FLEXICONV_ANNOTATE`)

With `FLEXICONV_ANNOTATE=true` a converted document goes through the same linguistic stages as
a table input. It comes out as TEITOK format 2 in `TEITOK_OUTPUT_DIR`: sentences, lemmas,
UPOS/XPOS/features, dependencies, `<name>` entities and TEITOK ids. It also keeps what flexiconv
found on the page:

```bash
python3 run_pipeline.py --with-flexiconv   # api_flexiconv.sh, then manifest → udp → nt → stats
```

`--with-flexiconv` sets the flag for every stage. To run stage by stage, set
`FLEXICONV_ANNOTATE=true` in `config_api.txt` and run `bash api_flexiconv.sh` before
`api_1_manifest.sh`.

| Stage               | With `FLEXICONV_ANNOTATE=true`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
|---------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 1 manifest          | Each `TEITOK_FLEXICONV_DIR/*.teitok.xml` is a document (`report.txt.teitok.xml` is `report.txt`) whose text is the rows `teitok_read` gives (one per line or text block). When a table document uses the file as its layout (next row), the table wins and the converted file is recorded as skipped.                                                                                                                                                                                                                                                                                                                                                                                           |
| 2–3 UDPipe, NameTag | Unchanged.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| 4 stats             | A document without `<doc_id>.alto.xml` takes its layout from its converted file ([api_util/teitok_layout.py](api_util/teitok_layout.py) 📎): pages from `<pb>` (label `pb@n`, image and size from `pb@facs`/`@bbox` or its `<surface>`), lines from `<lb bbox>`, word boxes from `<tok bbox>`, and text blocks with their element name as `subtype` (`head`, `p`, `item`, …). The file is `<doc_id>.teitok.xml`, else the one file whose `canonical_doc_id` is the document's (`report.v2.teitok.xml` serves `report`); two such files (`report.txt` and `report.md` conversions) are ambiguous: none is used, and a warning says so ([api_util/doc_identity.py](api_util/doc_identity.py) 📎). |
| document record     | `entities[].bbox`, `teitok_ref` and `pages[].teitok_surface` are filled as for ALTO documents.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |

| Source                             | Layout in the annotated TEITOK                                                                                                                                                                                                                                               |
|------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| PAGE XML, hOCR, ALTO via flexiconv | Word and line boxes, and the page image under its own name (`<graphic url>` = flexiconv's `facs`). The page size is set when the source has one (hOCR). Punctuation that flexiconv did not box gets no bbox. flexiconv's `<zone>`s are not carried over (format 2 has none). |
| txt, md, docx, odt, pdf, …         | `<div type="TextBlock" subtype="p\|head\|item\|…">`, with no boxes and no facsimile.                                                                                                                                                                                         |

Inline markup such as `<hi>` is flattened into its block. The converted file stays in
`TEITOK_FLEXICONV_DIR` as the record of the conversion.

The header names flexiconv (`<change who="flexiconv" type="converted">`) and the original
document (`orgfile`). A page image the converted file names is looked up in `INPUT_PAGES_DIR` under
that name. When found, it sets the surface size; the coordinates are already image pixels and stay
as they are.

**In the full ATRIUM pipeline**, alto-postprocess's `--method text-lines` reads the same documents
into `DOC_LINE_CATEG/`, which is this repo's `INPUT_TABLES_DIR`. With `INPUT_DOCS_DIR` pointing at
the originals, the text comes from alto-postprocess, where its lines are categorised, and the
layout comes from flexiconv.

### Checking a real collection (issue #10)

Before relying on the flexiconv path for a collection, convert a few real documents of each kind
and record what comes out. `api_util/flexiconv_report.py` prints the table for you, one row per
converted file, and lists the inputs that produced nothing:

```bash
bash api_flexiconv.sh
python3 api_util/flexiconv_report.py "$TEITOK_FLEXICONV_DIR" --inputs "$INPUT_DOCS_DIR"
# | Input | Pages | Rows | Tokens | Elements with bbox | `--profile core` |
# | hocr `page_1.hocr` | 1 | 3 | 13 | 15 | ✅ |   ... exit 0 only if every file passes
```

Fill in the notes by looking at a few of the files:

| Input (format, source)      | Converted? | `--profile core` | Rows | Notes (layout kept? text order? encoding?) |
|-----------------------------|------------|------------------|------|--------------------------------------------|
| PAGE XML (e.g. Transkribus) |            |                  |      |                                            |
| hOCR (e.g. Tesseract)       |            |                  |      |                                            |
| docx / odt                  |            |                  |      |                                            |
| pdf (text layer)            |            |                  |      |                                            |
| txt / md                    |            |                  |      |                                            |

Reference run (2026-09-23, flexiconv v0.3.10, its own `examples/` plus one Czech txt), all through
`api_flexiconv.sh`, all passing `--profile core`, nothing installed during the run:

| Input                                 | Rows | Tokens | Elements with bbox | `keywords.py -m yake` |
|---------------------------------------|------|--------|--------------------|-----------------------|
| PAGE XML `aletheiaexamplepage.xml`    | 96   | 532    | 593                | ✅                     |
| hOCR `output_page_1.hocr`             | 3    | 13     | 15                 | ✅                     |
| ALTO `sample.alto.xml`                | 1    | 5      | 8                  | ✅                     |
| docx `16453-1.docx`                   | 21   | 464    | —                  | ✅                     |
| md `FORMATS_AND_MAPPINGS.md`          | 209  | 1463   | —                  | ✅                     |
| txt (2 paragraphs)                    | 2    | 10     | —                  | ✅                     |

Before the 2026-09 reader fallback every one of them read as **0 rows**. Keywords of English documents
need `keywords.py -l en` (the default language is Czech). For docx/odt/epub, flexiconv also writes the
embedded images into a `<output>_files/` directory next to the TEITOK file.

Real flexiconv v0.3.10 output for txt, md, PAGE XML, hOCR and ALTO is committed in
[tests/fixtures/teitok/flexiconv/](tests/fixtures/teitok/flexiconv) 📁 and covered by the tests.

> [!TIP]
> Upstream, flexiconv can also annotate its own output (`flexiconv --flexipipe`, or
> xmltokenizer); this pipeline uses its own UDPipe/NameTag stages instead
> ([above](#annotating-converted-documents-flexiconv_annotate)), so that every document gets
> the same writer and ids. If your format is not supported by flexiconv, please open an issue
> on the [flexiconv GitHub repository](https://github.com/ufal/flexiconv).

---

## EXTRA: LLM Semantic Enrichment (Vocabulary Mapping)

> [!NOTE]
> This is an advanced, optional step. It runs a local Large Language Model to
> semantically analyse each text line and map it to the controlled TEATER/AMCR
> archaeological vocabulary. Two inference backends are supported:
> **`transformers`** (HuggingFace + BnB 4-bit, single GPU, models ≤ 31 B) and
> **`vllm`** (multi-GPU, Automatic Prefix Caching, native guided JSON decoding,
> models ≥ 70 B or any multi-GPU node).

This pipeline goes beyond traditional keyword extraction by using **Constrained Decoding**.
For the `transformers` backend this is implemented via Pydantic schemas and `lmformatenforcer`.
For the `vllm` backend, guided decoding is handled natively by **xgrammar** inside vLLM —
no additional library is required. In both cases the model is mathematically prevented from
producing any token that would violate the predefined JSON structure or select a vocabulary
term outside the thematic dictionary, entirely eliminating hallucinated formatting.

### ⚙️ Configuration ([llm_config.txt](llm_config.txt) 📎)

The pipeline reads all runtime parameters from `llm_config.txt` in the repository root.
The minimum required change is `MODEL_KEY`; every other key has a sensible default.

```text
# Single-GPU (BACKEND=transformers): qwen-3.6-27b-it | gemma-4-31b-it | qwen3-14b |
#                                    qwen-3.5-9b-it | qwen3-8b | qwen2.5-14b-awq |
#                                    qwen2.5-7b | gemma-3-12b-it
# MoE / GGUF (single GPU):           gemma-4-26b-moe-gguf | qwen-3.6-35b-moe
# Multi-GPU (BACKEND=vllm):          qwen3-235b-a22b-fp8 | deepseek-v3 | llama4-maverick | llama3.1-70b
MODEL_KEY=qwen-3.6-27b-it

# Only needed for gated models: gemma-4-*, llama4-maverick, llama3.1-70b
# HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxx

INPUT_DIR=data_samples/DOC_LINE_CATEG
OUTPUT_DIR=data_samples/KW_PER_DOC_LLM
VOCAB_PATH=data_samples/vocab/union_nested.json
PARADATA_DIR=paradata

# Attach the surviving vocabulary term's source record id(s) to each enrichment as
# teater_category_ids (issue #6, M7). Kept behind a switch since it was agreed to be
# reversible: "list them now and drop it if it will create some issues."
EMIT_CATEGORY_IDS=true

INCLUDE_NON_TEXT=true
MIN_CHAR_COUNT=3
MIN_CHAR_NON_TEXT=8
MIN_ALPHA_RATIO_NON_TEXT=0.4

# ── System prompt ─────────────────────────────────────────────────────────────
# The instruction text lives in prompts/system_prompt.txt as [[named blocks]]; these
# flags choose which of them render. The run banner prints the resulting on/off list,
# so a log always says what the model was told. See prompts/RUNBOOK.md.
PROMPT_TEMPLATE=prompts/system_prompt.txt
PROMPT_TASK_EXTRACT=true
PROMPT_TASK_SELECT=true
PROMPT_METATEXT_RULE=true
PROMPT_OCR_NORMALISATION=true
PROMPT_EXACT_TERM=true
PROMPT_EXAMPLES=true

# The one three-way switch: strict | preference | off. Paired with
# taxonomy_config.json's geo_guardrail.active — vocab_build.py refuses a build where
# the two disagree.
PROMPT_GEO_GUARDRAIL=preference

# Term-list layout: facet_sub | facet | flat. Same terms, same truncation; only the
# headers move.
PROMPT_VOCAB_GROUPING=facet_sub

# ── Inference parameter overrides ─────────────────────────────────────────────
# ALL of these are COMMENTED OUT in the shipped file. Backend, GPU count, memory
# utilisation, batch size and context cap are resolved automatically from the model
# registry in llm_utils.py; the startup log prints every effective value next to
# where it came from (← llm_config.txt / model default / global default). Uncomment
# a line only to deviate from the model's recommended configuration.
# BACKEND=vllm                 # transformers (single GPU, ≤ 31 B) | vllm (multi-GPU)
# TENSOR_PARALLEL_SIZE=8       # GPUs to shard across (vLLM only)
# GPU_MEMORY_UTILIZATION=0.88  # Fraction of each GPU's VRAM for the KV cache
# VLLM_BATCH_SIZE=8            # Lines per generate() call
# MAX_MODEL_LEN=16384          # Cap the context window to reduce KV-cache pressure
# CPU_OFFLOAD_GB=0             # Weights to keep in CPU RAM when VRAM is short
# GUIDED_DECODING_BACKEND=xgrammar
# ENABLE_PREFIX_CACHING=false  # Not recommended; reduces throughput
```

The nine `PROMPT_*` keys are the prompt's whole configuration surface;
[`prompts/RUNBOOK.md`](prompts/RUNBOOK.md) 📎 documents each block, what it costs, and
which pairings are unsafe to change alone.

### 🗂 Workflow

**1. Vocabulary Harvesting ([vocab_build.py](vocab_build.py) 📎)**

The vocabulary is built in two stages, and only the first needs the internet:

```
harvest (network)   →   FLAT artifacts    →   nest (pure)    →   NESTED artifacts
vocab_sources.py        *_flat.{json,csv}     vocab_manager      *_nested.json
```

[`vocab_sources.py`](vocab_sources.py) 📎 harvests two controlled vocabularies:

| Source               | How                                                                                                                                              | What comes back                                                                                                                                                                   |
|----------------------|--------------------------------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **AMCR** heslář      | OAI-PMH, `api.aiscr.cz/2.2/oai?set=heslo`                                                                                                        | Czech–English pairs **plus** `ident_cely`, `nazev_heslare` (which of the ~50 controlled lists the term belongs to), `popis`, `zkratka`, `razeni`, broader terms and SKOS mappings |
| **TEATER** thesaurus | the 12 pinned `import_*.json` files in [`ARUP-CAS/aiscr-teater`](https://github.com/ARUP-CAS/aiscr-teater), or live `teater.aiscr.cz/api/export` | 4 134 concepts in 12 branches, trilingual labels, scope notes, and the real broader/narrower hierarchy                                                                            |

[`vocab_manager.py`](vocab_manager.py) 📎 then groups the flat terms into the thematic
taxonomy defined by [taxonomy_config.json](data_samples/taxonomy_config.json) 📎. Placement
is tried in precedence order — a per-term correction in
[taxonomy_overrides.json](data_samples/taxonomy_overrides.json) 📎, AMCR list membership
(`heslar_map`), TEATER branch (`teater_branch_map`, resolved most-specific-first so a
depth-2 sub-branch like `muzeum` can be moved without moving its whole parent branch),
the legacy keyword match, a cross-source rescue, an opt-in LLM fallback, then `Other` —
and **every placement records the rule that made it** in `*_placement_audit.csv`, so the
grouping can be reviewed rather than taken on trust.

Two labels can collide (AMCR and TEATER both use `zámek` for "lock" *and* "château").
`vocab_sources.to_term_pairs()` treats a same-label group as one concept by default —
the winning record's id survives, every other one is listed on it as `discarded_ids`
(issue #6, M7) — and only pulls a record into its own bracketed entry
(`"zámek (sídlo elity)"`) when `taxonomy_overrides.json` explicitly flags it as a
genuine homonym (M8). Guessing that from a differing English gloss alone would mistake
ordinary translation variance for a real split far more often than it would catch one.

**Every vocabulary decision is a config edit, not a code change.** The two JSON files
are the whole surface a domain reviewer needs; nothing below requires touching Python:

| In `taxonomy_config.json` → `_settings` | Decides                                                                                                           |
|-----------------------------------------|-------------------------------------------------------------------------------------------------------------------|
| `heslar_map`, `teater_branch_map`       | which facet a whole AMCR list or TEATER branch lands in, or `__exclude__`                                         |
| `_exclusions`                           | why each exclusion stands, and whether it is `settled` or still open (`open_geo_ethnic` / `open_other`)           |
| `geo_guardrail`                         | whether the prompt's "never select a country/language/region name" clause is in force, and which rules it reaches |
| `nested_keep`                           | which harvested keys reach the prompt payload                                                                     |
| `admin_stop_words`                      | what sorts to the back of a facet, and so what survives prompt truncation                                         |
| `composite_separators`                  | what splits a composite `X/Y` label                                                                               |
| `tie_break`, per-facet `priority`       | facet order — load-bearing, since the prompt truncates a *prefix*                                                 |

| In `taxonomy_overrides.json`, per `(source, id)` | Decides                                                                                     |
|--------------------------------------------------|---------------------------------------------------------------------------------------------|
| `facet`                                          | one term's facet, including `"__exclude__"` to drop a single term from a list worth keeping |
| `sub`                                            | one term's sub-header — otherwise a moved term keeps the header of the list it left         |
| `qualifier_cs`                                   | pull a confirmed homonym out of its dedup group as `"<cs> (<qualifier>)"`                   |
| `same_as` / `same_as_suppress`                   | add or drop a composite/component equivalence link; neither changes what the prompt offers  |

**The system prompt is a config surface too.** Its instruction text lives in
[`prompts/system_prompt.txt`](prompts/system_prompt.txt) 📎 as `[[named blocks]]` in
render order; `llm_config.txt`'s `PROMPT_*` flags choose which of them reach the model,
and the run banner prints the resulting on/off list so a log always says what the model
was told. `PROMPT_GEO_GUARDRAIL` is the one three-way switch (`strict` / `preference` /
`off`) because the geographic rule has three states, and it is paired with
`taxonomy_config.json`'s `geo_guardrail.active`:
[`prompts/output_template.json`](prompts/output_template.json) 📎 documents the resulting
per-document output file.

Reading the prompt needs neither a GPU nor the model stack — `prompt_template.py` imports
nothing outside the standard library, so these run in a bare checkout:

```bash
python3 prompt_template.py --blocks                  # which rules are on, and what each costs
python3 prompt_template.py --preview                 # the instruction text, term list elided
python3 prompt_template.py --full  > prompt.txt      # the whole prompt, all 4 718 terms
python3 prompt_template.py --diff PROMPT_GEO_GUARDRAIL=strict \
                                 PROMPT_GEO_GUARDRAIL=preference
python3 prompt_template.py --write                   # regenerate the committed sheets
python3 prompt_template.py --check                   # exit 1 if a sheet is out of date
```

`--full` renders the untruncated prompt — instructions under the current flags, then every
term `VOCAB_PATH` offers, grouped exactly as `build_system_prompt()` groups them (both call
the same two functions, and a test asserts they agree byte for byte). It is what a model
with room for the whole vocabulary sees; at a tighter window the run drops a tail of terms,
which [`context_budget.csv`](data_samples/vocab/context_budget.csv) 📎 sizes per window.

The four sheets under [`prompts/`](prompts) 📎 — `prompt_blocks.txt`, `prompt_preview.txt`,
`prompt_full.txt`, `prompt_guardrail_diff.txt` — are the output of the first four commands,
committed so a reviewer can read the prompt in a diff without running Python. They are
**generated, never hand-edited**: `--write` rewrites them, `--check` fails when the
vocabulary, a flag or the template has moved without them, and
[.github/workflows/vocab-drift.yml](.github/workflows/vocab-drift.yml) runs that check on
every PR touching either half.

**`PROMPT_VOCAB_GROUPING` controls the layout of the term list**, not its contents:
`facet_sub` (shipped — `--- Facet / Subgroup ---`, both curated levels), `facet` (facet
headers only) or `flat` (no headers). It exists to answer @motyc's question in
[issue #6](https://github.com/ufal/atrium-nlp-enrich/issues/6#issuecomment-5424905539) —
whether the facet grouping affects results at all, now that the whole vocabulary fits a
128k window. All three offer the same terms and truncate identically; `facet` and `flat`
also preserve term order, while `facet_sub` makes each facet's sub-groups contiguous. So
`facet` vs `flat` isolates the ~125 header lines and `facet_sub` vs `facet` measures the
source's second level. The headers are not free: at an 8 192-token window they cost 26
terms, at 32 768 they cost 126, and at 128k nothing, since everything fits either way.

`validate_settings()` refuses an edit that would not do what it says — an undeclared
facet, a relabel for a list no map places, a reason for something nobody excludes, an
unknown override key, a stale `(source, id)`, a pair both linked and suppressed, two
overrides that would build the same bracketed key — and reports every problem at once
rather than one per rebuild. `vocab_build.py` additionally renders the prompt the config
selects and refuses to build a vocabulary that contradicts its geographic guardrail.
Two runbooks carry the operational detail, and they are the pages to read before
touching either half:
[`data_samples/vocab/RUNBOOK.md`](data_samples/vocab/RUNBOOK.md) 📎 — every vocabulary
script, the eight review sheets, and the full "where a decision gets recorded" table;
[`prompts/RUNBOOK.md`](prompts/RUNBOOK.md) 📎 — the eleven prompt blocks, the nine flags,
the guardrail's two halves, and the output contract.

```bash
# stage 1 + 2, needs network access to aiscr.cz
python3 vocab_build.py --source both --stats

# stage 2 only: re-nest from the committed flat files after editing the taxonomy.
# Pure, offline, sub-second — this is the loop for tuning the taxonomy.
python3 vocab_build.py --from-flat --stats
python3 vocab_build.py --from-flat --check     # exit 1 if the artifacts would change
```

If this machine cannot reach `aiscr.cz`, run the **Vocabulary Refresh** workflow
([.github/workflows/vocab-refresh.yml](.github/workflows/vocab-refresh.yml)) — a hosted
runner harvests and uploads the artifacts.

> [!NOTE]
> The nested files are deliberately **not** written with `sort_keys=True`. Theme order is
> priority-descending and load-bearing: `build_system_prompt()` iterates the file in
> insertion order and truncates a *prefix* of the resulting term list, so alphabetising
> the keys would silently change which themes survive a tight context budget. Determinism
> comes from the explicit priority ordering plus a `(boilerplate, razeni, label)` sort
> within each theme. Provenance lives in a sidecar `*.meta.json`, not inline — every
> consumer reads the nested file as `{theme: terms}`, so an inline `_meta` key would be
> rendered to the model as a phantom theme.

`python3 vocab_manager.py` still works and still performs the legacy AMCR-only sync.

#### Reviewing the vocabulary

Two read-only tools turn the built vocabulary into sheets a domain reviewer can rule on.
Neither writes to the vocabulary, and neither guesses a semantic verdict — they rank and
surface candidates; a human decides, and the decision goes back as a config edit. Both run from a
checkout and are left out of the published images (`.dockerignore`,
[atrium-project#72](https://github.com/ufal/atrium-project/issues/72)).

[`vocab_review.py`](vocab_review.py) 📎 — **eight sheets, offline and pure**, built from
the committed `*_flat.json` plus the taxonomy config:

```bash
python3 vocab_review.py --all            # all eight, into data_samples/vocab/
python3 vocab_review.py --collisions     # collision_review.csv        same-label groups (M8/M13)
python3 vocab_review.py --composites     # composite_pairs.csv         "X/Y" vs standalone X, Y
python3 vocab_review.py --exclusions     # exclusion_impact.csv        what each exclusion costs
python3 vocab_review.py --subbranches    # teater_subbranch_impact.csv the same, one level finer
python3 vocab_review.py --reinstate      # reinstatement_preview.csv   usable count + token delta
python3 vocab_review.py --specificity    # specificity_pairs.csv       term offered under its own parent
python3 vocab_review.py --budget         # context_budget.csv          what survives each window
python3 vocab_review.py --census         # facet_census.csv            what is in each facet, and its cost
```

[`corpus_review.py`](corpus_review.py) 📎 — **three evidence sheets** matching the
vocabulary against real report text by UDPipe lemma, which is the evidence standard for
keeping or dropping a branch:

```bash
python3 corpus_review.py --all           # corpus_term_evidence.csv, corpus_branch_evidence.csv,
                                         # gold_workbook.csv (+ corpus_review.meta.json)
```

⚠️ `corpus_review.py` needs the document corpus, and the repository tracks only three
**synthetic** demo documents — the real reports arrive as the issue
[#19](https://github.com/ufal/atrium-nlp-enrich/issues/19) attachment and are untracked.
Every run prints its corpus size before writing, and the tool **refuses** to overwrite
sheets built from a larger corpus rather than silently replacing real evidence with
placeholder numbers. On a clean checkout these three are a smoke test, not evidence.

Both sets are covered by a drift test: a taxonomy edit that is not followed by a
regeneration fails `tests/test_vocab_review.py`, for the same reason the artifacts have
their own gate. See [`data_samples/vocab/RUNBOOK.md`](data_samples/vocab/RUNBOOK.md) 📎
for what each sheet answers and how to read it.

**2. LLM Inference Pipeline ([llm_run.py](llm_run.py) 📎)**

Reads the CSV files, filters lines by quality, injects the nested vocabulary and a
sliding context window into the system prompt, and executes constrained generation.
Output files are named `<stem>_enriched.json` and written to
`KW_PER_DOC_LLM_<model_suffix>/`.

> [!TIP]
> All model-loading logic, constrained-decoding helpers, and prompt templates live in
> [llm_utils.py](llm_utils.py) 📎 and are shared between both backends.

```bash
# Transformers backend (default)
python3 llm_run.py

# Custom config file
python3 llm_run.py my_config.txt
```

**For multi-GPU runs (vLLM backend):**

```bash
# 1. Edit llm_config.txt:
#    BACKEND=vllm
#    MODEL_KEY=qwen3-235b-a22b-fp8
#    TENSOR_PARALLEL_SIZE=2
#    ENABLE_PREFIX_CACHING=true
python3 llm_run.py
```

### 🖥 Model Registry

The built-in registry in `llm_utils.py` covers the full range of supported models.
All VRAM figures assume BnB 4-bit for the transformers backend and FP8/BF16 for vLLM.

#### Single-GPU — `BACKEND=transformers` (or `BACKEND=vllm`)

| Registry key      | Model                               | Size       | Context | Est. VRAM | Notes                                                     |
|-------------------|-------------------------------------|------------|---------|-----------|-----------------------------------------------------------|
| `qwen-3.6-27b-it` | Qwen/Qwen3.6-27B [^24]              | 27 B dense | 262 k   | ~18 GB    | **Default.** Best accuracy/VRAM ratio on a single GPU.    |
| `gemma-4-31b-it`  | google/gemma-4-31B-it [^22]         | 31 B dense | 256 k   | ~21 GB    | Highest single-GPU accuracy. Gated — `HF_TOKEN` required. |
| `qwen3-14b`       | OpenPipe/Qwen3-14B-Instruct [^18]   | 14 B dense | 128 k   | ~9 GB     | Good baseline; thinking mode suppressed automatically.    |
| `qwen-3.5-9b-it`  | Qwen/Qwen3.5-9B [^26]               | 9 B dense  | 262 k   | ~6 GB     | Entry-level (8 GB VRAM).                                  |
| `qwen3-8b`        | Qwen/Qwen3-8B [^19]                 | 8 B dense  | 128 k   | ~16 GB    | BF16 (no 4-bit); straightforward baseline.                |
| `qwen2.5-14b-awq` | Qwen/Qwen2.5-14B-Instruct-AWQ [^12] | 14 B AWQ   | 128 k   | ~9 GB     | Pre-quantized; fast on NVIDIA GPUs.                       |
| `qwen2.5-7b`      | Qwen/Qwen2.5-7B-Instruct [^13]      | 7 B dense  | 32 k    | ~14 GB    | BF16; short context window.                               |
| `gemma-3-12b-it`  | google/gemma-3-12b-it [^20]         | 12 B dense | 128 k   | ~8 GB     | Good bilingual extraction. Gated.                         |

#### MoE models — GGUF / llama.cpp fallback (any GPU, any VRAM)

| Registry key           | Model                                    | Active params | Context | Notes                                                                     |
|------------------------|------------------------------------------|---------------|---------|---------------------------------------------------------------------------|
| `gemma-4-26b-moe-gguf` | bartowski/google_gemma-4-26B-A4B-it-GGUF | 4 B           | 8 k     | BnB 4-bit unsupported (fused experts). Q4_K_M quantization via llama.cpp. |

#### MoE models — `BACKEND=vllm` (single GPU or multi-GPU)

| Registry key          | Model                           | Active params | Context | Notes                                              |
|-----------------------|---------------------------------|---------------|---------|----------------------------------------------------|
| `qwen-3.6-35b-moe`    | Qwen/Qwen3.6-35B-A3B [^23]      | 3 B           | 262 k   | 35 B total / 3 B active. Single GPU usually fits.  |
| `gemma-4-26b-moe`     | google/gemma-4-26B-A4B-it [^25] | 4 B           | 256 k   | 26 B total / 4 B active. Gated.                    |
| `gemma-4-26b-moe-awq` | google/gemma-4-26B-A4B-it [^25] | 4 B           | 256 k   | AWQ-quantised variant of `gemma-4-26b-moe`. Gated. |

#### Large models — `BACKEND=vllm`, `TENSOR_PARALLEL_SIZE ≥ 2`

| Registry key          | Model                                               | Total / Active            | Context | Rec. TP | Notes                                                                   |
|-----------------------|-----------------------------------------------------|---------------------------|---------|---------|-------------------------------------------------------------------------|
| `qwen3-235b-a22b-fp8` | Qwen/Qwen3-235B-A22B-Instruct-2507-FP8 [^27]        | 235 B / 22 B              | 128 k   | **2**   | **Recommended for 144 GB / 200 GB nodes.** Native FP8 (~117 GB loaded). |
| `qwen3-235b-a22b`     | Qwen/Qwen3-235B-A22B-Instruct-2507 [^27]            | 235 B / 22 B              | 128 k   | 2       | BF16 variant — heavier than FP8.                                        |
| `deepseek-v3`         | deepseek-ai/DeepSeek-V3 [^28]                       | 671 B MoE / —             | 128 k   | **4**   | FP8 official checkpoint available. 4×80 GB minimum.                     |
| `llama4-maverick`     | meta-llama/Llama-4-Maverick-17B-128E-Instruct [^29] | 128 experts / 17 B active | 1 M     | 2       | Multimodal. 1 M token context. Gated — `HF_TOKEN` required.             |
| `llama3.1-70b`        | meta-llama/Meta-Llama-3.1-70B-Instruct [^30]        | 70 B dense / —            | 128 k   | 2       | Also works with `transformers` + 4-bit on 2×40 GB. Gated.               |

> [!TIP]
> **Automatic Prefix Caching (APC)** — enabled by default for the vLLM backend
> (`ENABLE_PREFIX_CACHING=true`). The system prompt (which embeds the full TEATER
> vocabulary) is computed once per run; its KV-cache is reused across every line in
> every document. This is the primary throughput multiplier: on a 500-line document the
> vocabulary forward pass happens once instead of 500 times. APC also removes the need
> to truncate the vocabulary to fit the token budget — the full thematic dictionary is
> injected when APC is active.

### 📁 Inputs and Outputs

* **Input:** `DOC_LINE_CATEG/*.csv` (contains `file_id`, `page_num`, `line_num`,
  `categ`, `quality_score`, and raw `text`).
* **Output:** `KW_PER_DOC_LLM_<model_suffix>/*_enriched.json` — one file per document,
  containing an array of JSON objects that merge CSV metadata with the LLM's semantic
  extraction.
* **Abort sidecar:** `KW_PER_DOC_LLM_<model_suffix>/*_enriched.abort.json` — written
  alongside the main output **only** when a document is abandoned after 10 consecutive
  inference errors. Its presence is the canonical signal that the corresponding JSON
  file contains partial results.

**Example output record:**
```json
{
  "file_id": "CTX195603828",
  "page": 1,
  "line": 14,
  "categ": "Text",
  "quality_score": 0.98,
  "original_text": "Výzkum odhalil základy gotického kostela ze 14. století.",
  "enrichment": {
    "extracted_keywords_cs": ["základy", "gotický kostel"],
    "extracted_keywords_en": ["foundations", "gothic church"],
    "teater_category": "kostel",
    "teater_category_ids": [
      { "source": "amcr", "id": "HES-000021" },
      { "source": "amcr", "id": "HES-000465" },
      { "source": "teater", "id": "1333" }
    ],
    "confidence_score": 0.95
  }
}
```

`teater_category_ids` (present when `EMIT_CATEGORY_IDS=true`, the default) lists every
source record the selected vocabulary term absorbed during dedup — issue #6, M7. It is
attached after inference, from the vocabulary's own `discarded_ids`; the prompt and
schema are unaffected. When the selected term was a bracketed disambiguation (B3, e.g.
`"zámek (sídlo elity)"`), `teater_category` is stripped back to the bare label
(`"zámek"`) before it is written, and `teater_category_ids` carries the id that
disambiguates which sense was meant — a term that legitimately contains parentheses in
its own source label (e.g. `"GPS (navigační systém)"`) is never touched.

**Abort sidecar format (`*_enriched.abort.json`):**
```json
{
  "aborted": true,
  "abort_reason": "10 consecutive inference errors",
  "processed_before_abort": 42,
  "errors_before_abort": 10,
  "timestamp_utc": "2026-05-20T09:14:33"
}
```

> [!NOTE]
> None of the per-model output sets below is committed to this repository — each is the
> real output of a real run against the full report corpus, not a sample bundled with
> the code (same reason `data_samples/DOC_LINE_CATEG/` itself holds only three synthetic
> demo documents). The directory names are the `OUTPUT_DIR` a local run with that
> `MODEL_KEY` produces; the footnote on each is the model card.

Output examples per model (directory names, not links — see the note above):
- `KW_PER_DOC_LLM_qwen3_14b` by Qwen 3-14B [^18]
- `KW_PER_DOC_LLM_qwen25_14b_awq` by Qwen 2.5-14B AWQ [^12]
- `KW_PER_DOC_LLM_gemma_3_12b_it` by Gemma 3-12B-IT [^20]
- `KW_PER_DOC_LLM_qwen_36_27b_it` by Qwen 3.6-27B-IT [^24]
- `KW_PER_DOC_LLM_gemma_4_31b_it` by Gemma 4-31B-IT [^22]
- `KW_PER_DOC_LLM_qwen_35_9b_it` by Qwen 3.5-9B-IT [^26]
- `KW_PER_DOC_LLM_llama31_70b` by LLaMA 3.1-70B [^30]
- `KW_PER_DOC_LLM_qwen3_8b` by Qwen 3-8B [^19]

Pending (sample runs in progress):
- `KW_PER_DOC_LLM_qwen_36_35b_moe` by Qwen 3.6-35B-MoE [^23]
- `KW_PER_DOC_LLM_gemma_4_26b_a4b_it` by Gemma 4-26B-A4B-IT [^25]

Archived (unsuccessful — evaluation notes in issue #6; would have been under
`archived_KW_PER_DOC_LLM/`):
- `KW_PER_DOC_LLM_mistral_nemo_12b` by Mistral Nemo 12B [^14]
- `KW_PER_DOC_LLM_aya_expanse_8b` by Aya Expanse 8B [^15]
- `KW_PER_DOC_LLM_bielik_11b_v30` by Bielik 11B v3.0 [^16]
- `KW_PER_DOC_LLM_llama31_8b` by LLaMA 3.1-8B [^17]
- `KW_PER_DOC_LLM_ministral_3_14b` by Ministral 3-14B [^21]
- `KW_PER_DOC_LLM_qwen3_8b` (early run) by Qwen 3-8B [^19]
- `KW_PER_DOC_LLM_qwen25_7b` by Qwen 2.5-7B [^13]

### 📊 Paradata Integration

Just like the main shell-script pipelines, LLM enrichment natively hooks into
`atrium_paradata.py` and automatically logs:

* Full snapshot of [llm_config.txt](llm_config.txt) 📎 and quality-filter settings.
* **Which vocabulary build was used** — read from the `*.meta.json` beside the artifact:
tool version, term count, the sha256 of both taxonomy files, and each source's record
count and pinned ref (TEATER's harvest commit). A run is reproducible only if the
vocabulary it saw is identifiable, and every placement decision is a function of those
two sha256s.
* **Both vocabulary sources as licence components.** The AMCR heslář and the TEATER
thesaurus are CC0, as their rights holder stated on 2026-09-28 (atrium-project#6; records
written earlier say CC BY-NC 4.0), and declared *conditional* in
[para_config.txt](para_config.txt) 📎, so they are components of a run's licence only when
`log_component()` names them — and, being CC0, never make it more restrictive.
Logged per source actually present in the build — an AMCR-only artifact does not claim
it used TEATER data.
* Total processed lines (`json` success events).
* Per-line tracking of filter skips (`skipped_filter`), inference faults
(`skipped_error`), and already-completed files (`already_exists`).
* **Abort events** — when a document is abandoned after 10 consecutive inference errors,
the paradata entry records the abort reason alongside the count of lines processed
before the failure. A sidecar `*.abort.json` file is also written next to the
(partial) output JSON for easy programmatic detection.
The resulting logs are dropped into the specified `PARADATA_DIR` alongside the other pipeline execution records.

---

## EXTRA: REST API Service

The pipeline now includes a fully-featured **FastAPI REST service** that exposes the core NLP enrichment and rescaling functionalities over HTTP.

* **Single-file enrichment:** Upload CSV, XLSX, or plain text to the `/enrich` endpoint and receive a combined JSON envelope (or ZIP workspace) with TEITOK XML, keywords, paradata, and NER summaries.
* **Coordinate Rescaling:** Use the `/rescale` endpoint to align XML spatial coordinates to specific target image resolutions directly over the network.
* **Job Management:** Background processing for larger documents with a asynchronous `/jobs` queue.

For complete setup instructions, payload examples, and endpoint documentation, refer to the [Service README](service/README.md).

---

## Paradata Logs

Every pipeline script records structured provenance metadata through
[atrium_paradata.py](atrium_paradata.py) 📎.  Two complementary log surfaces
are produced after a run:

### `<OUTPUT_DIR>/paradata/` — structured run logs 📂

Each of the four pipeline scripts produces one JSON file here, named with the
pattern:

```
YYMMDD-HHmmss_nlp-enrich.json
```

where the timestamp prefix is the UTC wall-clock time at which the script
started.  Because every script is an independent invocation, a complete
four-step run will create four separate files, making it straightforward to
audit individual stages in isolation.

The paradata logs (samples in directory [paradata](data_samples/paradata) 📂) capture key details about each pipeline stage,
including the program name, run ID, execution duration, configuration parameters, input and output statistics,
and performance metrics. They also document skipped files with reasons and provide a breakdown of output
types and processing rates for benchmarking. This structured metadata ensures traceability and facilitates
auditing of the pipeline's execution.

The declared output types per stage are:

| Script              | Types recorded                                                                                    |
|---------------------|---------------------------------------------------------------------------------------------------|
| `api_1_manifest.sh` | `tsv` (one entry per input CSV/XLSX processed into the manifest)                                  |
| `api_2_udp.sh`      | `conllu` (one per document)                                                                       |
| `api_3_nt.sh`       | `tsv` (one per page — count reflects individual page TSV files)                                   |
| `api_4_stats.sh`    | `csv` always; `conllu` when `SAVE_CONLLU_NE=true`; `xml` when `SAVE_TEITOK=true`                  |
| `keywords.py`       | `csv_per_doc` (one per document keyword CSV) and `csv_summary_row` (one summary row per document) |

> [!NOTE]
> When resuming an interrupted run (steps 2–4 skip already-finished documents
> via `[ -f "$out" ] && continue`), the resumed documents are not re-counted in
> the paradata JSON.  The `input_files_total` field still reflects the full
> manifest, so `skipped_files + successfully_processed` will be less than
> `input_files_total` for partial runs.  This is expected behaviour; the
> difference represents the documents carried over from a previous invocation.

> [!NOTE]
> **Paradata state files.** While a pipeline script is running, [atrium_paradata.py](atrium_paradata.py)
> stores intermediate state in a plain-text JSON file inside `<OUTPUT_DIR>/paradata/`
> (named `.state_<runid>_<program>.json`).  This file is automatically removed when
> the script completes.  Because it is plain JSON it can be inspected with any text
> editor if a run is interrupted unexpectedly.

### `<OUTPUT_DIR>/processing.log` — human-readable runtime log 📄

[api_common.sh](api_util/api_common.sh) 📎 exposes a `log()` helper that timestamps and
`tee`-appends warnings and errors to this flat file. The four main pipeline scripts
(`api_1_manifest.sh` … `api_4_stats.sh`) write to `processing.log` indirectly through the
Python helpers, which print timestamped messages to stderr; any script that sources
`api_common.sh` can also write here via the `log()` function directly.

```
[2026-01-15 09:42:11] [WARN] UDPipe failed (HTTP 503). Retrying in 2s…
[2026-01-15 09:42:14] [ERR]  UDPipe failed permanently after 5 attempts.
```

This file accumulates across reruns;
it is the first place to check when a document appears in
`skipped_files_detail` but the reason is terse.

### `TEMP/` — intermediate working files 📂

`TEMP/` (set by `WORK_DIR` in [config_api.txt](config_api.txt) 📎) holds
transient artefacts that are only needed during processing and can be deleted
once the full pipeline has completed successfully:

```
TEMP/
├── CHUNKS/
│   ├── <doc_id>/
│   │   ├── chunk_0.txt      # OCR-line-preserving text fragment sent to UDPipe
│   │   ├── chunk_1.txt
│   │   └── …
│   └── …
└── nametag_response_<doc_id>.conllu.json   # raw JSON reply from the NameTag API
```

`CHUNKS/` is produced by [api_util/chunk.py](api_util/chunk.py) 📎 which splits
documents that exceed `WORD_CHUNK_LIMIT` (default 900 words) into
sentence-boundary-aware fragments before each UDPipe API call.  Each chunk file
preserves the original OCR line structure (one line per row) so that UDPipe
receives correct sentence-boundary signals between text lines.  The per-chunk
plain-text files and the raw NameTag JSON responses carry no provenance value
after the CoNLL-U files have been merged and validated; they are not tracked by
the paradata logger.

> [!TIP]
> If disk space is a concern you can safely delete `TEMP/` once
> `<OUTPUT_DIR>/UDP/` and `<OUTPUT_DIR>/NE/` have been fully populated and
> step 4 has completed without errors.  The paradata JSONs in
> `<OUTPUT_DIR>/paradata/` and the `processing.log` are the only runtime
> records worth keeping long-term.

### Document record schema 📑

The per-document record this tool reads and writes — `<doc_id>.document.json`, the pair of the
paradata above — follows [atrium_document.schema.json](atrium_document.schema.json) 📎, schema
version **`1.0`**. That version is frozen as the hub tag
[`doc-schema-v1`](https://github.com/ufal/atrium-project/releases/tag/doc-schema-v1)
(ufal/atrium-project@`544298b`), and it is the baseline this tool implements:

* [tests/test_schema_freeze.py](tests/test_schema_freeze.py) 📎 checks this repository's schema
  against the frozen copy beside it,
  [atrium_document.schema.doc-schema-v1.json](atrium_document.schema.doc-schema-v1.json) 📎: the
  copy is exactly the tagged file, nothing declared at the freeze has been removed or renamed, and
  every change made since is registered;
* all three files, like `atrium_document.py`, are vendored from the hub and kept byte-identical to
  its `v1` tag by the `para-drift` CI check, so they are never edited in this repository. The hub
  additionally checks that every record shape the tools write validates under both the frozen and
  the current schema.

What may change after the freeze, and what a new major version takes, is in the hub's
[Freeze & conformance](https://github.com/ufal/atrium-project/blob/main/docs/document_schema.md#freeze--conformance).
[CITATION.cff](CITATION.cff) 📎 carries the same reference under `references`.

---

### One-command pipeline run (`run_pipeline.py`)

While each stage can be launched manually (see [Workflow Stages](#workflow-stages)),
[run_pipeline.py](run_pipeline.py) 📎 chains them end-to-end and merges every per-stage paradata JSON
produced during the run into a single `pipeline-run-merged` record.

```bash
# Full core run: api_1 → api_2 → api_3 → api_4
python3 run_pipeline.py

# Core run plus keyword extraction (CPU-only YAKE backend, default)
python3 run_pipeline.py --kw

# Keyword extraction with the GPU KeyBERT backend
python3 run_pipeline.py --kw --kw-method keybert

# Add the optional LLM semantic-enrichment stage (needs requirements_llm.txt)
python3 run_pipeline.py --kw --llm

# Run only a subset of the core stages (canonical order is always enforced)
python3 run_pipeline.py --stages udp nt

# Resume after an interruption: start from a chosen stage, skip every earlier one
python3 run_pipeline.py --start-from nt

# Skip individual stages (re-run only NER + stats, leave manifest/UDPipe as-is)
python3 run_pipeline.py --skip-manifest --skip-udp

# Clear stale .state_* checkpoint sidecars from PARADATA_DIR before running
python3 run_pipeline.py --clean-state

# Force execution: bypass missing dependency checks and ignore individual stage failures
python3 run_pipeline.py --kw --kw-method keybert --force

# Validate configuration and resolve the plan without running anything
python3 run_pipeline.py --dry-run

# Print the resolved config + stage plan as JSON (for wrappers / healthchecks)
python3 run_pipeline.py --print-config json
```

The runner reads the **same** [config_api.txt](config_api.txt) 📎 that the shell stages source, so Python and Bash always agree on `OUTPUT_DIR`, `PARADATA_DIR`, and the input/output paths.

#### What the runner does

1. **Resolves config** from [config_api.txt](config_api.txt) 📎 (with `$VAR` / `${VAR}` expansion).
2. **Runs each stage in order**, spacing stage starts by ≥ 1.1 s so the
1-second-resolution paradata filenames (`YYMMDD-HHmmss_nlp-enrich.json`)
never collide.
3. **Collects** the paradata JSON each stage writes, scoped to **this run only**
(paradata files that already existed before the run are never merged).
4. **Merges** all per-stage records into one
`<PARADATA_DIR>/<runid>_nlp-enrich_pipeline-run.json` via
`atrium_paradata.merge_run_paradata`. The merged record accurately tracks document-level statistics across the sequential pipeline (recording true throughput without inflating input counts). The effective license of the merged record is re-derived from the **union** of every component used across the stages, so the most-restrictive rule holds end-to-end (a core run is CC BY-NC-SA 4.0; adding the YAKE backend escalates the share-alike/AGPL constraint, etc.).

#### Resume / checkpoint recovery

Long batches on constrained hardware are expensive to restart from scratch, so the
runner lets you re-enter the pipeline at any stage instead of redoing completed work.
Recovery operates at two complementary levels.

**Document-level (automatic).** Every stage already skips inputs whose output exists
(steps 2–4 via `[ -f "$out" ] && continue`; the LLM stage logs `already_exists` and
moves on), so simply re-running the same command picks up where the previous run
stopped. When the LLM stage abandons a document after 10 consecutive inference
errors it writes a `*_enriched.abort.json` sidecar next to the partial output (see
[LLM Inputs and Outputs](#-inputs-and-outputs)); that marker is the canonical signal
that a document holds partial results and should be re-run.

**Pipeline-level starting points.** To skip whole stages — not just completed
documents — the runner accepts explicit entry points over the full stage order:

| Flag                   | Effect                                                                   |
|------------------------|--------------------------------------------------------------------------|
| `--start-from <stage>` | Run from `<stage>` onward; every earlier stage is skipped.               |
| `--skip-<stage>`       | Skip one named stage, run the rest.                                      |
| `--clean-state`        | Sweep stale `.state_*.json` sidecars from `PARADATA_DIR` before running. |

`<stage>` is one of `manifest`, `udp`, `nt`, `stats`, `keywords`, `llm` (the canonical
order; `keywords`/`llm` require their `--kw`/`--llm` flags to be part of the run).
Each skip flag also has an equivalent `SKIP_<STAGE>=true` knob that can live in
[config_api.txt](config_api.txt) 📎 (e.g. `SKIP_MANIFEST=true`), so a habitual resume
profile can be persisted without retyping flags.

```bash
# UDPipe + NameTag already finished — resume at statistics, then keywords
python3 run_pipeline.py --kw --start-from stats

# Re-run only NER and statistics; keep the existing manifest and CoNLL-U
python3 run_pipeline.py --skip-manifest --skip-udp
```

Skipped stages are recorded under `skipped_stages` in the merged
`<runid>_nlp-enrich_pipeline-run.json` record, so a resumed run remains fully
auditable. An all-skipped run is treated as a **successful resume**, not an empty
failure (see [Exit codes](#exit-codes) below).

#### Provenance for containers

When the runner (or its Docker entrypoint) is started with the
`ATRIUM_RUNNER_IMAGE`, `ATRIUM_RUNNER_REPO`, and `ATRIUM_RUNNER_REF` environment
variables set, those values are forwarded to every stage subprocess and end up
in each stage's paradata record (and therefore the merged record). This ties a
run back to the exact image/commit that produced it.

```bash
ATRIUM_RUNNER_IMAGE="ghcr.io/ufal/atrium-nlp-enrich:0.22.0" \
ATRIUM_RUNNER_REF="$(git rev-parse --short HEAD)" \
python3 run_pipeline.py --kw
```

The published images are `ghcr.io/ufal/atrium-nlp-enrich:<version>` (the batch runner),
`ghcr.io/ufal/atrium-nlp-enrich-api:<version>` and `ghcr.io/ufal/atrium-nlp-enrich-llm:<version>`,
where `<version>` is the release **without** its leading `v` (`0.22.0` for `v0.22.0`) — the
target is part of the image name, not the tag. `ATRIUM_RUNNER_REF` is the git ref and keeps the `v`.

> [!NOTE]
> **Docker on Linux: run as yourself.** `./data` is part of the clone and belongs to you, while
> the images run as uid 10001 by default. `docker-compose.yaml` runs every service as
> `user: "${ATRIUM_UID:-10001}:0"`, so put your uid in `.env` once —
> `echo "ATRIUM_UID=$(id -u)" >> .env` — and the container writes `./data` as you. With
> `docker run`, pass `--user "$(id -u):0"`. Docker Desktop (macOS, Windows) needs neither.
> (atrium-project#69)

#### Exit codes

| Code | Meaning                                                                                                                                                                                                                   |
|------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `0`  | All requested stages completed; nothing flagged.                                                                                                                                                                          |
| `1`  | A stage processed **nothing** despite having input and no resume, and `FAIL_ON_EMPTY=true` (the default). Also: `--with-flexiconv` could not convert at least one document.                                               |
| `2`  | A required stage script was not found.                                                                                                                                                                                    |
| `3`  | A dependency preflight failed (e.g. `--kw-method keybert` without `keybert`/`sentence-transformers`, `--llm` without the [requirements_llm.txt](requirements_llm.txt) 📎 stack, or `--with-flexiconv` without flexiconv). |
| `5`  | The TEITOK output failed its output contract (`api_4_stats.sh`'s gate: the XSD, unique ids, resolvable references, the page rules). A writer defect: please report it.                                                    |
| `≠0` | A stage script itself exited non-zero (its code is propagated).                                                                                                                                                           |

> [!TIP]
> **Using `--force` (`-f`)** overrides exit codes `1`, `3`, and `≠0`. It bypasses preflight dependency crashes and forces `FAIL_ON_EMPTY=False`, allowing the pipeline to continue attempting subsequent stages even if one stage crashes or processes zero files.

The empty-run guard is governed by `FAIL_ON_EMPTY` in [config_api.txt](config_api.txt) 📎. A
**resumed** run — where every document was already complete and thus *skipped*, or
where a stage was skipped outright via `--start-from` / `--skip-<stage>` (see
[Resume / checkpoint recovery](#resume--checkpoint-recovery)) — is treated as
success, not an empty failure. Set `FAIL_ON_EMPTY=false` to permit genuinely empty
stages.

> [!NOTE]
> The runner never re-implements stage logic: it shells out to the exact same
> [api_1_manifest.sh](api_1_manifest.sh) … [api_4_stats.sh](api_4_stats.sh), [config_api.txt](config_api.txt), and [llm_run.py](llm_run.py) 📎 you can
> run by hand. Anything documented for those stages (resume behaviour, output
> flags, model registry, …) applies unchanged under the runner.

---


## Acknowledgements 🙏

**For support write to:** lutsai.k@gmail.com responsible for this GitHub repository [^8] 🔗

- **Developed by** UFAL [^7] 👥
- **Funded by** ATRIUM [^4]  💰
- **Shared by** ATRIUM [^4] & UFAL [^7] 🔗
- **Frameworks used**:
  - Lindat/CLARIAH-CZ **NameTag 3** API [^6] 🏷
  - Lindat/CLARIAH-CZ **UDPipe 2** API [^5] 🏷
  - local **KER** (original lemma-frequency keyword extraction) [^1] 🏷
  - **YAKE** (Yet Another Keyword Extractor, CPU statistical keyword extraction) [^10] 🏷
  - **KeyBERT** (embedding-based keyword extraction, GPU-accelerated) [^11] 🏷
  - UFAL **flexiconv** (format conversion to TEITOK XML) [^9] 🏷

**©️ 2026 UFAL & ATRIUM**

[^1]: https://github.com/ufal/ker
[^2]: https://github.com/ufal/atrium-alto-postprocess
[^3]: https://ufal.mff.cuni.cz/~strakova/cnec2.0/ne-type-hierarchy.pdf
[^4]: https://atrium-research.eu/
[^5]: https://lindat.mff.cuni.cz/services/udpipe/api-reference.php
[^6]: https://lindat.mff.cuni.cz/services/nametag/api-reference.php
[^7]: https://ufal.mff.cuni.cz/
[^8]: https://github.com/ufal/atrium-nlp-enrich
[^9]: https://github.com/ufal/flexiconv
[^10]: https://github.com/LIAAD/yake
[^11]: https://github.com/MaartenGr/KeyBERT
[^12]: https://huggingface.co/Qwen/Qwen2.5-14B-Instruct-AWQ
[^13]: https://huggingface.co/Qwen/Qwen2.5-7B-Instruct
[^14]: https://huggingface.co/mistralai/Mistral-Nemo-Instruct-2407
[^15]: https://huggingface.co/CohereForAI/aya-expanse-8b
[^16]: https://huggingface.co/speakleash/Bielik-11B-v3.0-Instruct
[^17]: https://huggingface.co/meta-llama/Meta-Llama-3.1-8B-Instruct
[^18]: https://huggingface.co/OpenPipe/Qwen3-14B-Instruct
[^19]: https://huggingface.co/Qwen/Qwen3-8B
[^20]: https://huggingface.co/google/gemma-3-12b-it
[^21]: https://huggingface.co/Aratako/Ministral-3-14B-Instruct-2512-BF16-TextOnly
[^22]: https://huggingface.co/google/gemma-4-31B-it
[^23]: https://huggingface.co/Qwen/Qwen3.6-35B-A3B
[^24]: https://huggingface.co/Qwen/Qwen3.6-27B
[^25]: https://huggingface.co/google/gemma-4-26B-A4B-it
[^26]: https://huggingface.co/Qwen/Qwen3.5-9B
[^27]: https://huggingface.co/Qwen/Qwen3-235B-A22B-Instruct-2507
[^28]: https://huggingface.co/deepseek-ai/DeepSeek-V3
[^29]: https://huggingface.co/meta-llama/Llama-4-Maverick-17B-128E-Instruct
[^30]: https://huggingface.co/meta-llama/Meta-Llama-3.1-70B-Instruct
