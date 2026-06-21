"""
tests/test_run_pipeline.py
==========================
Unit tests for the pure helper functions in ``run_pipeline.py``.
"""

import argparse
from pathlib import Path

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
        cfg = self._write(tmp_path, 'OUTPUT_DIR="./out"\nPARADATA_DIR="${OUTPUT_DIR}/paradata"\n')
        values = rp._parse_config(cfg)
        assert values["PARADATA_DIR"] == "./out/paradata"

    def test_var_expansion_unbraced(self, tmp_path):
        cfg = self._write(
            tmp_path, 'OUTPUT_DIR="./out"\nINPUT_TABLES_DIR="$OUTPUT_DIR/DOC_LINE_CATEG"\n'
        )
        values = rp._parse_config(cfg)
        assert values["INPUT_TABLES_DIR"] == "./out/DOC_LINE_CATEG"

    def test_comment_and_blank_lines_ignored(self, tmp_path):
        cfg = self._write(tmp_path, "# comment\n\nOUTPUT_DIR=x\n")
        values = rp._parse_config(cfg)
        assert values == {"OUTPUT_DIR": "x"}


class TestBuildPlan:
    def _args(self, **kwargs):
        ns = argparse.Namespace(
            stages=["manifest", "udp", "nt", "stats"],
            config=Path("dummy"),
            kw=False,
            kw_method="yake",
            llm=False,
            llm_config="dummy_llm.txt",
            force=False,
        )
        for k, v in kwargs.items():
            setattr(ns, k, v)
        return ns

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
            if name in (
                "torch",
                "transformers",
                "transformers.modeling_utils",
                "keybert",
                "sentence_transformers",
            ):
                return object()
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        rp._keybert_deps_preflight()


def test_is_empty_failure_strict():
    stats_all_skipped = {"total": 3, "processed": 0, "skipped": 3}

    # strict=False: skipped >= total means it's considered NOT an empty failure (a cached run)
    assert rp._is_empty_failure(stats_all_skipped, strict=False) is False

    # strict=True: processed == 0 means it IS an empty failure
    assert rp._is_empty_failure(stats_all_skipped, strict=True) is True
