# 📓 atrium-nlp-enrich — agent_dev_logs/DEVLOG.md (timeline index)
> _NLP enrichment of OCR text lines. 6 open issues (#6–#11). `test` HEAD `71341ed` (2026-07-12) · **v0.16.1**._
> _Per-issue detail: `digests/{id}.digest.md` · `plans/{id}.plan.md` · `issues/` exports (source of truth; **#11 has no export yet**). Cross-repo/hub history lives in `ufal/atrium-project/agent_dev_logs/DEVLOG.md` (deduplicated out of this file)._

## 2026-04-17
- **#6 Extract keywords via LLM (TEATER topics)** — Opened by K4TEL: a local LLM takes the document + topic thesaurus + JSON template and returns thesaurus terms linked to the text.

## 2026-04-19
- **#6** — Posted an LLM comparison table (Qwen 2.5, Mistral NeMo, …); commit `003c05b` drafts the solution + README "LLM Semantic Enrichment" section.

## 2026-04-22
- **#6** — Commit `c9759a7`: full implementation, switching Ollama → Hugging Face `transformers` with `lmformatenforcer` to guarantee Pydantic-schema JSON.

## 2026-04-23
- **#6** — Commit `7e851e3`: anti-hallucination fixes incl. an injected `"Nerelevantní (meta-text)"` category that survives context truncation; cross-model analysis.

## 2026-04-24
- **#6** — Commit `c109444` results: top performers Qwen 2.5 14b (AWQ) & Qwen 3 14b (phrase-level keywords); priority taxonomy (Documentation, Chronology, …).

## 2026-04-27
- **#6** — Commit `382b27e` adds 3 LLM result folders (MoE still has memory bugs); Gemini ranking (Tier 1 `gemma-4-31b`, `qwen-36-27b`); commit `89f3d82` disables Qwen 3 thinking-mode for constrained decoding + a meta-text keyword guard; commit `fafbb37` restructures the LLM pipeline (`llm_run.py` / `llm_utils.py` / `vocab_manager.py`), released **v0.10.0**.

## 2026-04-29
- **#6** — Commit `49f9b2b` results: Gemma 4 31B top tier (flawless meta-text discipline, phrase-level keywords); TODO MoE models + 100–500B dense/MoE runs on the 144 GB/200 GH node; feed the whole vocabulary when context allows.

## 2026-05-11
- **#7 Train domain-specific NameTag NER** — Opened by K4TEL: NameTag 3 for archaeology entities (à la ArchaeoBERT: PER/LOC/ART/CON/MAT/SPE); define flat vs nested NE types; annotate O-I-B data.

## 2026-05-13
- **#7** — 10 example sentences per NE type for a demo; ~10,000/type for actual BERT training.

## 2026-05-20
- **#6** — Commit `3e24876` (items 7 & 8): a `_write_abort_marker()` writes a `*.abort.json` sidecar instead of a silent abort after consecutive errors.

## 2026-05-25
- **#6** — Commit `733f8e4`: results for 8–70B models with rankings (+ a comparison chart); Qwen 3.6-27B settles in as production default.

## 2026-05-28
- **#7** — PERIOD and LOCATION to be merged with existing NameTag3 types; other ArchaeoBERT NEs transferred as-is.
- **#8 Add API service** — Opened by K4TEL (single-file entry point → NLP-enriched `teitok.xml`, all intermediate steps).
- **#9 TEITOK image-file dependence** — Opened by K4TEL (bbox calibration needs image width/height; prefer relative values; document a user-DPI alternative).
- **#10 Flexiconv-supported input options** — Opened by K4TEL (accept any flexiconv format, incl. CSV/XLSX, as raw text).

## 2026-05-29
- **#7** — motyc: Location should be merged (same thing); Period is probably a custom subcategory.

## 2026-06-12
- **#8** — Commit `3928c67`: a documented merged `run_pipeline` script (input = ordered text lines; output = teitok + keywords + LLM JSON).

## 2026-06-14
- **#8** — Commit `f295b5e` review: `num_keywords` is silently ignored (never reaches `run_pipeline.py`).

## 2026-06-15
- **#8** — Commits `d950e1d`/`48c8b85`: working API draft; flagged the missing `nlp-api` service in `docker-compose.gpu.yaml`; released **v0.12.0** (documented API tested via CLI).
- **#9** — Commit `8935d60`: post-factum TEITOK fixer + metadata-based bbox resolution; `api_util/teitok_alto.py` gets dependency-free PNG/JPEG/TIFF header readers; released **v0.13.0**; requested alignment with the flexiconv GUI.
- **#10** — Opus architecture plan confirmed (tabular CSV/XLSX → api_1–4 unchanged).

## 2026-06-16
- **#7** — Posted the CNEC 2.0 ↔ ATRIUM taxonomic alignment strategy (synthesize ArchaeoBERT domain types with NameTag 3 multitagset).
- **#10** — Commit `efbf8b8` (code not yet tested on real out-of-scope formats).

## 2026-06-17
- **#7** — Opus 4.8 Max validation of the CNEC↔ATRIUM mapping: NameTag 3 mechanics right, but use a **separate tagset** (multitagset output-masking keeps labels separate); entity-code mapping corrected.

## 2026-06-19
- **#7** — `nametag3-multilingual-260521` chosen as the base; Czech-only data + maybe some Dutch/British archaeology pages.

## 2026-06-20
- **#7** — stranak: do we need hierarchical tags? As a first learning step, would training an English-Archaeo NameTag from the ArchaeoBERT data make sense?

## 2026-06-21
- **#7** — Posted the refined NER roadmap ("decisions locked").
- **#8** — Commit `d952301`: added a `/rescale` API option + `</n>`→`</name>` fix in returned teitok.xml.
- **#11 NameTag3 multilingual base** — Opened by K4TEL: replace per-language NameTag3 model selection with the generalized multilingual model (links #7); not yet added to the API.

## 2026-06-22
- **#7** — Requested a LINDAT API model-list update (`ufal/nametag3#4`).
- **#8** — Current-state review: the FastAPI wrapper (`/enrich`, `/jobs` with a semaphore) makes the repo deployable.
- **#9** — TEITOK image-dependence audit; released **v0.15.0** with the handling implemented.
- **#10** — Current-state review.
- **#11** — Requested the API model-list update.

## 2026-06-23
- **#6** — Cluster node drivers updated to try bigger models; commit `4ef89fc`. The 235B/671B MoE run is next.

## 2026-06-24
- **#7** — `ufal/nametag3#4` resolved (to test); the multilingual LINDAT API tested fine (via #11).
- **#11** — Full model list available; commit `667b070` tested on synthetic samples (CNEC → ONTO tagset). Config switched to `nametag3-multilingual-onto-260521`; `CNEC_TO_ONTO_MAP` added to the summarization utility; `TestNameTagExplanationMapping` unit suite verifies native-ONTO + legacy-CNEC resolution. Later released in **v0.16.0** (with the paradata-template update + `agent_dev_logs/`).

## 2026-06-25
- **#7** — Posted Gemini DR 3.1's advanced methodological framework for the NameTag 3 archaeology-NER training routine. Immediate next step remains the Phase-0 pilot on Brandsen Dutch data — no training run yet.

## 2026-06-28
- **#10** — Tests + fixtures landed (`5f34f98`), resolving the coverage gap (stdlib readers, mocked conversion, lib→CLI fallback); per K4TEL the flexiconv path has **not been tested in practice** — the issue stays open until real conversion experiments run on live documents.

## 2026-07-12
- **#8** — Version single-sourcing landed (`8ab13e3`): `_read_tool_version()` reads `para_config.txt [tool] version` — no more hardcoded `0.11.0`; repo + `/info` at **v0.16.1** (licenses test per the hub template, dependency bumps; ruff pre-commit flipped to advisory). Digest reconciled against HEAD: the service now verified as a package (`service/api.py` + `enrichment.py` + `jobs.py` + `rescale.py`) with **all audit bugs fixed on HEAD**. Remaining: the `device` field in `/info`, the `nlp-api` NVIDIA reservation in `docker-compose.gpu.yaml`, and a real containerized deployment run.
- **#9** — Status corrected `Closed` → open/`Tocheck`: implementation shipped in v0.15.0, but the **XSD schema-conformance gate** at the end of `api_4_stats.sh` (Q1–Q2 milestone) and the **TEITOK-team confirmation on relative coordinates** are still pending.
- **#10** — Digest downgraded from "fully verified" per the 06-28 comment: test-covered only; stays open for real conversion experiments.
- Digests/plans refreshed across the repo; gap: **#11 still has no issue-log export** (re-run of the export tool needed).

## 2026-07-26: `atrium_document` Integration for nlp-enrich

**Action:** Implemented the paradata-pair accretion model (`atrium_document.py`) into the `nlp-enrich` pipeline.
**Details:**
- **Refactoring:** Extracted and centralized CoNLL-U parsing and ALTO bbox alignment in `teitok_alto.py` to prevent data drift between the TEITOK XML writer and the new accretion hook.
- **Reference Integrity:** Added stable `id="doc.nameN"` attributes to `<name>` tags in TEITOK XML to ensure the `entities[].teitok_ref` JSON field points to a dereferenceable element.
- **Hook Implementation:** Built `api_util/document_hook.py` to handle ONTO and CNEC tagset detection, mapping entities dynamically to the central FAIR JSON schema and capturing union bounding boxes.
- **Process Orchestration:** Wired `--document-json-dir` through `api_4_stats.sh` and `summarize_nt_udp.py`, reading paradata state via `.state_*.json` to extract `run_id` and `license_detail` across subprocess boundaries.
- **Design Gap Addressed:** `lines[]` contribution is strictly gated behind an `--include-lines` opt-in flag. Because `nlp-enrich` processes raw ALTO coordinates without visibility into `alto-postprocess`'s 1-based layout reordering, a naive `merge_block("lines")` would risk silent duplicate rows and misalignment. 
**Status:** Feature complete. Test suite and orchestration verified.

---
_Timeline index refreshed 2026-07-12 against `test` HEAD and the refreshed digests/plans. Nothing removed from the issues themselves (per hub #29); this file is a derived reading aid in `agent_dev_logs/`._
