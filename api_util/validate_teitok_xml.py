#!/usr/bin/env python3
"""validate_teitok_xml.py -- XSD output-contract gate for `.teitok.xml` (issue #28).

Validates every ``*.teitok.xml`` file under a directory against the
vendored, pinned schema in ``schemas/teitok/teitok.xsd``. Invoked by
``api_4_stats.sh`` immediately after TEITOK generation/post-processing and
before dataset packaging -- malformed TEITOK XML must never reach the
LINDAT release or downstream visualizers/search indexes.

Usage:
    python3 api_util/validate_teitok_xml.py <target_dir> [--schema PATH] [--quiet]

Exit codes:
    0  every discovered *.teitok.xml document is schema-valid
    1  at least one document failed validation, or no schema could be loaded
    2  usage error (bad arguments, target dir missing)

Design notes:
  * Uses lxml (already a pipeline dependency) rather than shelling out to
    xmllint, so this stays a single self-contained Python entry point with
    no additional system package required at runtime.
  * Recurses via ``rglob("*.teitok.xml")`` so it validates correctly
    whether TEITOK_OUTPUT_DIR is flat (one dir of files) or nested
    (per-document subdirectories) -- both layouts occur across the ATRIUM
    repos' pipeline configurations.
  * Every failing document is reported with its filename plus the
    underlying xmlschema error log, one line per structural violation, so
    a CI failure or local run points straight at the offending file and
    the offending element/attribute.
  * The schema is loaded once and reused for every document (schemas are
    stateless/reentrant in lxml), so this scales to full-corpus runs
    (tens of thousands of documents) without re-parsing the XSD each time.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from lxml import etree
except ImportError:  # pragma: no cover - environment sanity check
    print(
        "[FATAL] lxml is required for TEITOK XSD validation "
        "(pip install lxml).",
        file=sys.stderr,
    )
    sys.exit(1)

DEFAULT_SCHEMA = Path(__file__).resolve().parent.parent / "schemas" / "teitok" / "teitok.xsd"


def _load_schema(schema_path: Path) -> "etree.XMLSchema":
    """Parse and compile the XSD once. Raises on any load/compile failure."""
    schema_doc = etree.parse(str(schema_path))
    return etree.XMLSchema(schema_doc)


def _validate_one(schema: "etree.XMLSchema", xml_path: Path) -> list[str]:
    """Validate a single document. Returns a list of diagnostic strings
    (empty list means the document is valid). Well-formedness errors
    (e.g. truncated files, mismatched tags) are caught separately from
    schema-conformance errors so the reported message always identifies
    which kind of failure occurred.
    """
    try:
        doc = etree.parse(str(xml_path))
    except etree.XMLSyntaxError as exc:
        return [f"not well-formed XML: {exc}"]

    if schema.validate(doc):
        return []

    return [str(err) for err in schema.error_log]


def validate_directory(
    target_dir: Path, schema_path: Path = DEFAULT_SCHEMA, quiet: bool = False
) -> bool:
    """Validate every *.teitok.xml under target_dir. Returns True iff all
    documents (at least one must exist) passed validation."""
    if not target_dir.is_dir():
        print(f"[FATAL] target directory does not exist: {target_dir}", file=sys.stderr)
        return False

    try:
        schema = _load_schema(schema_path)
    except (etree.XMLSchemaParseError, etree.XMLSyntaxError, OSError) as exc:
        print(f"[FATAL] could not load schema {schema_path}: {exc}", file=sys.stderr)
        return False

    xml_files = sorted(target_dir.rglob("*.teitok.xml"))
    if not xml_files:
        print(f"[FATAL] no *.teitok.xml files found under {target_dir}", file=sys.stderr)
        return False

    total = len(xml_files)
    failed: list[Path] = []

    for xml_path in xml_files:
        errors = _validate_one(schema, xml_path)
        if errors:
            failed.append(xml_path)
            print(f"[FAIL] {xml_path.name}", file=sys.stderr)
            for line in errors:
                print(f"       {line}", file=sys.stderr)
        elif not quiet:
            print(f"[OK] {xml_path.name}")

    passed = total - len(failed)
    summary = f"TEITOK XSD validation: {passed}/{total} documents passed"
    if failed:
        print(summary, file=sys.stderr)
        print(f"[SUMMARY] {len(failed)} document(s) failed schema validation:", file=sys.stderr)
        for path in failed:
            print(f"  - {path}", file=sys.stderr)
        return False

    print(summary)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate *.teitok.xml files against the pinned TEITOK XSD contract."
    )
    parser.add_argument(
        "target_dir",
        type=Path,
        help="Directory to search (recursively) for *.teitok.xml files.",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=DEFAULT_SCHEMA,
        help=f"Path to the XSD schema (default: {DEFAULT_SCHEMA}).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print [FAIL] lines and the final summary, suppress [OK] lines.",
    )
    args = parser.parse_args(argv)

    ok = validate_directory(args.target_dir, args.schema, args.quiet)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
