import pytest
import subprocess
from pathlib import Path

import sys

_api_util_path = str(Path(__file__).parent.parent / "api_util")
if _api_util_path not in sys.path:
    sys.path.insert(0, _api_util_path)

from api_util.flexiconv_convert import (
    is_flexiconv_format,
    normalize_ext_list,
    convert_to_teitok,
    FlexiconvNotInstalled
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


def test_convert_fallback_to_cli(monkeypatch, tmp_path):
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
    import shutil
    monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/flexiconv")

    # 3. Mock subprocess.run to intercept the CLI call
    called_args = []

    def fake_run(args, **kwargs):
        called_args.append(args)

    monkeypatch.setattr(subprocess, "run", fake_run)

    out_path = convert_to_teitok(in_file, out_dir)

    assert Path(out_path).name == "test.teitok.xml"
    assert len(called_args) == 1
    assert called_args[0] == ["/usr/bin/flexiconv", "-t", "teitok", str(in_file), str(out_path)]


def test_convert_raises_when_missing(monkeypatch, tmp_path):
    in_file = tmp_path / "test.docx"

    # 1. Force Python library to fail
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "flexiconv":
            raise ImportError("No module named flexiconv")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    # 2. Mock shutil.which to pretend CLI is ALSO missing
    import shutil
    monkeypatch.setattr(shutil, "which", lambda x: None)

    with pytest.raises(FlexiconvNotInstalled, match="Flexiconv is not installed"):
        convert_to_teitok(in_file, tmp_path / "out")