# 🤝 Contributing to the NLP Enrichment Pipeline of the ATRIUM project

Thank you for your interest in contributing!
This document describes the development workflow, conventions, and rules for contributors.

## 📦 Release History

| Version    | Highlights                                                                                                                                                                                                                                                                                      | Status      |
|:-----------|:------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|:------------|
| **v0.5.1** | text lines -> UDP -> NER -> CSV + CoNLLU-NE + alpha TEITOK (CSV inputs, 3 steps to get NER and UDP, Outputs per-document files: CoNLLU-NE as a combination of UDP and NT APIs application, CSV table with text line's POS and NER columns, TEITOK XML formed from ALTO XML + CoNLLU-NE (draft)) | Pre-release |
| **v0.4.0** | UDP from raw CSV -> KER + NER -> CoNLLU-NE (Config file format changed, Added second per-document result files - combination of UDPipe with NER tags, UDPipe called on CSV files as before, KER called on CoNLL-U files for lemma access)                                                       | Pre-release |
| **v0.3.0** | KER + UDP -> NER from raw CSV files with textlines (Inputs format changed to CSV for KER and UDPipe, UDPipe results moved from TEMP to OUTPUT, UDPipe outputs as NER inputs)                                                                                                                    | Pre-release |
| **v0.2.0** | NER + UDP + KER from raw TXT files (KER local processing added, TXT inputs in all)                                                                                                                                                                                                              | Pre-release |
| **v0.1.0** | NER + UDP from raw TXT files (Per-page txt files extracted by alto-tools as inputs, 4-step process, Initial working version)                                                                                                                                                                    | Pre-release |

## 🌿 Branches & Environments

| Branch   | Environment          | Rule                                                                            |
|----------|----------------------|---------------------------------------------------------------------------------|
| `test`   | Staging              | Base for all development. Always branch from `test`.                            |
| `master` | Stable / Integration | Merged exclusively by a human reviewer. Do not open PRs directly into `master`. |

```text
test    ←  feature-<name>
test    ←  bugfix-<name>
master  ←  (humans only, after test stabilises)

```

### 🏷️ Branch Naming

| Type             | Pattern          | Example              |
|------------------|------------------|----------------------|
| New feature      | `feature-<name>` | `feature-teitok`     |
| Bug fix          | `bugfix-<name>`  | `bugfix-chunking`    |
| Hotfix on master | `hotfix-<name>`  | `hotfix-api-timeout` |

---

## 🔁 Contributor Workflow

1. **Create an issue** (or find an existing one) describing the problem or feature.
2. **Branch from `test`:**
```bash
git checkout test
git pull origin test
git checkout -b feature-<name>
```
3. **Implement your changes** observing the project's code conventions.
4. **Run the minimum tests** (see the Testing section).
5. **Open a Pull Request** targeting the `test` branch.

---

## 📋 Pull Request Format

Every PR must include:

* **Issue link:** `Closes #<number>` or `Refs #<number>`
* **Motivation:** why the change is needed
* **Description of change:** what was changed and how
* **Testing:** what was run, what passed, what could not be executed

Use a **Draft PR** if the work is not ready for review.

**Do not open PRs into `master` — merging into `master` is exclusively the 
maintainers' responsibility.

> **Note on issue tracking:** Issues reference the commits and PRs that resolved 
> them — not the other way around. Commit messages describe *what changed*; the issue 
> is the place to record *why* and link the resulting commits together.

---

## ✏️ Commit Messages

Format:

```text
[type] concise description of what changed
```

Allowed types:

| Type       | When to use                           |
|------------|---------------------------------------|
| `add`      | Added content (general)               |
| `edit`     | Edited existing content (general)     |
| `remove`   | Removed existing content (general)    |
| `fix`      | Bug fix                               |
| `refactor` | Refactoring without behaviour change  |
| `test`     | Adding or updating tests              |
| `docs`     | Documentation only                    |
| `chore`    | Build, dependencies, CI configuration |
| `style`    | Formatting, no logic change           |
| `perf`     | Performance optimisation              |


---

## 🧪 Code Conventions & Testing

### Code Conventions

* **Comments:** informative but short, may be LLM-generated, added when function name does 
not explain its functionality in detail
* **Argument types:** set default type (e.g., `int`, `list`) for function arguments
* **Console flags:** when a new one added, provide help message for it
* **Config files:** when set of variables changes it should be reflected in repository documentation
* **Generated code:** always should be manually launched and checked for mistakes before pushing

### Minimum checks before every commit

Always run basic validation locally before pushing:

```bash
# 1. Python compilation check
python -m compileall -q .

# 2. Pre-commit hooks (runs black, isort, flake8, etc.)
pre-commit run --all-files

```

> [!NOTE]
>  If specific scripts or extraction modules are updated, please run a smoke-test 
> against the `data_samples/` directory to verify extraction integrity.

---

## 📁 Repository Documentation Management

Each documentation file has one target audience and one responsibility. Rules are not repeated — cross-references are used instead.

| File              | Audience        | Responsibility                                 |
|-------------------|-----------------|------------------------------------------------|
| `README.md`       | GitHub visitors | Project overview, workflow stages, quick start |
| `CONTRIBUTING.md` | Developers      | Code conventions, branches, PRs, testing       |

* **Do not duplicate rules:** if a rule is defined in `CONTRIBUTING.md`, other files 
reference it rather than copying it.
* **When changing a rule:** update the canonical source and verify that referencing files
still point correctly.

---

## ⚙️ Generated Artefacts

Some files are modified automatically by scripts or hooks:

| Script              | What it generates                                      |
|---------------------|--------------------------------------------------------|
| `api_1_manifest.sh` | `manifest.tsv` — ordered list of all pages to process  |
| `api_2_udp.sh`      | `UDP/*.conllu` — per-document CoNLL-U files            |
| `api_3_nt.sh`       | `NE/*/*.tsv` — per-page NER-annotated TSV files        |
| `api_4_stats.sh`    | `UDP_NE/`, `TEITOK/`, `summary_ne_counts.csv`          |

Rules:

1. Do not manually edit auto-generated output files.
2. After changing chunking logic, re-run `api_2_udp.sh` to verify CoNLL-U validity.
3. After changing NER merging logic or TEITOK XML composition, re-run `api_4_stats.sh`
and inspect `summary_ne_counts.csv`.

---

## 🚩 Output Format Flags

Pipeline output is controlled by boolean flags in `config_api.txt`. When adding a new output format,
follow this pattern:

| Variable         | Description                                              | Default |
|------------------|----------------------------------------------------------|---------|
| `SAVE_CONLLU_NE` | Enriched CoNLL-U with NER in the `MISC` field            | `true`  |
| `SAVE_CSV`       | Token-level summary CSV per document                     | `true`  |
| `SAVE_TEITOK`    | TEITOK-style TEI XML with bounding boxes (requires ALTO) | `true`  |

New flags must be documented here and in `config_api.txt`.

---

## 📞 Contacts & Acknowledgements

For technical questions contact **lutsai.k@gmail.com**

**Issues:** https://github.com/ufal/atrium-nlp-enrich/issues


* **Developed by:** UFAL [^7]
* **Funded by:** ATRIUM [^4]
* **Models:**
  * NameTag 3 [^6]
  * UDPipe 2 [^5]

**©️ 2026 UFAL & ATRIUM**


[^2]: https://github.com/ufal/atrium-alto-postprocess
[^3]: https://ufal.mff.cuni.cz/~strakova/cnec2.0/ne-type-hierarchy.pdf
[^4]: https://atrium-research.eu/
[^5]: https://lindat.mff.cuni.cz/services/udpipe/api-reference.php
[^6]: https://lindat.mff.cuni.cz/services/nametag/api-reference.php
[^1]: https://github.com/ufal/atrium-nlp-enrich
[^7]: https://ufal.mff.cuni.cz/home-page