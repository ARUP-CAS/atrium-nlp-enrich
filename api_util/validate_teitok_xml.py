#!/usr/bin/env python3
"""validate_teitok_xml.py -- output-contract gate for `.teitok.xml` (issues #28, #38).

Validates every ``*.teitok.xml`` file under a directory against the
vendored, pinned schema in ``schemas/teitok/teitok.xsd`` and, for this repo's
own current output, against the rules a schema cannot express. Invoked by
``api_4_stats.sh`` immediately after TEITOK generation and before
``atrium_paradata.py finish`` -- malformed TEITOK XML must never reach the
LINDAT release or downstream visualizers/search indexes.

Usage:
    python3 api_util/validate_teitok_xml.py <target_dir> [--schema PATH]
                                            [--quiet] [--allow-empty]
                                            [--profile {contract,xsd,core,wellformed}]
                                            [--exclude PATH ...]

Profiles:
    contract    (default) ``xsd`` for every document; for documents this writer stamps as
                its current format (``<application ident="atrium-nlp-enrich"
                version="teitok-2">``) also ``core``, the writer's page/line/id rules
                (``lint_writer``) and, when the opt-in record projection is present, its
                pointer rules (``lint_projection``). Older documents on disk (format 1, from
                resumed runs) are held to the XSD only, as before.
    xsd         the pinned XSD alone
    core        TEITOK-core lint that holds for *any* TEITOK profile (e.g. flexiconv
                output): <TEI> root, a <text>, unique ids, resolvable heads and
                ``#`` references (``sameAs``, ``corresp``), no whitespace after a
                join="right" token, non-negative bboxes (``#`` references also in ``ana``,
                ``resp`` and ``scheme``)
    wellformed  well-formedness + <TEI> root only (``--wellformed-only`` is an alias)

The XSD types ids as plain strings, so a duplicate ``pb``/``lb`` id or a ``sameAs``
pointing nowhere passed it; ``core`` and ``lint_writer`` catch those (issue #38, D).

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
import re
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


_BBOX_OK = re.compile(r"^\d+ \d+ \d+ \d+$")


def _local(tag) -> str:
    return tag.split("}")[-1] if isinstance(tag, str) else ""


_BLOCK_BOUNDARY = frozenset({"s", "p", "div", "head", "item", "cell", "l", "u", "ab", "body"})


def _token_gaps(root):
    """Yield ``(tok, next_tok, gap_text)`` for consecutive tokens inside one block, where
    ``gap_text`` is the character data between them in document order."""
    state = {"prev": None, "gap": ""}
    out = []

    def walk(el):
        tag = _local(el.tag)
        if tag == "tok":
            if state["prev"] is not None:
                out.append((state["prev"], el, state["gap"]))
            state["prev"], state["gap"] = el, ""
            return
        if tag in _BLOCK_BOUNDARY:
            state["prev"] = None
        if el.text and state["prev"] is not None:
            state["gap"] += el.text
        for child in el:
            if isinstance(child.tag, str):
                walk(child)
            if child.tail and state["prev"] is not None:
                state["gap"] += child.tail
        if tag in _BLOCK_BOUNDARY:
            state["prev"] = None

    walk(root)
    return out


_XML_ID = "{http://www.w3.org/XML/1998/namespace}id"
# Attributes holding space-separated local references ("#w-1 #w-2"). `ana`, `resp` and
# `scheme` carry the opt-in record projection's pointers (api_util/teitok_project.py,
# atrium-project#70); a "#" reference that names nothing is an error in any TEITOK profile.
_REF_ATTRS = ("sameAs", "corresp", "ana", "resp", "scheme")


def lint_core(doc) -> list[str]:
    """TEITOK-core conventions every TEITOK document should meet, whatever its profile.

    Deliberately weaker than the XSD: flexiconv, teitok-tools and TEITOK itself write
    documents our schema does not describe (and tokens without ``@id`` before TEITOK
    renumbers them), so only rules those tools rely on are checked here: ids are unique
    across the document (TEITOK addresses every element by id), and a ``#id`` in
    ``sameAs``/``corresp``/``ana``/``resp``/``scheme`` names one of them.
    """
    root = doc.getroot()
    errors = []
    if _local(root.tag) != "TEI":
        return [f"unexpected root element <{_local(root.tag)}>, expected <TEI>"]
    if not any(_local(el.tag) == "text" for el in root.iter()):
        errors.append("no <text> element")

    ids = {}
    for el in root.iter():
        tag = _local(el.tag)
        if not tag:
            continue
        el_id = el.get("id") or el.get(_XML_ID)
        if el_id is None:
            continue
        if not el_id.strip():
            errors.append(f"line {el.sourceline}: <{tag}> has an empty @id")
        elif el_id in ids:
            errors.append(f"line {el.sourceline}: duplicate @id {el_id!r} (<{tag}>)")
        else:
            ids[el_id] = tag

    for el in root.iter():
        tag = _local(el.tag)
        if tag in ("tok", "dtok"):
            head = el.get("head")
            if head and not head.isdigit() and ids.get(head) not in ("tok", "dtok"):
                errors.append(f"line {el.sourceline}: head {head!r} does not resolve to a token id")
        for attr in _REF_ATTRS:
            for ref in (el.get(attr) or "").split():
                if ref.startswith("#") and ref[1:] not in ids:
                    errors.append(
                        f"line {el.sourceline}: {attr} {ref!r} on <{tag}> does not resolve"
                    )
        bbox = el.get("bbox")
        if bbox is not None and not _BBOX_OK.match(bbox.strip()):
            errors.append(f"line {el.sourceline}: bbox {bbox!r} is not four non-negative integers")

    # join="right" says "no space follows"; whitespace before the next token contradicts it.
    for tok, _next_tok, gap in _token_gaps(root):
        if tok.get("join") == "right" and gap and any(c.isspace() for c in gap):
            label = tok.get("id") or (tok.text or "").strip()
            errors.append(
                f'line {tok.sourceline}: join="right" token {label!r} is followed by whitespace'
            )
    return errors


WRITER_IDENT = "atrium-nlp-enrich"
#: The writer format ``lint_writer`` knows (``teitok_alto.WRITER_FORMAT``; not imported, so
#: this module stays free of the writer's dependencies).
WRITER_FORMAT = "teitok-2"
_PB_ID = re.compile(r"^pb-(\d+)$")
_PAGED_ID = re.compile(r"^(lb|b|fig)-(\d+)\.(\d+)$")


def writer_format(doc) -> str | None:
    """The ``version`` this repo's writer stamped on ``doc``, or None when another tool
    (or an old run of ours, before the stamp existed) wrote it."""
    for el in doc.getroot().iter():
        if _local(el.tag) == "application" and el.get("ident") == WRITER_IDENT:
            return el.get("version")
    return None


def lint_writer(doc) -> list[str]:
    """Rules of this writer's page model (issue #38) that neither the XSD nor ``core`` can
    state: ``pb-K`` ids strictly increase in document order; ``lb-P.L``, ``b-P.K`` and
    ``fig-P.K`` carry the page they are on (the last ``<pb>`` before them), with ``L``
    increasing on the page; ``pb@corresp`` names a ``<surface>`` and every ``<surface>``
    has its ``<pb>``."""
    root = doc.getroot()
    errors = []
    surfaces = {el.get("id") for el in root.iter() if _local(el.tag) == "surface" and el.get("id")}
    text = next((el for el in root.iter() if _local(el.tag) == "text"), None)
    if text is None:
        return errors
    page, last_line, used = None, 0, set()
    for el in text.iter():
        tag = _local(el.tag)
        el_id = el.get("id") or ""
        if tag == "pb":
            match = _PB_ID.match(el_id)
            if not match:
                errors.append(f"line {el.sourceline}: <pb> id {el_id!r} is not pb-K")
                continue
            number = int(match.group(1))
            if page is not None and number <= page:
                errors.append(
                    f"line {el.sourceline}: {el_id} after pb-{page} (pages only move forward)"
                )
            page, last_line = number, 0
            corresp = (el.get("corresp") or "").lstrip("#")
            if corresp:
                if corresp not in surfaces:
                    errors.append(f"line {el.sourceline}: {el_id} corresp names no <surface>")
                used.add(corresp)
        elif tag in ("lb", "div", "figure"):
            match = _PAGED_ID.match(el_id)
            if not match:
                continue  # format-1 style or foreign ids: the XSD judges them
            on_page, number = int(match.group(2)), int(match.group(3))
            if page is None or on_page != page:
                errors.append(
                    f"line {el.sourceline}: {el_id} is not on the page it names "
                    f"(current page: {f'pb-{page}' if page is not None else 'none'})"
                )
            if tag == "lb":
                if number <= last_line:
                    errors.append(f"line {el.sourceline}: {el_id} after lb-{page}.{last_line}")
                last_line = number
    for missing in sorted(surfaces - used):
        errors.append(f"<surface id={missing!r}> has no <pb> pointing at it")
    return errors


#: What each projection pointer must name (api_util/teitok_project.py): the attribute, on which
#: element, and the element its ``#`` references must resolve to.
_PROJECTION_TARGETS = (
    ("pb", "ana", "category"),
    ("keywords", "resp", "application"),
    ("keywords", "scheme", "taxonomy"),
    ("keywords", "corresp", "pb"),
    ("term", "corresp", "pb"),
)


def lint_projection(doc) -> list[str]:
    """The record projection's pointers name the right KIND of element (issue #70): a
    ``pb@ana`` a ``<category>``, a ``keywords@resp`` an ``<application>``, a
    ``keywords@scheme`` a ``<taxonomy>``, and a page reference (``term``/``keywords``
    ``@corresp``) a ``<pb>``. ``core`` already checks that they resolve at all; a document
    without a projection has none of these attributes and passes."""
    root = doc.getroot()
    kinds = {}
    for el in root.iter():
        el_id = el.get("id") or el.get(_XML_ID)
        if el_id:
            kinds[el_id] = _local(el.tag)
    errors = []
    for el in root.iter():
        tag = _local(el.tag)
        for on, attr, target in _PROJECTION_TARGETS:
            if tag != on:
                continue
            for ref in (el.get(attr) or "").split():
                kind = kinds.get(ref[1:]) if ref.startswith("#") else None
                if kind is not None and kind != target:
                    errors.append(
                        f"line {el.sourceline}: {tag}@{attr} {ref!r} names a <{kind}>, "
                        f"expected a <{target}>"
                    )
    return errors


def _check(schema, doc, profile: str) -> list[str]:
    """Run ``profile`` on an already-parsed, namespace-normalised document."""
    root = doc.getroot()
    if root.tag != "TEI":
        return [f"unexpected root element <{root.tag}>, expected <TEI>"]
    if profile == "wellformed":
        return []
    if profile == "core":
        return lint_core(doc)
    errors = [] if schema.validate(doc) else [str(err) for err in schema.error_log]
    if profile == "contract" and writer_format(doc) == WRITER_FORMAT:
        errors += lint_core(doc) + lint_writer(doc) + lint_projection(doc)
    return errors


def _validate_one(
    schema, xml_path: Path, wellformed_only: bool = False, profile: str = "contract"
) -> list[str]:
    """Validate a single document. Returns a list of diagnostic strings
    (empty list means the document is valid). Well-formedness errors
    (e.g. truncated files, mismatched tags) are caught separately from
    schema-conformance errors so the reported message always identifies
    which kind of failure occurred.
    """
    if wellformed_only:
        profile = "wellformed"
    etree = _etree()
    try:
        doc = etree.parse(str(xml_path))
    except etree.XMLSyntaxError as exc:
        errors = [f"not well-formed XML: {exc}"]
        if "</n>" in xml_path.read_text(encoding="utf-8", errors="replace"):
            errors.append(_NAME_CLOSE_HINT)
        return errors

    _strip_tei_namespace(doc)
    return _check(schema, doc, profile)


def validate_document(
    xml_path: Path,
    schema_path: Path = DEFAULT_SCHEMA,
    wellformed_only: bool = False,
    profile: str = "contract",
) -> list[str]:
    """Validate one document on disk and return its diagnostics (empty == valid).

    Public single-document entry point.
    """
    if wellformed_only:
        profile = "wellformed"
    schema = _load_schema(schema_path) if profile in _XSD_PROFILES else None
    return _validate_one(schema, Path(xml_path), profile=profile)


def validate_xml_text(
    xml_text: str, schema_path: Path = DEFAULT_SCHEMA, profile: str = "contract"
) -> list[str]:
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
    schema = _load_schema(schema_path) if profile in _XSD_PROFILES else None
    return _check(schema, tree, profile)


def _is_excluded(path: Path, exclude) -> bool:
    resolved = path.resolve()
    for ex in exclude or ():
        ex = Path(ex).resolve()
        if resolved == ex or ex in resolved.parents:
            return True
    return False


_MODE_LABEL = {
    "contract": "output-contract",
    "xsd": "XSD",
    "core": "core",
    "wellformed": "well-formedness",
}
_XSD_PROFILES = ("contract", "xsd")


def validate_directory(
    target_dir: Path,
    schema_path: Path = DEFAULT_SCHEMA,
    quiet: bool = False,
    allow_empty: bool = False,
    wellformed_only: bool = False,
    profile: str = "contract",
    exclude=(),
) -> bool:
    """Validate every *.teitok.xml under target_dir. Returns True iff all
    discovered documents passed validation.

    With allow_empty, a missing directory or a directory with no matching
    documents is success rather than failure: TEITOK_OUTPUT_DIR is only
    created inside api_4_stats.sh's per-document loop, so a run with zero
    input documents legitimately leaves nothing to validate and should be
    judged by the runner's FAIL_ON_EMPTY, not by this gate.

    ``exclude`` lists directories (or files) to leave out -- api_4_stats.sh excludes
    flexiconv's output subdirectory, which api_flexiconv.sh gates with ``core`` instead.
    """
    if wellformed_only:
        profile = "wellformed"
    if not target_dir.is_dir():
        if allow_empty:
            print(f"[SKIP] nothing to validate: {target_dir} does not exist")
            return True
        print(f"[FATAL] target directory does not exist: {target_dir}", file=sys.stderr)
        return False

    try:
        schema = _load_schema(schema_path) if profile in _XSD_PROFILES else None
    except LxmlMissing as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return False
    except Exception as exc:  # XMLSchemaParseError, XMLSyntaxError, OSError
        print(f"[FATAL] could not load schema {schema_path}: {exc}", file=sys.stderr)
        return False

    xml_files = sorted(p for p in target_dir.rglob("*.teitok.xml") if not _is_excluded(p, exclude))
    if not xml_files:
        if allow_empty:
            print(f"[SKIP] no *.teitok.xml files under {target_dir}")
            return True
        print(f"[FATAL] no *.teitok.xml files found under {target_dir}", file=sys.stderr)
        return False

    total = len(xml_files)
    failed: list[Path] = []
    mode = _MODE_LABEL[profile]

    for xml_path in xml_files:
        try:
            errors = _validate_one(schema, xml_path, profile=profile)
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
        description="Validate *.teitok.xml files against the TEITOK output contract."
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
        "--profile",
        choices=sorted(_MODE_LABEL),
        default="contract",
        help="contract (default): the XSD, plus TEITOK-core and the writer's page rules for "
        "documents stamped teitok-2; xsd: the XSD alone; core: TEITOK-core lint for any "
        "TEITOK profile (flexiconv output); wellformed: well-formedness + <TEI> root only.",
    )
    parser.add_argument(
        "--wellformed-only",
        action="store_true",
        help="Alias for --profile wellformed.",
    )
    parser.add_argument(
        "--exclude",
        type=Path,
        action="append",
        default=[],
        help="Directory or file to leave out (repeatable).",
    )
    args = parser.parse_args(argv)

    ok = validate_directory(
        args.target_dir,
        args.schema,
        quiet=args.quiet,
        allow_empty=args.allow_empty,
        profile="wellformed" if args.wellformed_only else args.profile,
        exclude=args.exclude,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
