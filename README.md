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

- [ ⚙️ Setup](#-setup)
- [Workflow Stages](#workflow-stages)
  - [Step 1: Prepare CSVs with texts from Page-Specific ALTOs](#-step-1-prepare-csvs-with-texts-from-page-specific-altos)
  - [Step 2: Extract NER and CONLL-U](#-step-2-extract-ner-and-conll-u)
    - [Configuration ⚙️](#configuration-)
    - [Execution Pipeline](#execution-pipeline)
      - [Generate Manifest](#1-generate-manifest)
      - [UDPipe Processing (Morphology & Syntax)](#2-udpipe-processing-morphology--syntax)
      - [NameTag Processing (NER tags)](#3-nametag-processing-ner-tags)
      - [Generate Statistics](#4-generate-statistics)
- [Output Structure](#output-structure)
- [EXTRA: Extract Keywords (KER)](#extra-extract-keywords-ker-based-on-tf-idf)
- [Acknowledgements](#acknowledgements-)

## ⚙️ Setup

Before you begin, set up your environment.

1.  Create and activate a new virtual environment in the project directory 🖥.
2.  Install the required Python packages:
    ```bash
    pip install -r requirements.txt
    ```
3. Review and update the [config_api.env](config_api.env) 📎 file with your specific paths and API configurations.
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
# Example settings in config_api.env
OUTPUT_DIR="../../ARUB"         # Destination for results
INPUT_TABLES_DIR="$OUTPUT_DIR/DOC_LINE_LR_CLS"  # Directory containing input tables (from Step 1)
ALTO_DIR="$OUTPUT_DIR/altos"    # Source of ALTO XML files (from Step 1) - for TEITOK conversion
WORK_DIR="./TEMP"               # Working directory for intermediate files

LOG_FILE="$OUTPUT_DIR/processing.log"

CONLLU_INPUT_DIR="$OUTPUT_DIR/UDP"
TSV_INPUT_DIR="$OUTPUT_DIR/NE"
SUMMARY_OUTPUT_DIR="$OUTPUT_DIR/NE_UDP"

MODEL_UDPIPE="czech-pdt-ud-2.15-241121"
MODEL_NAMETAG="nametag3-czech-cnec2.0-240830"

WORD_CHUNK_LIMIT=900           # Word limit per API call
TIMEOUT=60                     # API call timeout in seconds
MAX_RETRIES=5                  # Number of retries for failed API calls

SAVE_CONLLU_NE=true   # keep merged CoNLL-U with NER in MISC
SAVE_CSV=true         # write token-level summary CSV
SAVE_TEITOK=true      # write TEITOK-style TEI XML (flexiconv-compatible)
```

#### Execution Pipeline

Run the following scripts in sequence. Each script utilizes [api_common.sh](api_util/api_common.sh) 📎 for logging, 
retry logic, and error handling for API calls. Additionally, [api_util/](api_util/) 📁 contains 
helper Python scripts for chunking and analysis.

##### 1. Generate Manifest

Maps input text files to document IDs and page numbers to ensure correct processing order.

```bash
./api_1_manifest.sh
```

* **Input:** `../CSVS_with_TEXT/` (raw text files in subdirectories from Step 1).
* **Output:** `OUTPUT_DIR/manifest.tsv`.

Example output file [manifest.tsv](data_samples/manifest_SHORT.tsv) 📎 with **file**, **page**
number, and **path** columns. It lists all text files to be processed in the next steps.
Run the following command to see how many pages will be processed:

```bash
wc -l OUTPUT_DIR/manifest.tsv
```
which returns the total number of lines (pages) in the manifest (including the header line).

##### 2. UDPipe Processing (Morphology & Syntax)

Sends text to the UDPipe API [^5]. Large pages are automatically split into chunks (default 900 words) using 
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

Example output directory [NE](data_samples%2FNE) 📁 contains per-page TSV files with NE annotations, where the NE tags follow the CNEC 2.0 standard [^3] which is used in the Czech Nametag model.


##### 4. Generate Statistics

This stage consolidates the linguistic data from UDPipe (CoNLL-U) and the NER data from 
NameTag (TSV) into final per-document formats. It also generates a master summary of 
entity counts across the entire collection and can optionally produce TEITOK-compatible 
XML files that merge linguistic tokens with original ALTO layout coordinates.

The process utilizes [summarize_nt_udp.py](api_util/summarize_nt_udp.py) 📎 to merge these 
layers and [analyze.py](api_util/analyze.py) 📎 to map complex CNEC 2.0 tags 
(e.g., `g`, `pf`, `if`) into human-readable categories (e.g., "Geographical name", 
"First name", "Company/Firm"). Optionally, TEITOK-related functionality is implemented in
[teitok_alto.py](api_util/teitok_alto.py) 📎.

```bash
./api_4_stats.sh
```

#### Inputs and Outputs

* **Input 1:** `OUTPUT_DIR/UDP/*.conllu` — Per-document CoNLL-U files containing morphology and syntax.
* **Input 2:** `OUTPUT_DIR/NE/*/*.tsv` — Per-page TSV files containing Named Entity annotations.
* **Input 3 (Optional):** `ALTO_DIR/*.alto.xml` — Source ALTO XML files used during TEITOK conversion to provide spatial bounding box coordinates for each token.

* **Output 1:** `OUTPUT_DIR/summary_ne_counts.csv` — Global table of aggregated Named Entity statistics across all documents.
* **Output 2:** `OUTPUT_DIR/UDP_NE/<doc_id>/<doc_id>.csv` — Per-document CSV tables with tokens, lemmas, and human-readable NE explanations.
* **Output 3 (Optional):** `OUTPUT_DIR/UDP_NE/<doc_id>/<doc_id>.conllu` — Final CoNLL-U files with NER tags enriched in the `MISC` column.
* **Output 4 (Optional):** `OUTPUT_DIR/TEITOK/<doc_id>.teitok.xml` — TEITOK-style TEI XML files ready for the **flexiconv** converter and facsimile viewing (see below).

The behavior of this step is controlled by boolean flags in your [config_api.txt](config_api.txt):

| Variable         | Description                                                                   | Default |
|------------------|-------------------------------------------------------------------------------|---------|
| `SAVE_CONLLU_NE` | Keep the enriched CoNLL-U with NER in the `MISC` field.                       | `true`  |
| `SAVE_CSV`       | Write the token-level summary CSV per document.                               | `true`  |
| `SAVE_TEITOK`    | Write TEITOK-style TEI XML with bounding boxes and NER spans (requires ALTO). | `true`  |


#### ALTO-to-TEITOK XML Generation and Coordinate Alignment

When `SAVE_TEITOK=true`, the script  ([teitok_alto.py](api_util/teitok_alto.py) 📎) 
generates standard-compliant TEITOK XML by aligning UDPipe tokens to spatial bounding 
boxes from the corresponding ALTO XML file. 

This alignment is powered by an optimal sequence matching algorithm 
(`difflib.SequenceMatcher`). By flattening all ALTO `String` elements into a single 
NFC-normalized character sequence and mapping the token forms against it, the aligner 
seamlessly bridges complex OCR and tokeniser mismatches (such as arbitrary word splits, 
differing forms, or missing characters). This robust approach ensures virtually 100% 
of available ALTO bounding boxes are successfully transferred to the output tokens.

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

Named entity spans are wrapped in `<name>` elements grouping their constituent `<tok>` nodes. 
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


### EXTRA: Extract Keywords (KER) based on tf-idf

> [!NOTE]
> This is an optional step in NLP enrichment of your data since it can give an overview of the 
> whole collection in a relatively short time. Moreover, this process requires lemmas 
> of words (output of Step 2) to get informative results.

Finally, you can extract keywords 🔎 from your text. This script runs on a directory of
document-specific `.conllu` files (e.g., `OUTPUT_DIR/UDP/`) containing ordered text content with word lemmas..

    python3 keywords.py -i <input_dir> -l <lang> -w <integer> -n <integer> -d <output_dir> -o <output_file>.csv

where short flag meanings are (listed in the same order as used above):

-  `--input_dir`: Input directory (e.g., CoNLL-U files from Step 4.2).
-  `--lang`: Language for KER (`cs` for Czech or `en` for English).
-  `--max-words`: Number words per keyword entry.
-  `--num_keywords`: Number of keywords to extract.
-  `--per_doc_out_dir`: Output directory for per-document CSV files (default: `KW_PER_DOC`).
-  `--output_file`: Output CSV file for the master keywords table (default: `keywords_summary.csv`).

> [!WARNING]
> Make sure KER data (tf-idf table per language) is stored in [ker_data](ker_data) 📁 before running this script.

* **Input:** `OUTPUT_DIR/UDP/` (directory with document-specific CoNLL-U files from Step 4.2)
* **Output 1:** `keywords_summary.csv` (summary table with keywords per document)
* **Output 2:** `KW_PER_DOC/` (directory with per-document CSV files

This process creates `.csv` table with the columns like `file`, and pairs of `kw-<N>` (N-th keyword)) 
and `score-<N>` (N-th keyword's score). An example of the summary is available in [keywords_summary.csv](data_samples/keywords_summary_SHORT.csv) 📎.

Example of per-document CSV file with keywords: [KW_PER_DOC](data_samples/KW_PER_DOC) 📁.

```
KW_PER_DOC/
├── <docname1>.csv 
├── <docname2>.csv
└── ...
```

Where each file contains **keyword** plus its **score** in two columns sorted by the score in **descending order**.

| Score Range | Semantic Category     | Mathematical Driver | Interpretation                                |
|-------------|-----------------------|---------------------|-----------------------------------------------|
| 0.0         | The **Void**          | IDF ≈ 0             | Stopwords or ubiquitous terms.                |
| 0.0-0.2     | The **Noise** Floor   | Low TF × Low IDF    | Common words with low local relevance.        |
| 0.2-1.0     | The **Context** Layer | Mod. TF × Low IDF   | General vocabulary defining the broad topic.  |
| 1.0-5.0     | The **Topic** Layer   | High TF × Mod. IDF  | Specific nouns and verbs central to the text. |
| > 5.0       | The **Entity** Layer  | High TF × High IDF  | Rare terms, Neologisms, Named Entities.       |

The table above specifies how to interpret keyword scores returned by the KER algorithm based on their 
TF-IDF values computed inside the system.

> [!NOTE]
> This step was considered unnecessary for the ATRIUM project

---

## Acknowledgements 🙏

**For support write to:** lutsai.k@gmail.com responsible for this GitHub repository [^8] 🔗

- **Developed by** UFAL [^7] 👥
- **Funded by** ATRIUM [^4]  💰
- **Shared by** ATRIUM [^4] & UFAL [^7] 🔗
- **Frameworks used**: 
  - Lindat/CLARIAH-CZ **NameTag 3** API [^6] 🏷
  - Lindat/CLARIAH-CZ **UDPipe 2** API [^5] 🏷
  - local **KER** (Keyword Extraction and Ranking) [^1] 🏷

**©️ 2026 UFAL & ATRIUM**

[^1]: https://github.com/ufal/ker
[^2]: https://github.com/ufal/atrium-alto-postprocess
[^3]: https://ufal.mff.cuni.cz/~strakova/cnec2.0/ne-type-hierarchy.pdf
[^4]: https://atrium-research.eu/
[^5]: https://lindat.mff.cuni.cz/services/udpipe/api-reference.php
[^6]: https://lindat.mff.cuni.cz/services/nametag/api-reference.php
[^8]: https://github.com/ufal/atrium-nlp-enrich
[^7]: https://ufal.mff.cuni.cz/home-page
