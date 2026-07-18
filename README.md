<p align="center">
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.8+-blue.svg" title="Python Version"></a>
  <a href="https://lindat.mff.cuni.cz/services/udpipe/api-reference.php"><img src="https://img.shields.io/badge/API-UDPipe%202-0055A4.svg" title="UDPipe 2 API (Lindat)"></a>
  <a href="https://lindat.mff.cuni.cz/services/nametag/api-reference.php"><img src="https://img.shields.io/badge/API-NameTag%203-0055A4.svg" title="NameTag 3 API (Lindat)"></a>
  <a href="https://opensource.org/license/mit/"><img src="https://img.shields.io/github/license/ufal/atrium-nlp-enrich" title="MIT License"></a>
  <a href="https://atrium-research.eu/"><img src="https://img.shields.io/badge/funded%20by-ATRIUM-8A2BE2.svg" title="ATRIUM Project"></a>
</p>

---

# ATRIUM NLP Enrichment - Agent Skill 🤖📖

### Goal: let coding agents enrich digitized text with Czech NLP annotations via a server-client skill

This branch (`agent-skill`) packages the **ATRIUM NLP Enrichment API service** together
with a **Skill for coding agents** (Claude Code, Codex, Gemini/Antigravity). The design
follows a strict server-client split:

- **Server** 🖥️ - the FastAPI service in [`service/`](service/) runs the
  UDPipe + NameTag + keyword pipeline (Docker Compose `api` profile or local venv).
- **Client** 🪶 - [`scripts/atrium_enrich.py`](scripts/atrium_enrich.py), a
  **zero-dependency** stdlib-only script that agents call directly (sync, async
  jobs, stdin lines, and workspace-ZIP modes).
- **Skill contract** 📜 - [`SKILL.md`](SKILL.md) tells the agent when and how to use
  it: pipeline stages, keyword-method selection, busy/error playbooks.

For pipeline development, batch workflows, and full project documentation, see the
[`test`](https://github.com/ufal/atrium-nlp-enrich/tree/test) branch - this branch
intentionally carries only what the skill needs.

### Table of contents 📑

  * [Quick start 🚀](#quick-start-)
  * [Skill installation 🔧](#skill-installation-)
  * [Server setup 🖥️](#server-setup-)
  * [Client usage 🪶](#client-usage-)
  * [Remote server / LINDAT 🌐](#remote-server--lindat-)
  * [Maintenance notes 🔍](#maintenance-notes-)
  * [Contacts 📧](#contacts-)

----

## Quick start 🚀

```bash
git clone -b agent-skill https://github.com/ufal/atrium-nlp-enrich.git
cd atrium-nlp-enrich

bash scripts/server.sh                                            # start the server
python3 scripts/atrium_enrich.py small_data_samples/CTX000000001.csv   # enrich a sample
```

> [!NOTE]
> The first server start prefetches the KeyBERT embedding model (~500 MB) into
> the HF cache - be patient. ⏳

## Skill installation 🔧

### Claude Code

```bash
git clone -b agent-skill https://github.com/ufal/atrium-nlp-enrich.git \
    ~/.claude/skills/atrium-nlp-enrich
```

Restart Claude Code - the skill is available as `/atrium-nlp-enrich` and is selected
automatically for NLP-enrichment requests. For a project-local install, clone into
`.claude/skills/atrium-nlp-enrich` inside the target repository.

### Codex

```bash
git clone -b agent-skill https://github.com/ufal/atrium-nlp-enrich.git \
    ~/.codex/skills/atrium-nlp-enrich
```

The skill is detected automatically in the next Codex session.

### Google Antigravity

Clone the branch into your project and point `AGENTS.md` at it:

```
Use the ATRIUM NLP enrichment skill from `atrium-nlp-enrich/SKILL.md` for
enriching OCR/HTR text lines with morphology, named entities, and keywords.
Start the server with `bash atrium-nlp-enrich/scripts/server.sh`, then run
`python3 atrium-nlp-enrich/scripts/atrium_enrich.py [FILES...]`.
```

Update any install with `git pull` inside the cloned skill directory.

## Server setup 🖥️

The server exposes the enrichment API (see [`service/README.md`](service/README.md)
for details): `GET /info`, `GET /health`, `POST /enrich`, `POST /enrich_text`,
`POST /rescale`, and the async jobs API (`POST /jobs`, `GET /jobs/{id}`,
`GET /jobs/{id}/result`, `DELETE /jobs/{id}`).

```bash
bash scripts/server.sh          # auto: Docker Compose api profile, else local uvicorn
bash scripts/server.sh --local  # force local uvicorn via setup_api_service.sh
```

The script is idempotent and health-waits on `/info`. Port defaults to `8000`
(`ATRIUM_NE_PORT` to change).

## Client usage 🪶

```bash
python3 scripts/atrium_enrich.py lines.csv                          # sync enrichment
python3 scripts/atrium_enrich.py notes.txt --kw-method yake         # different backend
python3 scripts/atrium_enrich.py lines.csv --jobs                   # async jobs API
python3 scripts/atrium_enrich.py lines.csv --zip out.zip            # workspace ZIP
python3 scripts/atrium_enrich.py - --doc-id CTX1 < lines.txt        # stdin lines
python3 scripts/atrium_enrich.py --info                             # capabilities
```

Output rows: `DOC, RANK, KEYWORD, SCORE` (`--format table|csv|json`). The full
envelope (TEITOK XML, entities, paradata) is available via `--format json`; the
complete pipeline workspace via `--zip`. Semantics are documented in
[`SKILL.md`](SKILL.md).

## Remote server / LINDAT 🌐

The client is location-agnostic: point it at any deployment with `--base-url` or

```bash
export ATRIUM_NE_URL="https://<hosted-instance>/atrium-ne"
```

A hosted LINDAT instance is planned; once available, the environment variable is the
only change needed - the skill contract and client stay identical.

## Maintenance notes 🔍

Review checklist for every change / sync-merge into this branch (the ATRIUM skill
anti-pattern checklist):

- [ ] no doc references a script name that differs from the committed file;
- [ ] no provenance/paradata claim unless the service imports it on this branch;
- [ ] no reference to directories/files absent from this branch;
- [ ] documented response fields match what `service/api.py` actually returns;
- [ ] client smoke test re-run on `small_data_samples/` against a locally started server.

## Contacts 📧

**For support write to:** lutsai.k@gmail.com responsible for the
[GitHub repository](https://github.com/ufal/atrium-nlp-enrich)

### Acknowledgements 🙏

- **Developed by** UFAL, Charles University 👥
- **Funded by** [ATRIUM](https://atrium-research.eu/) 💰
- **Powered by** [LINDAT/CLARIAH-CZ](https://lindat.cz) UDPipe & NameTag services 🔗
