import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_api_util_path = str(Path(__file__).parent.parent / "api_util")
if _api_util_path not in sys.path:
    sys.path.insert(0, _api_util_path)

from api_util.flexiconv_convert import (  # noqa: E402
    FlexiconvNotInstalled,
    convert_to_teitok,
    flexiconv_available,
    is_flexiconv_format,
    normalize_ext_list,
)


def test_ext_normalization():
    assert normalize_ext_list("pdf docx, ODT") == frozenset({"pdf", "docx", "odt"})
    assert normalize_ext_list("") == frozenset()


def test_is_flexiconv_format():
    allowed = frozenset({"pdf", "docx", "txt"})
    assert is_flexiconv_format("doc.pdf", allowed) is True
    assert is_flexiconv_format("path/to/file.TXT", allowed) is True
    assert is_flexiconv_format("data.csv", allowed) is False
    assert is_flexiconv_format("data.xlsx", allowed) is False


def test_convert_fallback_to_cli_mocked(monkeypatch, tmp_path):
    """Hermetic test: Verifies that if Python import fails, fallback to CLI subprocessing occurs."""
    in_file = tmp_path / "test.docx"
    in_file.write_text("dummy")
    out_dir = tmp_path / "out"

    # 1. Force Python library import to fail
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "flexiconv":
            raise ImportError("No module named flexiconv")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    # 2. Mock shutil.which to pretend CLI exists
    monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/flexiconv")

    # 3. Mock flexiconv_available so it bypasses early rejection
    monkeypatch.setattr("api_util.flexiconv_convert.flexiconv_available", lambda: True)

    # 4. Mock subprocess.run to intercept the CLI call
    called_args = []

    def fake_run(args, **kwargs):
        called_args.append(args)

    monkeypatch.setattr(subprocess, "run", fake_run)

    out_path = convert_to_teitok(in_file, out_dir)

    assert Path(out_path).name == "test.teitok.xml"
    assert len(called_args) == 1
    assert called_args[0] == ["/usr/bin/flexiconv", "-t", "teitok", str(in_file), str(out_path)]


def test_convert_raises_when_missing(monkeypatch, tmp_path):
    """Hermetic test: Ensure missing both Python library and CLI cleanly raises FlexiconvNotInstalled."""
    in_file = tmp_path / "test.docx"

    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "flexiconv":
            raise ImportError("No module named flexiconv")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(shutil, "which", lambda x: None)
    monkeypatch.setattr("api_util.flexiconv_convert.flexiconv_available", lambda: False)

    with pytest.raises(FlexiconvNotInstalled, match="Flexiconv is not installed"):
        convert_to_teitok(in_file, tmp_path / "out")


@pytest.mark.skipif(not flexiconv_available(), reason="flexiconv library or CLI is not installed")
def test_live_conversion_real_formats(tmp_path):
    """Live test: Prove successful conversion of standard text inputs when flexiconv is actually available."""
    in_file = tmp_path / "sample.txt"
    in_file.write_text("Hello world. This is a real test.", encoding="utf-8")
    out_dir = tmp_path / "out"

    out_path = convert_to_teitok(in_file, out_dir)
    assert Path(out_path).exists()
    assert Path(out_path).name == "sample.teitok.xml"


@pytest.mark.skipif(
    not shutil.which("flexiconv"), reason="flexiconv CLI not installed in current environment"
)
def test_live_cli_fallback(monkeypatch, tmp_path):
    """Live test: Validate proper fallback behavior against an actual working flexiconv CLI installation."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "flexiconv":
            raise ImportError("No module named flexiconv")
        return real_import(name, *args, **kwargs)

    # By mocking ONLY the Python import, we force the function to use its subprocess fallback
    monkeypatch.setattr(builtins, "__import__", fake_import)

    in_file = tmp_path / "sample.txt"
    in_file.write_text("Fallback test.", encoding="utf-8")
    out_dir = tmp_path / "out"

    out_path = convert_to_teitok(in_file, out_dir)
    assert Path(out_path).exists()
    assert Path(out_path).name == "sample.teitok.xml"
