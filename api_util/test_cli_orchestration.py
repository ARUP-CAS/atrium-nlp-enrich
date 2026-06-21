import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).parent.parent

def test_llm_run_cli_help():
    """Ensure the LLM orchestration script compiles and parses arguments."""
    res = subprocess.run(
        [sys.executable, str(ROOT_DIR / "llm_run.py"), "--help"],
        capture_output=True, text=True
    )
    assert res.returncode == 0
    assert "usage" in res.stdout.lower() or "help" in res.stdout.lower()

def test_keywords_cli_help():
    """Ensure the keyword extraction script compiles and parses arguments."""
    res = subprocess.run(
        [sys.executable, str(ROOT_DIR / "keywords.py"), "--help"],
        capture_output=True, text=True
    )
    assert res.returncode == 0
    assert "usage" in res.stdout.lower() or "help" in res.stdout.lower()

def test_summarize_nt_udp_cli_help():
    """Ensure the summarization script compiles."""
    res = subprocess.run(
        [sys.executable, str(ROOT_DIR / "api_util" / "summarize_nt_udp.py"), "--help"],
        capture_output=True, text=True
    )
    assert res.returncode == 0
    assert "usage" in res.stdout.lower()

def test_fix_teitok_bboxes_missing_args():
    """Ensure the bounding box fixer fails gracefully without arguments."""
    res = subprocess.run(
        [sys.executable, str(ROOT_DIR / "fix_teitok_bboxes.py")],
        capture_output=True, text=True
    )
    # The script should throw an error because it expects positional args,
    # but it shouldn't crash the Python interpreter.
    assert res.returncode != 0
