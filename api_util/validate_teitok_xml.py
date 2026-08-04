#!/usr/bin/env python3
"""validate_teitok_xml.py -- XSD output-contract gate for `.teitok.xml` (issue #28).

Validates every ``*.teitok.xml`` file under a directory against the
vendored, pinned schema in ``schemas/teitok/teitok.xsd``. Invoked by
``api_4_stats.sh`` immediately after TEITOK generation and before
``atrium_paradata.py finish`` -- malformed TEITOK XML must never reach the
LINDAT release or downstream visualizers/search indexes.

Usage:
    python3 api_util/validate_teitok_xml.py <target_dir> [--schema PATH]
                                            [--quiet] [--allow-empty]
                                            [--wellformed-only]

Exit codes:
    0  every discovered *.teitok.xml document is schema-valid
    1  at least one document failed validation, or no schema could be loaded
    2  usage error (bad arguments, target dir missing)

Design notes:
  * Uses lxml (already a pipeline dependency) rather than shelling out to
    xmllint, so this stays a single self-contained Python entry point with
    no additional system package required at runtime. The import is lazy so
    that merely collecting this module (e.g. pytest in a lane that installed
    only requirements-test.txt) cannot abort the whole session.
  * Two namespace conventions exist in the wild and both are accepted, see
    ``_strip_tei_namespace``.
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

DEFAULT_SCHEMA = Path(__file__).resolve().parent.parent / "schemas" / "teitok" / "teitok.xsd"

TEI_NS = "http://www.tei-c.org/ns/1.0"

# The generator writes </name>; some older exports close <name> with </n>,
# which is not well-formed XML. api_util/bbox_scale.py::fix_name_close_tags
# repairs it (exposed as the fix_teitok_bboxes.py CLI). The gate must never
# repair silently -- it reports and fails -- but it does point at the fix.
_NAME_CLOSE_HINT = (
    "hint: this looks like the `<name>...</n>` quirk -- repair with "
    "`python3 fix_teitok_bboxes.py -i <dir>` "
    "(api_util/bbox_scale.py::fix_name_close_tags)"
)


class LxmlMissing(RuntimeError):
    """lxml is not installed, so XSD validation cannot run."""


def _etree():
    """Import lxml.etree lazily and raise a typed error if it is absent."""
    try:
        from lxml import etree
    except ImportError as exc:  # pragma: no cover - environment sanity check
        raise LxmlMissing("lxml is required for TEITOK XSD validation (pip install lxml).") from exc
    return etree


def _load_schema(schema_path: Path):
    """Parse and compile the XSD once. Raises on any load/compile failure."""
    etree = _etree()
    schema_doc = etree.parse(str(schema_path))
    return etree.XMLSchema(schema_doc)


def _strip_tei_namespace(doc) -> None:
    """Normalize a namespaced document in place so one schema covers both
    TEITOK namespace conventions.

    api_util/teitok_alto.py writes `<TEI xmlnsoff="..." lang="cs">` --
    `xmlnsoff`, not `xmlns` -- so its documents are in no namespace, and
    schemas/teitok/teitok.xsd is declared without a targetNamespace to match.
    Documents that DO carry the real TEI namespace (older exports; output of
    service/rescale.py, which preserves whatever namespace its input declared)
    are stripped here so they validate against the same schema instead of
    being rejected at the validation root.

    Attribute names are left alone: `xml:lang` stays `xml:lang` and is
    declared on <TEI> alongside the writer's plain `lang`.
    """
    prefix = f"{{{TEI_NS}}}"
    for el in doc.iter():
        if isinstance(el.tag, str) and el.tag.startswith(prefix):
            el.tag = el.tag[len(prefix) :]


def _validate_one(schema, xml_path: Path, wellformed_only: bool = False) -> list[str]:
    """Validate a single document. Returns a list of diagnostic strings
    (empty list means the document is valid). Well-formedness errors
    (e.g. truncated files, mismatched tags) are caught separately from
    schema-conformance errors so the reported message always identifies
    which kind of failure occurred.
    """
    etree = _etree()
    try:
        doc = etree.parse(str(xml_path))
    except etree.XMLSyntaxError as exc:
        errors = [f"not well-formed XML: {exc}"]
        if "</n>" in xml_path.read_text(encoding="utf-8", errors="replace"):
            errors.append(_NAME_CLOSE_HINT)
        return errors

    _strip_tei_namespace(doc)

    root = doc.getroot()
    if root.tag != "TEI":
        return [f"unexpected root element <{root.tag}>, expected <TEI>"]

    if wellformed_only:
        return []

    if schema.validate(doc):
        return []

    return [str(err) for err in schema.error_log]


def validate_document(
    xml_path: Path, schema_path: Path = DEFAULT_SCHEMA, wellformed_only: bool = False
) -> list[str]:
    """Validate one document on disk and return its diagnostics (empty == valid).

    Public single-document entry point.
    """
    schema = None if wellformed_only else _load_schema(schema_path)
    return _validate_one(schema, Path(xml_path), wellformed_only=wellformed_only)


def validate_xml_text(xml_text: str, schema_path: Path = DEFAULT_SCHEMA) -> list[str]:
    """Validate an in-memory TEITOK document and return its diagnostics
    (empty == valid).

    Used by the service layer to attach a conformance verdict to a document it
    has just transformed, without round-tripping through a temporary file.
    """
    etree = _etree()
    try:
        doc = etree.fromstring(xml_text.encode("utf-8"))
    except etree.XMLSyntaxError as exc:
        errors = [f"not well-formed XML: {exc}"]
        if "</n>" in xml_text:
            errors.append(_NAME_CLOSE_HINT)
        return errors

    tree = doc.getroottree()
    _strip_tei_namespace(tree)
    if tree.getroot().tag != "TEI":
        return [f"unexpected root element <{tree.getroot().tag}>, expected <TEI>"]

    schema = _load_schema(schema_path)
    if schema.validate(tree):
        return []
    return [str(err) for err in schema.error_log]


def validate_directory(
    target_dir: Path,
    schema_path: Path = DEFAULT_SCHEMA,
    quiet: bool = False,
    allow_empty: bool = False,
    wellformed_only: bool = False,
) -> bool:
    """Validate every *.teitok.xml under target_dir. Returns True iff all
    discovered documents passed validation.

    With allow_empty, a missing directory or a directory with no matching
    documents is success rather than failure: TEITOK_OUTPUT_DIR is only
    created inside api_4_stats.sh's per-document loop, so a run with zero
    input documents legitimately leaves nothing to validate and should be
    judged by the runner's FAIL_ON_EMPTY, not by this gate.
    """
    if not target_dir.is_dir():
        if allow_empty:
            print(f"[SKIP] nothing to validate: {target_dir} does not exist")
            return True
        print(f"[FATAL] target directory does not exist: {target_dir}", file=sys.stderr)
        return False

    try:
        schema = None if wellformed_only else _load_schema(schema_path)
    except LxmlMissing as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return False
    except Exception as exc:  # XMLSchemaParseError, XMLSyntaxError, OSError
        print(f"[FATAL] could not load schema {schema_path}: {exc}", file=sys.stderr)
        return False

    xml_files = sorted(target_dir.rglob("*.teitok.xml"))
    if not xml_files:
        if allow_empty:
            print(f"[SKIP] no *.teitok.xml files under {target_dir}")
            return True
        print(f"[FATAL] no *.teitok.xml files found under {target_dir}", file=sys.stderr)
        return False

    total = len(xml_files)
    failed: list[Path] = []
    mode = "well-formedness" if wellformed_only else "XSD"

    for xml_path in xml_files:
        try:
            errors = _validate_one(schema, xml_path, wellformed_only=wellformed_only)
        except LxmlMissing as exc:  # pragma: no cover - guarded above
            print(f"[FATAL] {exc}", file=sys.stderr)
            return False
        if errors:
            failed.append(xml_path)
            print(f"[FAIL] {xml_path.name}", file=sys.stderr)
            for line in errors:
                print(f"       {line}", file=sys.stderr)
        elif not quiet:
            print(f"[OK] {xml_path.name}")

    passed = total - len(failed)
    summary = f"TEITOK {mode} validation: {passed}/{total} documents passed"
    if failed:
        print(summary, file=sys.stderr)
        print(f"[SUMMARY] {len(failed)} document(s) failed {mode} validation:", file=sys.stderr)
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
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Succeed when the target directory is missing or holds no *.teitok.xml "
        "(an empty run has nothing to validate).",
    )
    parser.add_argument(
        "--wellformed-only",
        action="store_true",
        help="Check XML well-formedness and the <TEI> root only, skipping XSD "
        "conformance. For third-party TEITOK profiles (flexiconv) this schema "
        "does not describe.",
    )
    args = parser.parse_args(argv)

    ok = validate_directory(
        args.target_dir,
        args.schema,
        quiet=args.quiet,
        allow_empty=args.allow_empty,
        wellformed_only=args.wellformed_only,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
