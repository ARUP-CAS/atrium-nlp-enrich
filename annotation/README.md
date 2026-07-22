# Archaeo NER annotation (doccano)

Standalone [doccano](https://github.com/doccano/doccano) setup for building the
human-corrected **IOB2 training data** for the domain-specific NameTag 3 model
(issue [#7](https://github.com/ufal/atrium-nlp-enrich/issues/7); tool choice in
issue [#18](https://github.com/ufal/atrium-nlp-enrich/issues/18)).

Workflow: **tokenise → (optional) pre-annotate → import → correct in doccano →
export → convert to IOB2 → feed NameTag 3 training.** doccano was chosen as the
lightweight option for the flat v1 tagset; see #18 for the full comparison
(INCEpTION is the heavier fallback if model-in-the-loop active learning becomes
worth its setup cost).

Everything here is a **campaign deployment**, decoupled from the pipeline: the
only contract with the pipeline is the exported IOB2 files. The two converters
are pure-stdlib Python (no extra dependencies).

## Contents

| File                                 | Purpose                                                                |
|--------------------------------------|------------------------------------------------------------------------|
| `docker-compose.yml`, `.env.example` | Standalone doccano (all-in-one image, host port 8001)                  |
| `archaeo_labels.json`                | The 6-type label set — import on the project's **Labels** page         |
| `GUIDELINES.md`                      | Annotator boundary rules (hand this to annotators)                     |
| `conllu_to_doccano.py`               | Tokenised input → doccano import JSONL (with optional pre-annotations) |
| `doccano_to_iob2.py`                 | doccano export JSONL → NameTag 3 IOB2                                  |

## 1. Deploy doccano

Docker Compose is only one launch option — pick whichever fits. The rest of this
guide (project, labels, converters) is identical no matter how doccano is
started, since the converters only touch the import/export JSONL.

### Option A — Docker Compose (multi-user / campaign server)

```bash
cd annotation
cp .env.example .env          # then edit DOCCANO_ADMIN_PASSWORD
docker compose up -d          # http://localhost:8001
```

### Option B — single `docker run` (no compose file)

The all-in-one image bundles the web server **and** the Celery task worker, so
one command is enough:

```bash
docker run -d --name atrium-doccano -p 8001:8000 \
  -e ADMIN_USERNAME=admin -e ADMIN_EMAIL=admin@example.com -e ADMIN_PASSWORD=changeme \
  -v doccano-db:/data doccano/doccano:1.8.4    # http://localhost:8001
```

### Option C — pip, no Docker at all (single annotator)

doccano is a Django app and runs straight from a virtualenv:

```bash
pip install doccano
doccano init                                          # create the SQLite DB
doccano createuser --username admin --password admin  # your account
doccano webserver --port 8000                         # http://localhost:8000
```

⚠️ In a **second terminal**, also start the task worker — dataset **import and
export run as background jobs**, so without it they will silently hang:

```bash
doccano task
```

(Options A and B include this worker inside the container; only the pip path
needs it started separately.)

---

Then log in with your admin credentials. Create annotator user accounts under
the Django admin (`/admin`) or add them to the project as members.

## 2. Create the project

1. **Create Project → Sequence Labeling.** Tick *"Allow overlapping"* only if you
   want to record genuine nested spans (e.g. `bronze` = MATERIAL + ARTEFACT) for a
   future seq2seq v2; leave it off for a clean flat v1.
2. **Labels → Import** → `archaeo_labels.json` (loads the 6 types with hotkeys
   a/p/l/c/m/s and colours).
3. Upload / share `GUIDELINES.md` with the annotators.

## 3. Prepare + import documents

doccano works on character offsets, so import **text that is a deterministic
join of the pipeline's tokens** — then export stays token-aligned with NameTag.
`conllu_to_doccano.py` builds that text (tokens space-joined, sentences
newline-joined) and reconstructs any pre-annotation spans.

From a UDPipe CoNLL-U file (the pipeline's tokenisation — see `api_2_udp.sh` /
`api_util/call_udpipe.py`, or reuse `data_samples/UDP/*.conllu`):

```bash
python conllu_to_doccano.py --conllu ../data_samples/UDP/CTX000000001.conllu \
    -o import.jsonl
```

**Pre-annotation (recommended for speed)** — seed spans so annotators *correct*
instead of labelling from scratch. Either:

- read them from a CoNLL-U `NER=` MISC field:
  ```bash
  python conllu_to_doccano.py --conllu merged_ner.conllu --ner-from-misc -o import.jsonl
  ```
- or from an IOB2 TSV in the exact format `api_util/call_nametag.py` writes
  (`Word<TAB>Tag[<TAB>NE]`) — this is also the format a future LLM pre-annotator
  should emit:
  ```bash
  python conllu_to_doccano.py --tsv preannotated.tsv -o import.jsonl
  ```

Then in doccano: **Dataset → Import → JSONL** → `import.jsonl`.

> Bootstrapping options for the pre-annotation TSV/CoNLL-U: the existing LLM stack
> (`llm_run.py`, `llm_utils.py`) for LLM pre-labelling, or a first pass of the
> current NameTag model via `api_util/call_nametag.py` as weak labels to correct.

## 4. Annotate

Annotators select spans and press the type hotkey. Multiple people can label the
same documents; reconcile to a gold set (doccano shows per-example progress; for
formal inter-annotator agreement, export each annotator's set and compare, or use
the heavier INCEpTION curation if that becomes a requirement).

## 5. Export + convert to IOB2

1. **Dataset → Actions → Export dataset → JSONL (Text-Label)** → `export.jsonl`.
2. Convert to NameTag IOB2:
   ```bash
   python doccano_to_iob2.py -i export.jsonl -o archaeo.iob2
   ```
   Output is two tab-separated columns (`token<TAB>label`), `-DOCSTART-<TAB>O` +
   blank line per document, blank lines between sentences, and `|`-joined labels
   for overlaps — exactly what `call_nametag.py::_get_ne_suffix` and NameTag 3
   training expect.
3. Normalise / validate with NameTag 3's `preprocessing/iob_to_iob2.py`, then use
   as `--train_data` for the `archaeo` tagset (issue #7, Phase 1–2).

## Format notes

- One doccano example == one document. Sentence boundaries are newlines in the
  text; document boundaries become `-DOCSTART-` on export.
- Round-trip is token-exact because the import text is a space/newline join of the
  tokens and the exporter re-tokenises on the same whitespace rule.
- Offsets are Unicode code points (fine for Czech/Latin BMP text).
- `doccano_to_iob2.py` tolerates export variants: `label` / `entities` /
  `annotations` / `labels`, and both `[start, end, label]` triples and
  `{start_offset, end_offset, label}` objects.
