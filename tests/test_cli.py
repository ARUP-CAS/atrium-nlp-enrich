from __future__ import annotations

import csv
from pathlib import Path

from run_pipeline import main as cli_main
from service.enrichment import _derive_config


def _workspace(tmp_path: Path, doc_id: str = "CTX000000001") -> Path:
    """A throwaway pipeline workspace with one input CSV, and a config pointing at it.

    Reuses the service's own ``_derive_config()`` rather than re-deriving the fifteen
    relocated path keys here: it is production code in this repo, it writes the same
    template with every OUTPUT_DIR/WORK_DIR/PARADATA_DIR redirected under one directory,
    and it creates the tree — so this test exercises it instead of duplicating it.
    """
    cfg = _derive_config(tmp_path)
    with open(tmp_path / "in" / f"{doc_id}.csv", "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["text", "page_num", "line_num"])
        writer.writerow(["Archeologický výzkum byl proveden.", 1, 1])
    return cfg


def test_manifest_generation_direct(tmp_path: Path):
    """
    Unit-style test: call the CLI entry point directly.

    ``--config`` on a tmp_path workspace, NOT the repo's own config_api.txt
    (atrium-project#10): the manifest stage APPENDS to ``$OUTPUT_DIR/manifest.tsv`` after
    grep-deleting any row for the same doc_id, and drops a paradata record next to it.
    Run against the default config that is ``data_samples/`` — committed fixtures — so
    every ``pytest`` invocation silently reordered ``data_samples/manifest.tsv`` (same
    rows, moved to the end) and left two untracked ``data_samples/paradata/*.json``
    behind. A developer then either committed a reordered fixture or spent the afternoon
    working out why the tree was dirty. The suite must leave no diff.
    """
    cfg = _workspace(tmp_path)

    rc = cli_main(["--config", str(cfg), "--stages", "manifest"])
    assert rc == 0

    # The stage really ran in the workspace — otherwise this test would pass just as
    # happily while still writing into data_samples/.
    manifest = tmp_path / "out" / "manifest.tsv"
    assert manifest.exists()
    assert "CTX000000001" in manifest.read_text(encoding="utf-8")
    assert list((tmp_path / "out" / "paradata").glob("*_nlp-enrich*.json"))
