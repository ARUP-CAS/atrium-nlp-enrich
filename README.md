# 📦 ALTO XML Files Postprocessing Pipeline - NLP Enrichment

This project provides a workflow for processing ALTO XML files with NLP services. It takes raw ALTO 
XMLs and transforms them into structured statistics tables and extracts high-level linguistic features like 
Named Entities (NER) with tags and CONLL-U files with lemmas & part-of-sentence tags, and extraction of 
keywords (KER) per document.

---

## ⚙️ Setup

Before you begin, set up your environment.

1.  Create and activate a new virtual environment in the project directory 🖥.
2.  Install the required Python packages:
    ```bash
    pip install -r requirements.txt
    ```
3.  Clone and install `alto-tools` 🔧, which is used for statistics and text extraction:
    ```bash
    git clone https://github.com/cneud/alto-tools.git
    cd alto-tools
    pip install .
    cd .. 
    ```
You are now ready to start the workflow.

---

## Workflow Stages

The process is divided into sequential steps, starting from raw ALTO files and ending 
with extracted linguistic and statistic data.

### ▶ Step 1: Prepare text files from Page-Specific ALTOs

> [!IMPORTANT]
> If you already have a directory of extracted text files from ALTO XMLs, 
> you can skip Step 1 and proceed directly to Step 2.

First, ensure you have a directory 📁 containing your page-level `<file>.alto.xml` files: 
```
PAGE_ALTO/
├── <file1>
│   ├── <file1>-<page>.alto.xml 
│   └── ...
├── <file2>
│   ├── <file2>-<page>.alto.xml 
│   └── ...
└── ...
```
Each page-specific file retains the header from its original source document. Then run:

    python3 api_0_extract_TXT.py

The script uses the directory as input to generate a foundational CSV 
statistics file, capturing metadata (counts of XML elements like texts, graphics, etc) 
for each page (e.g. `alto_statistics.csv`):

    file, page, textlines, illustrations, graphics, strings, path
    CTX200205348, 1, 33, 1, 10, 163, /lnet/.../A-PAGE/CTX200205348/CTX200205348-1.alto.xml
    CTX200205348, 2, 0, 1, 12, 0, /lnet/.../A-PAGE/CTX200205348/CTX200205348-2.alto.xml
    ...


The next part of the script runs in parallel (using multiple **CPU** cores) to extract text from 
ALTO XMLs into `.txt` files. It reads the CSV with stats and process paths into output text files. 
The extraction is powered by the **alto-tools** framework [^1].


* **Input:** `../PAGE_ALTO/` (directory containing per-page ALTO XML files)
* **Output 1:** `alto_statistics.csv` (table of page-level statistics and ALTO file paths)
* **Output 2:** `../PAGE_TXT/` (directory containing per-page raw text files)

```
PAGE_TXT/
├── <file1>
│   ├── <file1>-<page>.txt 
│   └── ...
├── <file2>
│   ├── <file2>-<page>.txt 
│   └── ...
└── ...
```

> [!TIP]
> More about this step ypu can find in [GitHub repository](https://github.com/K4TEL/atrium-alto-postprocess.git) of ATRIUM project dedicated to ALTO XML
> processing into TXT and collection of statistics and keywords from these files [^2]. 

### ▶ Step 2: Extract NER and CONLL-U

This stage performs advanced NLP analysis using external APIs (Lindat/CLARIAH-CZ) 
to generate Universal Dependencies (CoNLL-U) and Named Entity Recognition (NER) data.

Unlike previous steps, this process is split into modular shell scripts to handle large-scale 
processing, text chunking, and API rate limiting.

#### Configuration ⚙️

Before running the pipeline, review the [api_config.env](config_api.env) 📎 file. This file controls 
directory paths, API endpoints, and model selection.

```bash
# Example settings in config_api.env
INPUT_DIR="../PAGE_TXT"        # Source of text files (from Step 3.1)
OUTPUT_DIR="../OUT_API"        # Destination for results
WORK_DIR="./TEMP"              # Working directory for intermediate files

LOG_FILE="$OUTPUT_DIR/processing.log"

CONLLU_INPUT_DIR="./TEMP/UDPIPE"
TSV_INPUT_DIR="../../OUT_API/NE"
SUMMARY_OUTPUT_DIR="../../OUT_API/NE_UDP"

MODEL_UDPIPE="czech-pdt-ud-2.15-241121"
MODEL_NAMETAG="nametag3-czech-cnec2.0-240830"

WORD_CHUNK_LIMIT=900           # Word limit per API call
TIMEOUT=60                     # API call timeout in seconds
MAX_RETRIES=5                  # Number of retries for failed API calls
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

* **Input:** `../PAGE_TXT/` (raw text files in subdirectories from Step 1).
* **Output:** `TEMP/manifest.tsv`.

Example output file [manifest.tsv](data_samples/manifest.tsv) 📎 with **file**, **page**
number, and **path** columns. It lists all text files to be processed in the next steps.
Run the following command to see how many pages will be processed:

```bash
wc -l TEMP/manifest.tsv
```
which returns the total number of lines (pages) in the manifest (including the header line).

##### 2. UDPipe Processing (Morphology & Syntax)

Sends text to the UDPipe API [^5]. Large pages are automatically split into chunks (default 900 words) using 
[chunk.py](api_util/chunk.py) 📎 to respect API limits, then merged back into valid CoNLL-U files.

```bash
./api_2_udp.sh
```

* **Input 1:** `TEMP/manifest.tsv` (mapping of text files to document IDs and page numbers).
* **Input 2:** `../PAGE_TXT/` (raw text files in subdirectories from Step 1).
* **Output:** `TEMP/UDPIPE/*.conllu` (Intermediate per-document CoNLL-U files).

Run the following command to see how many documents have been processed into CoNLL-U files:

```bash
ls -l TEMP/UDPIPE/ | wc -l
```
which returns the total number of CoNLL-U files created (each file corresponds to a document).


Example output directory [UDPIPE](data_samples%2FUDPIPE) 📁 contains per-document CoNLL-U files.

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

* **Input 1:** `TEMP/manifest.tsv` (mapping of text files to document IDs and page numbers).
* **Input 2:** `TEMP/UDPIPE/*.conllu` (Intermediate per-document CoNLL-U files).
* **Output:** `OUTPUT_DIR/NE/*/*.tsv` (NE annotated per-page files)

Run the following command to see how many documents have been processed into TSV files:

```bash
ls -l OUTPUT_DIR/NE | wc -l
```
which returns the total number of directories created (each subfolder corresponds to a document).

Example output directory [NE](data_samples%2FNE) 📁 contains per-page TSV files with NE annotations, where the NE tags follow the CNEC 2.0 standard [^3] which is used in the Czech Nametag model.

##### 4. Generate Statistics

Aggregates the entity counts from the final CoNLL-U files into a summary CSV. It utilizes 
[analyze.py](api_util/analyze.py) 📎 to map complex CNEC 2.0 tags (e.g., `g`, `pf`, `if`) 
into human-readable categories (e.g., "Geographical name", "First name", "Company/Firm").

```bash
./api_4_stats.sh
```

* **Input 1:** `OUTPUT_DIR/NE/*/*.tsv` (NE annotated per-page files).
* **Input 2:** `TEMP/UDPIPE/*.conllu` (Intermediate per-document CoNLL-U files).
* **Output 1:** `OUTPUT_DIR/summary_ne_counts.csv`.
* **Output 2:** `OUTPUT_DIR/UDP_NE/*/*.csv` (per-page CSV files with NE and UDPipe features).

Run the following command to see how many documents have been processed into CSV files:

```bash
ls -l OUTPUT_DIR/UDP_NE | wc -l
```
which returns the total number of directories created (each subfolder corresponds to a document).


Example summary table: [summary_ne_counts.csv](data_samples/summary_ne_counts.csv) 📎.

Example output directory [UDP_NE](data_samples%2FUDP_NE) 📁 contains per-page CSV tables with NE tag and columns for UDPipe features.

#### Output Structure

After completing the pipeline, your working and output directories will be organized as follows:
```
TEMP/
├── UDPIPE/  
│   ├── <doc_id>.conllu
│   ├── <doc_id>.conllu
│   └── ...
├── CHUNKS/
│   └── ...
├── nametag_response_docname1.conllu.json
├── ...
└── manifest.tsv
```
AND
```
<OUTPUT_DIR>
├── UDP_NE/          
│   ├── <doc_id>     
│   │   ├── <doc_id>-<page_num>.csv     
│   │   └── ...     
│   ├── <doc_id>     
│   │   ├── <doc_id>-<page_num>.csv     
│   │   └── ...
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
└── summary_ne_counts.csv  
```

The combined output [summary_ne_counts.csv](data_samples/summary_ne_counts.csv) 📎 contains aggregated Named Entity 
statistics across all processed pages.

> [!NOTE]
> Now you can delete `UDPIPE/` from `TEMP/` if you no longer need the raw CoNLL-U files.
> The final per-page CSV files with UDPipe features are in `<OUTPUT_DIR>/UDP_NE/`.

If you do not plan to rerun any part of the pipeline, you can also delete 
the entire `TEMP/` directory including [manifest.tsv](data_samples/manifest.tsv) 📎.


### EXTRA: Extract Keywords (KER) based on tf-idf

Finally, you can extract keywords 🔎 from your text. This script runs on a directory of subdirectories with
page-specific files `.txt` (e.g., `../PAGE_TXT/`).

    python3 keywords.py -i <input_dir> -l <lang> -w <integer> -n <integer> -d <output_dir> -o <output_file>.csv

where short flag meanings are (listed in the same order as used above):

-   `--input_dir`: Input directory (e.g., text files from Step 3).
-   `--lang`: Language for KER (`cs` for Czech or `en` for English).
-   `--max-words`: Number words per keyword entry.
-   `--num_keywords`: Number of keywords to extract.
-  `--per_doc_out_dir`: Output directory for per-document CSV files (default: `KW_PER_DOC`).
-  `--output_file`: Output CSV file for the master keywords table (default: `keywords_master.csv`).

> [!WARNING]
> Make sure KER data (tf-idf table per language) is stored in [ker_data](ker_data) 📁 before running this script.

* **Input:** `../PAGE_TXT/` (directory with page-specific text files from Step 3)
* **Output 1:** `keywords_master.csv` (summary table with keywords per document)
* **Output 2:** `KW_PER_DOC/` (directory with per-document CSV files

This process creates `.csv` table with the columns like `file`, and pairs of `kw-<N>` (N-th keyword)) 
and `score-<N>` (N-th keyword's score). An example of the summary is available in [keywords_master.csv](data_samples/keywords_master.csv) 📎.

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


---

## Acknowledgements 🙏

**For support write to:** lutsai.k@gmail.com responsible for this GitHub repository [^8] 🔗

- **Developed by** UFAL [^7] 👥
- **Funded by** ATRIUM [^4]  💰
- **Shared by** ATRIUM [^4] & UFAL [^7] 🔗

**©️ 2025 UFAL & ATRIUM**

[^1]: https://github.com/cneud/alto-tools
[^2]: https://github.com/K4TEL/atrium-alto-postprocess
[^3]: https://ufal.mff.cuni.cz/~strakova/cnec2.0/ne-type-hierarchy.pdf
[^4]: https://atrium-research.eu/
[^5]: https://lindat.mff.cuni.cz/services/udpipe/api-reference.php
[^6]: https://lindat.mff.cuni.cz/services/nametag/api-reference.php
[^8]: https://github.com/K4TEL/atrium-nlp-enrich
[^7]: https://ufal.mff.cuni.cz/home-page
