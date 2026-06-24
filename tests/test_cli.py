from __future__ import annotations

from pathlib import Path

from run_pipeline import main as cli_main


def test_manifest_generation_direct(monkeypatch, tmp_path: Path):
    """
    Unit-style test: call the CLI entry point directly.
    """
    # Pass the arguments exactly as your CLI expects them based on the usage log
    rc = cli_main(["--stages", "manifest"])
    assert rc == 0

