"""
tests/test_run_pipeline.py
==========================
Unit tests for the pure helper functions in ``run_pipeline.py``.
"""

import argparse
import json
import time
from pathlib import Path

import pytest

import run_pipeline as rp


class TestParseConfig:

    def _write(self, tmp_path, body):
        p = tmp_path / "config_api.txt"
        p.write_text(body, encoding="utf-8")
        return p

    def test_simple_assignment(self, tmp_path):
        cfg = self._write(tmp_path, 'OUTPUT_DIR="./out"\n')
        values = rp._parse_config(cfg)
        assert values["OUTPUT_DIR"] == "./out"

    def test_var_expansion_braced(self, tmp_path):
        cfg = self._write(tmp_path,
                          'OUTPUT_DIR="./out"\n'
                          'PARADATA_DIR="${OUTPUT_DIR}/paradata"\n')
        values = rp._parse_config(cfg)
        assert values["PARADATA_DIR"] == "./out/paradata"

    def test_var_expansion_unbraced(self, tmp_path):
        cfg = self._write(tmp_path,
                          'OUTPUT_DIR="./out"\n'
                          'INPUT_TABLES_DIR="$OUTPUT_DIR/DOC_LINE_CATEG"\n')
        values = rp._parse_config(cfg)
        assert values["INPUT_TABLES_DIR"] == "./out/DOC_LINE_CATEG"

    def test_comment_and_blank_lines_ignored(self, tmp_path):
        cfg = self._write(tmp_path,
                          '# a comment\n\nOUTPUT_DIR="./out"\n# trailing\n')
        values = rp._parse_config(cfg)
        assert values == {"OUTPUT_DIR": "./out"}

    def test_unquoted_inline_comment_stripped(self, tmp_path):
        cfg = self._write(tmp_path, "TIMEOUT=60   # seconds\n")
        values = rp._parse_config(cfg)
        assert values["TIMEOUT"] == "60"

    def test_quoted_value_keeps_hash(self, tmp_path):
        cfg = self._write(tmp_path, 'MSG="a # b"\n')
        values = rp._parse_config(cfg)
        assert values["MSG"] == "a # b"

    def test_quoted_value_with_trailing_comment(self, tmp_path):
        cfg = self._write(tmp_path, 'MSG="a # b" # my comment\n')
        values = rp._parse_config(cfg)
        assert values["MSG"] == "a # b"

    def test_export_prefix_accepted(self, tmp_path):
        cfg = self._write(tmp_path, 'export OUTPUT_DIR="./out"\n')
        values = rp._parse_config(cfg)
        assert values["OUTPUT_DIR"] == "./out"

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            rp._parse_config(tmp_path / "nope.txt")


class TestConfigBool:

    @pytest.mark.parametrize("val", ["true", "True", "1", "yes", "y", "on"])
    def test_truthy(self, val):
        assert rp._config_bool({"FAIL_ON_EMPTY": val}, "FAIL_ON_EMPTY", False) is True

    @pytest.mark.parametrize("val", ["false", "0", "no", "off", "nonsense"])
    def test_falsy(self, val):
        assert rp._config_bool({"FAIL_ON_EMPTY": val}, "FAIL_ON_EMPTY", True) is False

    def test_missing_uses_default(self):
        assert rp._config_bool({}, "FAIL_ON_EMPTY", True) is True
        assert rp._config_bool({}, "FAIL_ON_EMPTY", False) is False

    def test_empty_string_uses_default(self):
        assert rp._config_bool({"FAIL_ON_EMPTY": ""}, "FAIL_ON_EMPTY", True) is True


class TestIsEmptyFailure:

    def test_genuine_empty_failure(self):
        stats = {"input_files_total": 3, "successfully_processed": 0,
                 "skipped_files": 0}
        assert rp._is_empty_failure(stats) is True

    def test_processed_some_not_a_failure(self):
        stats = {"input_files_total": 3, "successfully_processed": 2,
                 "skipped_files": 1}
        assert rp._is_empty_failure(stats) is False

    def test_all_skipped_is_resume_not_failure(self):
        stats = {"input_files_total": 3, "successfully_processed": 0,
                 "skipped_files": 3}
        assert rp._is_empty_failure(stats) is False

    def test_partial_skip_is_failure(self):
        # 3 total, 0 processed, 1 skipped -> 2 failed silently! This should be a failure.
        stats = {"input_files_total": 3, "successfully_processed": 0,
                 "skipped_files": 1}
        assert rp._is_empty_failure(stats) is True

    def test_zero_input_not_a_failure(self):
        stats = {"input_files_total": 0, "successfully_processed": 0,
                 "skipped_files": 0}
        assert rp._is_empty_failure(stats) is False


class TestParadataDiscovery:

    def test_snapshot_ignores_state_files(self, tmp_path):
        (tmp_path / "a_nlp-enrich.json").write_text("{}")
        (tmp_path / ".state_x_nlp-enrich.json").write_text("{}")
        snap = rp._snapshot_paradata_dir(tmp_path)
        names = {p.name for p in snap}
        assert "a_nlp-enrich.json" in names
        assert ".state_x_nlp-enrich.json" not in names

    def test_sweep_removes_state_files(self, tmp_path):
        (tmp_path / ".state_a_nlp-enrich.json").write_text("{}")
        (tmp_path / ".state_b_nlp-enrich.json").write_text("{}")
        (tmp_path / "keep_nlp-enrich.json").write_text("{}")
        removed = rp._sweep_stale_state_files(tmp_path)
        assert len(removed) == 2


class TestResolveLlmParadataDir:

    def test_reads_paradata_dir_from_config(self, tmp_path):
        cfg = tmp_path / "llm_config.txt"
        cfg.write_text('MODEL_KEY=x\nPARADATA_DIR="custom/pd" # comment\n', encoding="utf-8")
        result = rp._resolve_llm_paradata_dir(str(cfg), Path("default"))
        assert result == Path("custom/pd")

    def test_missing_config_returns_default(self, tmp_path):
        result = rp._resolve_llm_paradata_dir(
            str(tmp_path / "nope.txt"), Path("default"))
        assert result == Path("default")


class TestBuildPlan:

    def _args(self, **overrides):
        base = dict(
            config=Path("config_api.txt"),
            stages=list(rp._CORE_ORDER),
            kw=False, kw_method="yake",
            llm=False, llm_config="llm_config.txt",
            force=False,
        )
        base.update(overrides)
        return argparse.Namespace(**base)

    _VALUES = {
        "OUTPUT_DIR": "./out",
        "PARADATA_DIR": "./out/paradata",
        "INPUT_TABLES_DIR": "./out/DOC_LINE_CATEG",
        "FAIL_ON_EMPTY": "true",
    }

    def test_core_only_plan_has_four_stages(self):
        plan = rp._build_plan(self._args(), self._VALUES)
        names = [s["name"] for s in plan["stage_plan"]]
        assert names == ["manifest", "udp", "nt", "stats"]

    def test_force_flag_overrides_fail_on_empty(self):
        vals = dict(self._VALUES, FAIL_ON_EMPTY="true")
        plan = rp._build_plan(self._args(force=True), vals)
        assert plan["fail_on_empty"] is False


class TestPreflights:
    def test_keybert_preflight_passes_when_present(self, monkeypatch):
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *a, **k):
            # Includes torch/transformers to safely emulate the patched preflight loader
            if name in ("torch", "transformers", "transformers.modeling_utils", "keybert", "sentence_transformers"):
                return object()  # pretend importable
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        # Should not raise
        rp._keybert_deps_preflight()


class TestSpaceStages:
    def test_first_call_returns_timestamp_without_sleeping(self):
        t0 = time.time()
        result = rp._space_stages(None)
        assert result >= t0
        assert time.time() - t0 < rp._STAGE_SPACING_SECONDS