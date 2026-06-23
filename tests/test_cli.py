from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# Update this to your actual module path
from run_pipeline import main as cli_main


def test_manifest_generation_direct(monkeypatch, tmp_path: Path):
    """
    Unit-style test: call the CLI entry point directly.
    """
    # Pass the arguments exactly as your CLI expects them based on the usage log
    rc = cli_main(["--stages", "manifest"])
    assert rc == 0

@pytest.mark.slow
def test_manifest_generation_smoke_subprocess(tmp_path: Path):
    # Change the module path to the script name
    result = subprocess.run(
        [sys.executable, "run_pipeline.py", "--stages", "manifest"], # Use the file name directly
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
