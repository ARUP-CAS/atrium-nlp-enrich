# Archaeo NER annotation (Label Studio)

Standalone [Label Studio](https://labelstud.io/) setup for building the
human-corrected **IOB2 training data** for the domain-specific NameTag 3 model
(issue [#7](https://github.com/ufal/atrium-nlp-enrich/issues/7); tool choice in
issue [#18](https://github.com/ufal/atrium-nlp-enrich/issues/18)).

Workflow: **tokenise → (optional) pre-annotate → import → correct in Label Studio →
export → convert to IOB2 → feed NameTag 3 training.** Label Studio was chosen as a
lightweight, easy-to-setup option that seamlessly handles LLM pre-annotations.

Everything here is a **campaign deployment**, decoupled from the pipeline: the
only contract with the pipeline is the exported IOB2 files. The two converters
are pure-stdlib Python (no extra dependencies).

## Contents

| File                                 | Purpose                                                                |
|--------------------------------------|------------------------------------------------------------------------|
| `docker-compose.yml`, `.env.example` | Standalone Label Studio (host port 8001)                               |
| `archaeo_labels.xml`                 | The 6-type label set — paste into the project's **Labeling Interface** |
| `GUIDELINES.md`                      | Annotator boundary rules (hand this to annotators)                     |
| `conllu_to_labelstudio.py`           | Tokenised input → Label Studio import JSON (with pre-annotations)      |
| `labelstudio_to_iob2.py`             | Label Studio export JSON → NameTag 3 IOB2                              |

## 1. Deploy Label Studio

```bash
cd annotation
cp .env.example .env          # then edit LS_ADMIN_PASSWORD
docker compose up -d          # http://localhost:8001
```

Log in with your admin credentials. Create annotator user accounts or add them to the project as members.

## 2. Create the project

1. **Create Project → Labeling Setup → Custom Template**.
2. Paste the contents of `archaeo_labels.xml` into the Code block (loads the 6 types with hotkeys a/p/l/c/m/s and colours).
3. Upload / share `GUIDELINES.md` with the annotators.

## 3. Prepare + import documents

From a UDPipe CoNLL-U file (the pipeline's tokenisation — see `api_2_udp.sh` /
`api_util/call_udpipe.py`):

```bash
python conllu_to_labelstudio.py --conllu ../data_samples/UDP/CTX000000001.conllu \
    -o import.json
```

**Pre-annotation (recommended for speed)** — seed spans so annotators *correct*
instead of labelling from scratch. Either:

* read them from a CoNLL-U `NER=` MISC field:

```bash
python conllu_to_labelstudio.py --conllu merged_ner.conllu --ner-from-misc -o import.json
```

* or from an IOB2 TSV:

```bash
python conllu_to_labelstudio.py --tsv preannotated.tsv -o import.json
```

Then in Label Studio: **Import** → `import.json`.

## 4. Annotate

Annotators select spans and press the type hotkey. Multiple people can label the same documents.

## 5. Export + convert to IOB2

1. **Export → JSON** → `export.json`.
2. Convert to NameTag IOB2:

```bash
python labelstudio_to_iob2.py -i export.json -o archaeo.iob2
```

3. Normalise / validate with NameTag 3's `preprocessing/iob_to_iob2.py`, then use
as `--train_data` for the `archaeo` tagset.
