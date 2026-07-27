# Archaeo NER Annotation (Label Studio Setup & Workflow)

Standalone [Label Studio](https://labelstud.io/) environment and conversion utilities for building human-corrected **IOB2 training data** for domain-specific NameTag 3 models.

The core campaign workflow consists of:
**Tokenised Input → Pre-Annotation Conversion → Label Studio Correction → Export → IOB2 Re-alignment → NameTag 3 Model Training**.

---

## Contents

| File                                 | Purpose                                                                     |
|--------------------------------------|-----------------------------------------------------------------------------|
| `docker-compose.yml`, `.env.example` | Standalone Label Studio campaign deployment (host port 8001)                |
| `archaeo_labels.xml`                 | 6-type XML label configuration for Label Studio interface                   |
| `GUIDELINES.md`                      | Boundary rules and entity definitions for annotators                        |
| `conllu_to_ls.py`                    | Converts UDPipe CoNLL-U or NameTag TSV into Label Studio task JSON          |
| `ls_to_iob2.py`                      | Converts Label Studio JSON exports back into NameTag-compatible IOB2 format |

---

## 1. Quick Start & Deployment

Deploy the campaign server using Docker Compose:

```bash
cd annotation
cp .env.example .env          # Edit LS_ADMIN_USERNAME and LS_ADMIN_PASSWORD
docker compose up -d          # Access interface at http://localhost:8001
```

Once online, create a project, select **Labeling Setup → Custom Template**, and paste the contents of `archaeo_labels.xml` into the template configuration.

---

## 2. Converting Data Samples to Label Studio Format

Below are the commands to convert raw and pre-annotated repository data samples into Label Studio JSON import files.

### Command 1: Converting Raw CoNLL-U Files

```bash
python annotation/conllu_to_ls.py \
    --conllu data_samples/UDP/CTX000000001.conllu \
    -o import_raw.json
```

* **Explanation:** Reads tokenised UDPipe output (`.conllu`) without existing entity tags and packages the text into a Label Studio import JSON array.

* **Justification:** Ensures that raw text is rendered by joining tokens with single spaces and sentences with newlines. This deterministic token joining guarantees exact character-to-token alignment when annotations are later exported.

---

### Command 2: Converting CoNLL-U Files with Pre-Annotations

```bash
python annotation/conllu_to_ls.py \
    --conllu data_samples/UDP_NE/CTX000000001/CTX000000001.conllu \
    --ner-from-misc \
    -o import_preannotated.json
```

* **Explanation:** Parses CoNLL-U files that contain pre-existing entity annotations stored inside the `MISC` column's `NER=` feature (e.g., `NER=B-ARTEFACT`).

* **Justification:** Pre-populates the Label Studio tasks with `predictions`. Importing pre-annotations shifts the annotator's task from manual span creation to fast verification and correction, drastically reducing annotation time per document.

---

### Command 3: Converting IOB2 TSV Files

```bash
python annotation/conllu_to_ls.py \
    --tsv data_samples/NE/CTX000000001/CTX000000001-1.tsv \
    -o import_tsv.json
```

* **Explanation:** Ingests two-column or three-column IOB2 TSV files (written by `api_util/call_nametag.py` or LLM pre-annotation scripts) and formats them into Label Studio task JSONs.

* **Justification:** Provides seamless interoperability with legacy model outputs and LLM pre-labeling pipelines, translating token-level `B-` / `I-` tags into character offset spans (`start`, `end`) expected by Label Studio.

---

## 3. Exporting Annotations Back to NameTag IOB2

Once annotation or correction is complete in Label Studio, export the dataset via **Export → JSON**. Run the exporter command below:

```bash
python annotation/ls_to_iob2.py \
    -i export.json \
    -o archaeo_gold.iob2
```

* **Explanation:** Reads Label Studio's exported JSON array, extracts character-level spans from `annotations`, re-aligns them to whitespace tokens, and outputs vertical IOB2 format.

* **Justification:** NameTag 3 requires token-level IOB2 files with two tab-separated columns (`token\tlabel`), `-DOCSTART-` document boundaries, and `|`-joined labels for overlapping/nested entities (e.g., `B-MATERIAL|B-ARTEFACT`). This script restores exact pipeline token alignment for direct model training.

---

## 4. Format & Design Guarantees

1. **Token Alignment Preservation:** Text strings in `data.text` are constructed deterministically from tokens. The export parser re-tokenises on identical whitespace rules, keeping span offsets 100% token-aligned with UDPipe and NameTag.

2. **Multi-Label / Overlap Handling:** Nested or overlapping spans marked in Label Studio are exported as pipe-separated IOB2 tags, fully compatible with `api_util/call_nametag.py` and `api_util/summarize_nt_udp.py`.