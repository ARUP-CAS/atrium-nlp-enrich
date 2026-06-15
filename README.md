<p align="center">
  <a href="[https://www.python.org/downloads/](https://www.python.org/downloads/)"><img src="[https://img.shields.io/badge/python-3.8+-blue.svg](https://img.shields.io/badge/python-3.8+-blue.svg)" title="Python Version"></a>
  <a href="[https://lindat.mff.cuni.cz/services/udpipe/api-reference.php](https://lindat.mff.cuni.cz/services/udpipe/api-reference.php)"><img src="[https://img.shields.io/badge/API-UDPipe%202-0055A4.svg](https://img.shields.io/badge/API-UDPipe%202-0055A4.svg)" title="UDPipe 2 API (Lindat)"></a>
  <a href="[https://lindat.mff.cuni.cz/services/nametag/api-reference.php](https://lindat.mff.cuni.cz/services/nametag/api-reference.php)"><img src="[https://img.shields.io/badge/API-NameTag%203-0055A4.svg](https://img.shields.io/badge/API-NameTag%203-0055A4.svg)" title="NameTag 3 API (Lindat)"></a>
  <a href="[https://github.com/ufal/ker](https://github.com/ufal/ker)"><img src="[https://img.shields.io/badge/dep-KER-lightgrey.svg](https://img.shields.io/badge/dep-KER-lightgrey.svg)" title="KER Keyword Extraction"></a>
  <a href="[https://opensource.org/license/mit/](https://opensource.org/license/mit/)"><img src="[https://img.shields.io/github/license/ufal/atrium-nlp-enrich](https://img.shields.io/github/license/ufal/atrium-nlp-enrich)" title="MIT License"></a>
  <a href="[https://atrium-research.eu/](https://atrium-research.eu/)"><img src="[https://img.shields.io/badge/funded%20by-ATRIUM-8A2BE2.svg](https://img.shields.io/badge/funded%20by-ATRIUM-8A2BE2.svg)" title="ATRIUM Project"></a>
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
  - [Configuration ⚙️](#-configuration-llm_configtxt-)
  - [Workflow](#-workflow)
  - [Model Registry](#-model-registry)
  - [Inputs and Outputs](#-inputs-and-outputs)
  - [Paradata Integration](#-paradata-integration)
- [Paradata Logs](#paradata-logs)
  - [`<OUTPUT_DIR>/paradata/` — structured run logs 📂](#output_dirparadata--structured-run-logs-)
  - [`<OUTPUT_DIR>/processing.log` — human-readable runtime log 📄](#output_dirprocessinglog--human-readable-runtime-log-)
  - [`TEMP/` — intermediate working files 📂](#temp--intermediate-working-files-)
  - [One-command pipeline run (`run_pipeline.py`)](#one-command-pipeline-run-run_pipelinepy)
- [Acknowledgements](#acknowledgements-)

## TEITOK XML — Unified Output Format

**TEITOK XML** (`.teitok.xml`) is the primary enriched output format of this pipeline. It is a
[TEI](https://tei-c.org/)-compliant XML format used by the [TEITOK](https://www.teitok.org/)
corpus platform, extended to carry spatially-grounded linguistic and NER annotations produced by
UDPipe and NameTag.

Each document in the collection is serialised as a single `.teitok.xml` file that integrates four
layers of information in a consistent, machine-readable structure:

| Layer                   | Content                                                                                                                                                                             |
|-------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Layout**              | Page, text-block, and line boundaries with pixel-accurate bounding boxes from the source ALTO XML, scaled to match the stored PNG images                                            |
| **Morphology & Syntax** | Per-token lemma, UPOS/XPOS tags, morphological features, and dependency relations produced by UDPipe 2                                                                              |
| **Named Entities**      | BIO-tagged entity spans with both a CoNLL-style category (`PER`, `ORG`, `LOC`, `MISC`) and a fine-grained CNEC 2.0 code (e.g. `pf` = first name, `gu` = city) produced by NameTag 3 |
| **Facsimile links**     | `<surface>` elements in `<facsimile>` that tie each page to its companion image, enabling TEITOK's side-by-side text/image view                                                     |

### Why TEITOK XML?

Storing all enrichment layers in a single interoperable format offers several practical advantages
over keeping CoNLL-U, TSV, and image files in separate silos:

- 🔍 **Full-text and attribute search** — TEITOK's built-in CQL/XPATH query engine lets users
  search across lemmas, NER types, POS tags, and raw text simultaneously.
- 🏷 **Named entity access** — entity spans (`<name type="PER" cnec="pf">`) are first-class XML
  elements: queryable, stylable, and exportable independently of the surrounding tokens.
- 🖱 **Mouseover information** — hovering over any token in the TEITOK GUI surfaces its lemma,
  morphological features, and dependency relation without leaving the page view.
- 🖼 **Page visualisation with spatial overlays** — bounding box coordinates on every `<tok>`,
  `<lb>`, and `<div>` are used by TEITOK's facsimile viewer to overlay text highlights directly
  onto the scanned page image, making OCR quality immediately visible.
- 📐 **Layout-aware structure** — text blocks (`<div type="MarginTextZone-P">`), lines (`<lb>`),
  and graphical elements (`<figure>`) preserve the physical layout of the original document.
- 🔗 **Interoperability** — TEI/XML is a widely adopted standard in digital humanities; the files
  can be ingested by other TEI-aware tools (e.g. eXist-db, Oxygen XML Editor) without conversion.

### TEITOK XML structure at a glance

```xml
<TEI xmlns="http://www.tei-c.org/ns/1.0" xml:lang="cs">
  <teiHeader> ... </teiHeader>

  <facsimile>
    <surface id="doc1.surface1" lrx="1240" lry="1754">
      <graphic url="doc1-1.png"/>
    </surface>
  </facsimile>

  <text><body>
    <pb n="1" id="doc1.pb1" facs="doc1-1.png"/>

    <div type="MarginTextZone-P" id="doc1.TB_1" bbox="142 210 1098 880">
      <s id="doc1.s1" text="Výroční zpráva 2012 .">
        <lb id="doc1.TL_1" bbox="142 210 680 255"/>

        <tok id="doc1.s1.w1" type="w" lemma="výroční" upos="ADJ"
             feats="Case=Nom|..." deprel="amod"
             bbox="142 210 310 255">Výroční</tok>

        <name type="ORG" cnec="if">
          <tok id="doc1.s1.w3" type="w" lemma="ministerstvo" upos="NOUN"
               bbox="320 210 580 255">Ministerstvo</tok>
          <tok id="doc1.s1.w4" type="w" lemma="finance" upos="NOUN"
               bbox="585 210 680 255">financí</tok>
        </n>
      </s>
    </div>
  </body></text>
</TEI>
```

> [!NOTE]
> TEITOK XML is generated by Step 4 of this pipeline (`api_4_stats.sh`) when
> `SAVE_TEITOK=true`. The source ALTO XML files must be present in `INPUT_ALTO_DIR`
> for spatial coordinates to be included. If `INPUT_ALTO_DIR` is not set, TEITOK XML
> is still produced but without bounding box attributes. If your documents are not in
> ALTO format, see [EXTRA: Converting Other Input Formats with flexiconv](#extra-converting-other-input-formats-with-flexiconv).

---

## ⚙️ Setup

Before you begin, set up your environment.

1.  Create and activate a new virtual environment in the project directory 🖥.
2.  Install the required Python packages:
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
INPUT_ALTO_DIR="$OUTPUT_DIR/altos"              # Source ALTO XML files - for TEITOK conversion
# ── Image Options ─────────────────────────────────────────────────────────────
# OPTIONAL: Only required if your companion PNG/JPEG display images have been resized 
# to a different target resolution relative to ABBYY's baseline dimensions.
# If left empty, the pipeline calibrates layout shifts natively using ALTO PrintSpace.
INPUT_PAGES_DIR=""

UDPIPE_URL="https://lindat.mff.cuni.cz/services/udpipe/api/process"
NAMETAG_URL="https://lindat.mff.cuni.cz/services/nametag/api/recognize"

MODEL_UDPIPE="czech-pdt-ud-2.15-241121"
MODEL_NAMETAG="nametag3-czech-cnec2.0-240830"

TIMEOUT=60                     # API call timeout in seconds
MAX_RETRIES=5                  # Number of retries for failed API calls
BACKOFF_FACTOR=1.5
WORD_CHUNK_LIMIT=900           # Word limit per API call

SAVE_CSV=true                  # write token-level summary CSV
SAVE_CONLLU_NE=true            # keep merged CoNLL-U with NER in MISC
SAVE_TEITOK=true               # write TEITOK-style TEI XML (flexiconv-compatible)
```

#### Execution Pipeline

Run the following scripts in sequence. Each script sources [config_api.txt](config_api.txt) 📎
directly for configuration. Retry logic and per-attempt error handling are implemented inside
the Python helper scripts ([call_udpipe.py](api_util/call_udpipe.py),
[call_nametag.py](api_util/call_nametag.py)) using exponential back-off controlled by the
`MAX_RETRIES` and `BACKOFF_FACTOR` variables. [api_util/api_common.sh](api_util/api_common.sh) 📎
is a standalone utility module that exposes a `log()` helper and an `api_call_with_retry()`
shell function for any custom scripts that choose to source it; the four main pipeline scripts
(`api_1_manifest.sh` … `api_4_stats.sh`) do not source it. Additionally, [api_util/](api_util/) 📁
contains helper Python scripts for chunking and analysis.

##### 1. Generate Manifest

Maps input text files to document IDs and page numbers to ensure correct processing order.

```bash
./api_1_manifest.sh
```

* **Input:** `../CSVS_with_TEXT/` (raw text files in subdirectories from Step 1).
* **Output:** `OUTPUT_DIR/manifest.tsv`.

Example output file [manifest.tsv](data_samples/manifest_SHORT.tsv) 📎 with **file**, **page**
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
* **Output:** `OUTPUT_DIR/UDP/*.conllu` (Intermediate per-document CoNLL-U files).

Run the following command to see how many documents have been processed into CoNLL-U files:

```bash
ls -l <OUTPUT_DIR>/UDP/ | wc -l
```
which returns the total number of CoNLL-U files created (each file corresponds to a document).

Example output directory [UDP](data_samples%2FUDP) 📁 contains per-document CoNLL-U files.

> [!NOTE]
> **Chunking and page boundaries.** [chunk.py](api_util/chunk.py)📎 splits text on OCR line boundaries (not raw whitespace),
> preserving the newline-separated structure of the source CSV so that UDPipe receives proper
> sentence-boundary hints between lines.  When a document spans multiple chunks, [call_udpipe.py](api_util/call_udpipe.py)📎
> merges them into a single CoNLL-U and injects a `# page_break = true` comment immediately before
> every sentence that began a new page in its source chunk.  All downstream scripts
> ([call_nametag.py](api_util/call_nametag.py)📎, [summarize_nt_udp.py](api_util/summarize_nt_udp.py)📎, 
> [teitok_alto.py](api_util/teitok_alto.py)📎) recognise this marker alongside the
> legacy `# sent_id = 1` page-reset convention, so both single-chunk and multi-chunk files are
> handled transparently.

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

* **Input:** `OUTPUT_DIR/UDP/*.conllu` (Intermediate per-document CoNLL-U files).
* **Output:** `OUTPUT_DIR/NE/*/*.tsv` (NE annotated per-page files)

Run the following command to see how many documents have been processed into TSV files:

```bash
ls -l OUTPUT_DIR/NE | wc -l
```
which returns the total number of directories created (each subfolder corresponds to a document).

Example output directory [NE](data_samples%2FNE) 📁 contains per-page TSV files with NE annotations, where the NE tags 
follow the CNEC 2.0 standard [^3] which is used in the Czech Nametag model.


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
* **Input 3 (Optional):** `INPUT_ALTO_DIR/*.alto.xml` — Source ALTO XML files used during TEITOK conversion to provide spatial bounding box coordinates for each token.
* **Input 4 (Optional):** `INPUT_PAGES_DIR/<doc_id>-N.png` — Per-page facsimile images. When specified, the pipeline dynamically 
extracts pixel boundaries from the headers to compute scaling transformations (sx, sy). If omitted, coordinates are safely 
translated and aligned at a native 1.0 scale factor.
* **Output 1:** `OUTPUT_DIR/summary_ne_counts.csv` — Global table of aggregated Named Entity statistics across all documents.
* **Output 2:** `OUTPUT_DIR/UDP_NE/<doc_id>/<doc_id>.csv` — Per-document CSV tables with tokens, lemmas, and human-readable NE explanations.
* **Output 3 (Optional):** `OUTPUT_DIR/UDP_NE/<doc_id>/<doc_id>.conllu` — Final CoNLL-U files with NER tags enriched in the `MISC` column.
* **Output 4 (Optional):** `OUTPUT_DIR/TEITOK/<doc_id>.teitok.xml` — TEITOK-style TEI XML files ready for the **flexiconv** converter and facsimile viewing (see below).

The behavior of this step is controlled by boolean flags in your [config_api.txt](config_api.txt):

| Variable          | Description                                                                                                                                                                                                                                                | Default   |
|-------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-----------|
| `SAVE_CONLLU_NE`  | Keep the enriched CoNLL-U with NER in the `MISC` field.                                                                                                                                                                                                    | `true`    |
| `SAVE_CSV`        | Write the token-level summary CSV per document.                                                                                                                                                                                                            | `true`    |
| `SAVE_TEITOK`     | Write TEITOK-style TEI XML with bounding boxes and NER spans. When `INPUT_ALTO_DIR` is not set a warning is emitted and TEITOK XML is still produced without bboxes. If `INPUT_ALTO_DIR` is set but the path does not exist, the step exits with an error. | `true`    |
| `INPUT_PAGES_DIR` | Directory of per-page images (`<doc_id>-N.png`). When set, bbox coordinates are scaled to match the actual PNG resolution. Leave empty to write raw ALTO pixel values.                                                                                     | *(empty)* |


#### ALTO-to-TEITOK XML Generation and Coordinate Alignment

When `SAVE_TEITOK=true`, [teitok_alto.py](api_util/teitok_alto.py) 📎 reads and processes the internal spatial 
hierarchy of your ALTO source specifications.

**Offset Alignment (Resolving Layout Shifting):**

ABBYY FineReader naturally indexes element positions from the absolute physical boundary of the scanner bed `(0,0)`. 
However, companion web images cropped for public view or optimized to strip away raw scanner artifacts introduce 
a uniform positional drift (causing text layers to display too far left or too high up on screen).

To neutralize this error without modifying binary assets or re-cropping, the script automatically parses page-level 
`<PrintSpace>` properties from the ALTO structure:

```xml
<PrintSpace HEIGHT="3263" WIDTH="2027" VPOS="80" HPOS="297">
```

The horizontal boundary (`HPOS`) and vertical boundary (`VPOS`) values are captured as active translation variables 
(`dx`, `dy`). Prior to rendering bounding boxes into the TEITOK XML stream, these values are subtracted from the 
coordinate targets, recalculating alignment automatically:

$$\text{Scaled Coordinate} = \text{round}((\text{Absolute Coordinate} - \text{Offset}) \times \text{Scale Factor})$$

**Dynamic Scale Calculations:**

* **Companion Image Present (Tier 1):** If `INPUT_PAGES_DIR` is set and matching images exist, the tool safely reads 
binary file headers without invoking bloated third-party imaging dependencies. Ratios are resolved by evaluating layout 
sizes against image shapes (`sx = img_width / alto_width`).
* **User-set DPI (Tier 2):** If no image is available, scale is derived directly from the ALTO `<MeasurementUnit>` 
(`inch1200`, `mm10`, or `pixel`) mapped against the environment variables `IMAGE_DPI` and `ALTO_DPI`.
* **Native Processing (Fallback):** If no image and no DPI is provided, the tool calibrates positions through native 
PrintSpace logic but maintains a standard `1.0` scale factor.

> **Future direction:** Relative / resolution-independent coordinates are the preferred long-term direction 
> (pending TEITOK-team confirmation).

**Pre-demonstration coordinates fix of existing teitok xml files:**

The *post factum* fixer [fix_teitok_bboxes.py](fix_teitok_bboxes.py) 📎 is a standalone, retroactive maintenance 
utility designed to repair or fine-tune layout alignments in your TEITOK XML corpus without forcing a full re-run 
of the entire tokenization and NLP enrichment pipelines.

* **Instant Coordinate Correction:** It targets structural elements (`<tok>`, `<lb>`, and `<div>`) in already 
generated TEITOK XML files, parsing their whitespace-separated hOCR-style coordinates. It then applies programmatic 
shifting ($\Delta x, \Delta y$) and optional scale transformations ($\text{scale}_x, \text{scale}_y$).
* **Resolving Scan-Margin Shifts:** It directly eliminates the constant displacement error highlighted in the 
developer discussions—where elements appear shifted too far left or too high up on the visual canvas due to 
unprinted canvas margins or strict `PrintSpace` boundaries (`LeftMargin`/`TopMargin`).
* **TEITOK-Syntax Safe:** Standard XML parsers routinely choke or strip out custom structural hacks like TEITOK's 
`xmlnsoff="..."` attribute. This script uses a safe raw-string pre-swap block to ensure that TEITOK-specific formatting 
remains uncorrupted and compliant during parsing and rewriting.

 **When to Deploy It**

1. **Pre-Demonstration Quick Fixes:** If you change your visual image layers (e.g., swapping out full-page scans for 
tightly cropped variants) right before a live demonstration or data hand-off, you can instantly realign thousands 
of documents using a single shell command instead of re-processing billions of tokens through UDPipe and NameTag.
2. **Batch Calibration Testing:** It allows you to systematically trial different displacement values on a couple of 
sample documents in the TEITOK viewer to pinpoint the exact visual delta before committing hardcoded changes to 
your upstream generation scripts.

> [!NOTE]
> When a token's matched ALTO strings span more than one page (a rare OCR edge case near page
> boundaries), a warning is printed to stderr identifying the token and the conflicting page
> indices. The first matched page is used for the bbox assignment in that case.

The structural and spatial hierarchy from the ALTO file is strictly preserved in the generated TEITOK XML:
* **Tokens:** Matched coordinates are written to each `<tok>` element as `@bbox="x1 y1 x2 y2"` (absolute 
pixel coordinates in TEITOK's hOCR-derived format). Each token also carries `@type="w"` (word) or 
`@type="pc"` (punctuation character) derived from UDPipe's UPOS tag.
* **Lines:** ALTO `<TextLine>` elements are preserved via `<lb>` (line break) tags, which also include 
their own `@bbox` spatial coordinates.
* **Blocks:** Text blocks are encapsulated within `<div type="MarginTextZone-P">` containers, satisfying 
the core ATRIUM guidelines for classified text zones.
* **Graphics:** Non-text elements like `Illustration` and `GraphicalElement` blocks are parsed and 
appended to their respective pages as `<figure>` tags with strict bounding boxes.
* **Pages:** Page boundaries are marked with `<pb n="N" id="..." facs="..."/>` elements pointing to 
the specific document surface.

Named entity spans are wrapped in `<n>` elements grouping their constituent `<tok>` nodes. 
Two attributes encode the entity type at different levels of granularity: `@type` holds the CoNLL-style 
category (`PER`, `ORG`, `LOC`, or `MISC`) intended for querying and interoperability, while `@cnec` carries
the raw CNEC 2.0 code (e.g., `pf`, `gu`, `if`) for use in visualisation. For example, a span tagged as a 
first name is written as `<name type="PER" cnec="pf">`. 

> [!NOTE]
> Thanks to the sequence matching approach, the script achieves near-perfect spatial alignment between 
> NLP tokens and OCR coordinates, drastically improving upon older greedy matching methods that would 
> break on minor character variations. Alignment statistics (matched vs. total tokens) are printed to
> the console per document.


<details>
<summary> Commands to check progress of the script </summary>
  Run the following command to see how many documents have been processed into CSV files:

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

</details>

Example summary table: [summary_ne_counts.csv](data_samples/summary_ne_counts_SHORT.csv) 📎.

Example output directory [UDP_NE](data_samples%2FUDP_NE) 📁 contains per-document CSV 
tables with NE tags and UDPipe feature columns, plus CoNLL-U files with NE annotations in 
per-document manner.

Example output directory [TEITOK](data_samples%2FTEITOK) 📁 contains per-document TEITOK 
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
│   ├── <doc_id>.conllu
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

The combined output [summary_ne_counts.csv](data_samples/summary_ne_counts_SHORT.csv) 📎 contains aggregated Named Entity 
statistics across all processed pages.

> [!NOTE]
> Now you can delete `UDP/` from `<OUTPUT_DIR>/` if you no longer need the raw CoNLL-U files.
> The final CoNLL-U files with NER features are in `<OUTPUT_DIR>/UDP_NE/`.

If you do not plan to rerun any part of the pipeline, you can also delete 
the entire `TEMP/` directory including [manifest.tsv](data_samples/manifest_SHORT.tsv) 📎.

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


<details>

<summary> Command Line usage (with examples) 👀</summary>

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

</details>

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
[kw_summary_l.csv](data_samples/kw_summary_l.csv) 📎.

<details>
<summary>KER (Legacy) scores interpretation 👀</summary>

| Score range | Interpretation                                           |
|-------------|----------------------------------------------------------|
| 1–5         | Common functional nouns, low informativeness             |
| 5–20        | Topic-representative vocabulary                          |
| > 20        | Dominant terms, likely named entities or domain headings |

</details>

**`yake`** — normalised inverse YAKE score, [0, 1] per document. Examples in directory: [KW_PER_DOC_Y](data_samples/KW_PER_DOC_Y) 📂 and summary file
[kw_summary_y.csv](data_samples/kw_summary_y.csv) 📎.

<details>
<summary>YAKE) scores interpretation 👀</summary>

| Score range | Semantic category | Interpretation                               |
|-------------|-------------------|----------------------------------------------|
| 0.0–0.2     | Noise floor       | Common words, low local relevance            |
| 0.2–0.6     | Context layer     | General vocabulary defining the broad topic  |
| 0.6–0.9     | Topic layer       | Specific nouns and verbs central to the text |
| 0.9–1.0     | Entity layer      | Rare terms, neologisms, named entities       |

</details>

**`keybert`** — cosine similarity to document centroid, [0, 1]. Examples in directory: [KW_PER_DOC_KB](data_samples/KW_PER_DOC_KB) 📂 and summary file
[kw_summary_kb.csv](data_samples/kw_summary_kb.csv) 📎.

<details>
<summary>KeyBERT scores interpretation 👀</summary>

| Score range | Interpretation                   |
|-------------|----------------------------------|
| < 0.3       | Weakly related phrases           |
| 0.3–0.6     | Contextually relevant terms      |
| > 0.6       | Highly representative keyphrases |

</details>

---

## EXTRA: Converting Other Input Formats with flexiconv

> [!NOTE]
> This section is relevant when your documents originate from an OCR or digitisation
> pipeline that does **not** produce ALTO XML — for example, PAGE XML, hOCR, plain-text
> exports, or proprietary formats. If you already have ALTO XML, the pipeline generates
> TEITOK XML natively via `api_4_stats.sh` (see above).

### What is flexiconv?

[**flexiconv**](https://github.com/ufal/flexiconv) [^9] is a flexible format-conversion tool
developed at UFAL that translates a variety of OCR and document layout formats into **TEITOK
XML** — the unified output format used by this project. It acts as a universal adapter: once
your documents are in TEITOK XML, they can be ingested directly into the TEITOK corpus
platform and will benefit from all the same search, visualisation, and NER capabilities
described above.

```
  Your input format           flexiconv             Unified output
  ─────────────────    ─────────────────────────   ─────────────────
  PAGE XML          ─┐
  hOCR              ─┤──► flexiconv ──────────────► .teitok.xml ──► TEITOK platform
  plain text + CSV  ─┤                                            ──► this pipeline
  other OCR output  ─┘                                               (NER, KWs, ...)
```

### When to use flexiconv

Use flexiconv **before** running this pipeline when:

- Your collection was OCR-processed with a tool that outputs **PAGE XML** (e.g. Transkribus, OCRopus, kraken).
- Your layout data is in **hOCR** format (used by Tesseract and some ABBYY exports).
- You have structured text with positional metadata but no standard bounding-box format.
- You received digitised material from a partner institution using a format not natively supported
  by `teitok_alto.py`.

### How to use flexiconv

1. **Clone and install** the tool:

    ```bash
    git clone https://github.com/ufal/flexiconv.git
    cd flexiconv
    pip install -r requirements.txt
    ```

2. **Run the conversion** on your input files:

    ```bash
    python flexiconv.py \
        --input-dir  /path/to/your/source/documents \
        --input-fmt  page-xml \          # or: hocr, plain, ...
        --output-dir /path/to/teitok_out \
        --output-fmt teitok
    ```

    Refer to the [flexiconv documentation](https://github.com/ufal/flexiconv) for the full list
    of supported `--input-fmt` values and format-specific options.

3. **Continue with this pipeline** using the converted TEITOK XML files. At this point your
   documents already have layout structure and bounding boxes embedded — the NLP enrichment
   steps (UDPipe morphology, NameTag NER, keyword extraction) can be applied on top via the
   scripts in this repository.

> [!TIP]
> If your format is not yet supported by flexiconv, please open an issue on the
> [flexiconv GitHub repository](https://github.com/ufal/flexiconv). The tool is
> actively developed within the ATRIUM project and new format adapters are added
> regularly.

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

INPUT_DIR=data_samples/DOC_LINE_LANG_CLASS
OUTPUT_DIR=data_samples/KW_PER_DOC_LLM
VOCAB_PATH=data_samples/teater_nested_vocab.json
PARADATA_DIR=paradata

INCLUDE_NON_TEXT=true
MIN_CHAR_COUNT=3
MIN_CHAR_NON_TEXT=8
MIN_ALPHA_RATIO_NON_TEXT=0.4

# ── Backend ───────────────────────────────────────────────────────────────────
# transformers  HuggingFace Transformers + BnB 4-bit + lmformatenforcer.
#               Best for single-GPU runs on models ≤ 31 B.
# vllm          vLLM + xgrammar guided decoding + Automatic Prefix Caching.
#               Required for models ≥ 70 B or any multi-GPU node.
BACKEND=transformers

# ── vLLM-specific (ignored when BACKEND=transformers) ─────────────────────────
TENSOR_PARALLEL_SIZE=1        # Number of GPUs to shard the model across
GPU_MEMORY_UTILIZATION=0.90   # Fraction of each GPU's VRAM for the KV cache
GUIDED_DECODING_BACKEND=xgrammar
ENABLE_PREFIX_CACHING=true    # Automatic Prefix Caching — highly recommended
VLLM_BATCH_SIZE=16            # Lines per generate() call; increase on ≥ 160 GB nodes
# MAX_MODEL_LEN=65536          # Optional: cap context to reduce KV-cache pressure
```

### 🗂 Workflow

**1. Vocabulary Harvesting ([vocab_manager.py](vocab_manager.py) 📎)**

Before running inference, build the allowable vocabulary list. The vocabulary manager
queries the AMCR OAI-PMH endpoint via paginated HTTP GET requests to fetch Czech–English
term pairs, groups them into a thematic taxonomy guided by
[taxonomy_config.json](data_samples/taxonomy_config.json) 📎, and caches the result
locally as `teater_nested_vocab.json`. The cache is written with `sort_keys=True` for
deterministic diffs, and the serialised prompt string is memoised so repeated reads
within one pipeline run are free.

```bash
python3 vocab_manager.py
```

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

* **Input:** `DOC_LINE_LANG_CLASS/*.csv` (contains `file_id`, `page_num`, `line_num`,
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
    "confidence_score": 0.95
  }
}
```

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

Output examples per model:
- [KW_PER_DOC_LLM_qwen3_14b](data_samples/KW_PER_DOC_LLM_qwen3_14b) 📂 by Qwen 3-14B [^18]
- [KW_PER_DOC_LLM_qwen2.5-14b-awq](data_samples/KW_PER_DOC_LLM_qwen25_14b_awq) 📂 by Qwen 2.5-14B AWQ [^12]
- [KW_PER_DOC_LLM_gemma_3_12b_it](data_samples/KW_PER_DOC_LLM_gemma_3_12b_it) 📂 by Gemma 3-12B-IT [^20]
- [KW_PER_DOC_LLM_qwen_3.6_27b_it](data_samples/KW_PER_DOC_LLM_qwen_36_27b_it) 📂 by Qwen 3.6-27B-IT [^24]
- [KW_PER_DOC_LLM_gemma_4_31b_it](data_samples/KW_PER_DOC_LLM_gemma_4_31b_it) 📂 by Gemma 4-31B-IT [^22]
- [KW_PER_DOC_LLM_qwen_3.5_9b_it](data_samples/KW_PER_DOC_LLM_qwen_35_9b_it) 📂 by Qwen 3.5-9B-IT [^26]
- [KW_PER_DOC_LLM_llama31_70b](data_samples/KW_PER_DOC_LLM_llama31_70b) 📂 by LLaMA 3.1-70B [^30]
- [KW_PER_DOC_LLM_qwen3_8b](data_samples/KW_PER_DOC_LLM_qwen3_8b) 📂 by Qwen 3-8B [^19]

Pending (sample runs in progress):
- [KW_PER_DOC_LLM_qwen_3.6_35b_moe](data_samples/KW_PER_DOC_LLM_qwen_36_35b_moe) 📂 by Qwen 3.6-35B-MoE [^23]
- [KW_PER_DOC_LLM_gemma_4_26b_a4b_it](data_samples/KW_PER_DOC_LLM_gemma_4_26b_a4b_it) 📂 by Gemma 4-26B-A4B-IT [^25]

Archived (unsuccessful — evaluation notes in issue #6):
- [KW_PER_DOC_LLM_mistral-nemo-12b](data_samples/archived_KW_PER_DOC_LLM/KW_PER_DOC_LLM_mistral_nemo_12b) 📂 by Mistral Nemo 12B [^14]
- [KW_PER_DOC_LLM_aya_expanse_8b](data_samples/archived_KW_PER_DOC_LLM/KW_PER_DOC_LLM_aya_expanse_8b) 📂 by Aya Expanse 8B [^15]
- [KW_PER_DOC_LLM_bielik_11b_v3.0](data_samples/archived_KW_PER_DOC_LLM/KW_PER_DOC_LLM_bielik_11b_v30) 📂 by Bielik 11B v3.0 [^16]
- [KW_PER_DOC_LLM_llama31_8b](data_samples/archived_KW_PER_DOC_LLM/KW_PER_DOC_LLM_llama31_8b) 📂 by LLaMA 3.1-8B [^17]
- [KW_PER_DOC_LLM_ministral_3_14b](data_samples/archived_KW_PER_DOC_LLM/KW_PER_DOC_LLM_ministral_3_14b) 📂 by Ministral 3-14B [^21]
- [KW_PER_DOC_LLM_qwen3_8b (early run)](data_samples/archived_KW_PER_DOC_LLM/KW_PER_DOC_LLM_qwen3_8b) 📂 by Qwen 3-8B [^19]
- [KW_PER_DOC_LLM_qwen2.5-7b](data_samples/archived_KW_PER_DOC_LLM/KW_PER_DOC_LLM_qwen25_7b) 📂 by Qwen 2.5-7B [^13]

### 📊 Paradata Integration

Just like the main shell-script pipelines, LLM enrichment natively hooks into
`atrium_paradata.py` and automatically logs:

* Full snapshot of [llm_config.txt](llm_config.txt) 📎 and quality-filter settings.
* Total processed lines (`json` success events).
* Per-line tracking of filter skips (`skipped_filter`), inference faults
  (`skipped_error`), and already-completed files (`already_exists`).
* **Abort events** — when a document is abandoned after 10 consecutive inference errors,
  the paradata entry records the abort reason alongside the count of lines processed
  before the failure. A sidecar `*.abort.json` file is also written next to the
  (partial) output JSON for easy programmatic detection.
The resulting logs are dropped into the specified `PARADATA_DIR` alongside the other pipeline execution records.

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

The paradata logs (samples in directory [paradata](paradata) 📂) capture key details about each pipeline stage, 
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

[api_common.sh](api_util%2Fapi_common.sh) 📎 exposes a `log()` helper that timestamps and
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

#### Provenance for containers

When the runner (or its Docker entrypoint) is started with the
`ATRIUM_RUNNER_IMAGE`, `ATRIUM_RUNNER_REPO`, and `ATRIUM_RUNNER_REF` environment
variables set, those values are forwarded to every stage subprocess and end up
in each stage's paradata record (and therefore the merged record). This ties a
run back to the exact image/commit that produced it.

```bash
ATRIUM_RUNNER_IMAGE="ghcr.io/ufal/atrium-nlp-enrich:v0.11.0" \
ATRIUM_RUNNER_REF="$(git rev-parse --short HEAD)" \
python3 run_pipeline.py --kw
```

#### Exit codes

| Code | Meaning                                                                                                                                                                             |
|------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `0`  | All requested stages completed; nothing flagged.                                                                                                                                    |
| `1`  | A stage processed **nothing** despite having input and no resume, and `FAIL_ON_EMPTY=true` (the default).                                                                           |
| `2`  | A required stage script was not found.                                                                                                                                              |
| `3`  | A dependency preflight failed (e.g. `--kw-method keybert` without `keybert`/`sentence-transformers`, or `--llm` without the [requirements_llm.txt](requirements_llm.txt) 📎 stack). |
| `≠0` | A stage script itself exited non-zero (its code is propagated).                                                                                                                     |

> [!TIP]
> **Using `--force` (`-f`)** overrides exit codes `1`, `3`, and `≠0`. It bypasses preflight dependency crashes and forces `FAIL_ON_EMPTY=False`, allowing the pipeline to continue attempting subsequent stages even if one stage crashes or processes zero files.

The empty-run guard is governed by `FAIL_ON_EMPTY` in [config_api.txt](config_api.txt) 📎. A
**resumed** run — where every document was already complete and thus *skipped* —
is treated as success, not an empty failure. Set `FAIL_ON_EMPTY=false` to permit
genuinely empty stages.

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