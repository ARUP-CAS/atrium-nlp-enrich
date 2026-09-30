#!/usr/bin/env python3
"""
api_util/teitok_project.py — project a document record onto its TEITOK file (atrium-project#70
item 2; flexiconv#1).

**Opt-in, off by default.** The AMČR storage contract keeps page classification and line quality
"only in the record" and stores ``atr/teitok-xml`` as linguistics + layout, so nothing here runs
unless asked: ``run_pipeline.py --teitok-enrichment`` (or ``TEITOK_ENRICHMENT=true`` in
config_api.txt), ``teitok_enrichment=true`` on ``/enrich`` and ``/enrich_text``, ``POST
/project_record``, or this module's CLI. flexiconv#1 and the LINDAT dataset are who switch it on.

What is written — all of it in the header, plus one attribute on ``<pb>``; nothing inside
``<s>``/``<tok>``/``<name>``, so ``teitok_read``, ``teitok_layout``, alto-postprocess's reader and
the E2E (which skip the header) see the same document:

====================================  ==================================================================
Record                                TEITOK
====================================  ==================================================================
page category (``pages[].category``,  ``<pb ana="#pcat-DRAW"/>`` + ``encodingDesc/classDecl/taxonomy
``page_categories[P]``)               [@id="tax-page-category"]/category[@id="pcat-DRAW"]
                                      [@corresp=<atrium_vocab concept URI>]/catDesc``
TEATER category (``enrichment.items``) ``profileDesc/textClass/keywords[@scheme="#tax-amcr-teater"]
                                      [@resp="#app-llm-enrich"]/term[@type="teater-category"][@ref=
                                      concept URIs from teater_category_ids][@cert=max confidence]
                                      [@corresp="#pb-K …"]``; the meta sentinel is skipped
controlled keywords cs / en           ``keywords[@resp="#app-llm-enrich"][@lang]/term[@type=
                                      "extracted-keyword"][@corresp]`` (no ``@cert``: the confidence
                                      belongs to the category)
statistical keywords (nlp-enrich,     ``keywords[@resp="#app-kw"][@scheme="#kw-<method>"]/term[@type=
per document and per page)            "statistical-keyword"][@n=rank][@score]``; a page's list is one
                                      more such ``keywords`` with ``@corresp="#pb-K"``
provenance                            ``appInfo/application[@id="app-…"]`` appended after the writer's
                                      own (``teitok_layout``'s converter pick is unaffected) and one
                                      ``revisionDesc/change[@type="enriched"]``
====================================  ==================================================================

``@score`` is a TEITOK-style attribute (like ``@bbox``); everything else is TEI P5.

**Pages** named by the record resolve, in order: ``pages[].teitok_surface`` (the ``<pb
corresp>``), ``page_index`` (``pb-K``), a numeric page key (``pb-K`` — the physical index
nlp-enrich and page-classification key by), and last the ``<pb n>`` label. An llm-enrich item's
``page`` is the label of its ``## Page`` marker, so it resolves through the record's ``pages[]``
entry of that label when there is one, then by ``<pb n>``, then as a number. A page that resolves
to nothing is reported, never fatal. Line anchoring is deferred (llm-enrich's line numbers and the
writer's ``lb`` numbering drift). ``enrichment.summary``/``topics`` are never written by
llm-enrich, so there is no paragraph description to project.

**Contract.** Reads only the record (no block owner changes). Refuses a TEITOK whose ``<title>``
is neither the record's ``doc_id`` nor the canonical id of its ``source.filename`` (the seed-id
risk). Idempotent: it owns ``classDecl``, ``textClass``, ``pb@ana``, the ``app-*`` applications
and its own ``change``, strips them and writes them anew, and re-indents the header. Validates the
result with ``validate_teitok_xml`` (profile ``contract``) before anything is written; never
touches ``note[@n="orgfile"]``. A TEITOK regeneration (``REGENERATE_TEITOK``, a stage-4 re-run)
drops the projection — re-project after it.

CLI::

    python -m api_util.teitok_project --teitok X.teitok.xml --record X.document.json --in-place
    python -m api_util.teitok_project --teitok X.teitok.xml --record X.document.json --out Y.teitok.xml \\
        --kw-method yake --kw-csv KW_PER_DOC_Y/X_keywords.csv
    python -m api_util.teitok_project --teitok-dir data_samples/TEITOK --record-dir records/ \\
        --kw-method yake --kw-per-doc-dir data_samples/KW_PER_DOC_Y --in-place   # what run_pipeline runs
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import atrium_vocab  # noqa: E402
from api_util.teitok_read import _local, _tok_form  # noqa: E402
from api_util.validate_teitok_xml import (  # noqa: E402
    WRITER_FORMAT,
    _etree,
    validate_xml_text,
    writer_format,
)
from atrium_document import canonical_doc_id  # noqa: E402

#: Who the ``<change>`` names.
PROJECTOR = "atrium-nlp-enrich"
#: The applications this module may add, ``key -> (id, ident, label)``. Their ids are owned here.
APPLICATIONS: Dict[str, Tuple[str, str, str]] = {
    "pc": ("app-pc", "atrium-page-classification", "ATRIUM page classification"),
    "llm": ("app-llm-enrich", "atrium-llm-enrich", "ATRIUM LLM enrichment"),
    "kw": ("app-kw", "atrium-nlp-enrich-keywords", "atrium-nlp-enrich keywords.py"),
}
OWNED_APP_IDS = frozenset(app_id for app_id, _, _ in APPLICATIONS.values())
TAX_PAGE_CATEGORY = "tax-page-category"
TAX_VOCAB = "tax-amcr-teater"
PCAT_PREFIX = "pcat-"
KW_SCHEME_PREFIX = "kw-"
#: llm-enrich's "not about archaeology" answer (llm_client_shared.META_TERM) — not a category.
META_TERM = "Nerelevantní (meta-text)"
#: Where a ``teater_category_ids`` ``{source, id}`` pair lives as a URI.
VOCAB_BASES = {"amcr": atrium_vocab.NS["amcr"], "teater": atrium_vocab.NS["teater"]}
#: flexiconv#1 asks for 5-20 keyword/score pairs per page.
KW_MIN, KW_MAX = 5, 20

_ID_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


class ProjectionError(ValueError):
    """The projection was refused: wrong document, invalid input, or invalid output."""


def clamp_keywords(n: int) -> int:
    return max(KW_MIN, min(KW_MAX, int(n)))


# ── small XML helpers (namespace-agnostic: rescaled documents may carry the TEI namespace) ──


def _children(parent, tag: str) -> list:
    return [el for el in parent if _local(el.tag) == tag]


def _child(parent, tag: str):
    found = _children(parent, tag)
    return found[0] if found else None


def _make(parent, tag: str, attrs: Optional[Dict[str, str]] = None, text: Optional[str] = None):
    """A new element in ``parent``'s namespace (appended; callers move it where it belongs)."""
    etree = _etree()
    ns = parent.tag[1:].split("}")[0] if parent.tag.startswith("{") else None
    el = etree.SubElement(parent, f"{{{ns}}}{tag}" if ns else tag)
    for key, value in (attrs or {}).items():
        if value is not None and value != "":
            el.set(key, str(value))
    if text is not None:
        el.text = text
    return el


_HEADER_ORDER = ("fileDesc", "encodingDesc", "profileDesc", "revisionDesc")


def _ind(depth: int) -> str:
    return "\n" + "  " * depth


def _place(parent, el, depth: int, before=None) -> None:
    """Move ``el`` (just appended by :func:`_make`) before ``before`` or to the end of ``parent``,
    with the writer's two-space indentation at ``depth``. Only whitespace next to ``el`` changes,
    so the writer's own header lines stay byte-identical; a one-line parent
    (``<profileDesc><langUsage>…</langUsage></profileDesc>``) opens up to take a second child."""
    parent.remove(el)
    if before is not None:
        el.tail = _ind(depth)
        parent.insert(parent.index(before), el)
        return
    if len(parent) == 0:
        parent.text = _ind(depth)
        el.tail = _ind(depth - 1)
    else:
        last = parent[-1]
        if last.tail and not last.tail.strip() and "\n" in last.tail:
            el.tail = last.tail
        else:
            if not (parent.text or "").strip():
                parent.text = _ind(depth)
            el.tail = _ind(depth - 1)
        last.tail = _ind(depth)
    parent.append(el)


def _detach(el) -> None:
    """Remove ``el``; when it was the last child, its tail (the parent's closing indentation)
    passes to the element before it, which is what :func:`_place` took it from."""
    parent = el.getparent()
    if el.getnext() is None:
        prev = el.getprevious()
        if prev is not None:
            prev.tail = el.tail
        else:
            parent.text = el.tail
    parent.remove(el)


def _header_slot(header, name: str):
    """The header child ``name`` must go before (None: the end), by the TEI element order."""
    rank = _HEADER_ORDER.index(name)
    for child in header:
        other = _local(child.tag)
        if other in _HEADER_ORDER and _HEADER_ORDER.index(other) > rank:
            return child
    return None


def _fmt_num(value: float) -> str:
    return format(float(value), "g")


# ── page resolution ─────────────────────────────────────────────────────────────────────────


class _PageIndex:
    """The document's ``<pb>`` elements, addressable the ways the record names pages."""

    def __init__(self, text_el):
        self.pbs = [el for el in text_el.iter() if _local(el.tag) == "pb" and el.get("id")]
        self.by_id = {el.get("id"): el for el in self.pbs}
        self.order = {el.get("id"): i for i, el in enumerate(self.pbs)}
        self.by_surface: Dict[str, str] = {}
        self.by_n: Dict[str, List[str]] = {}
        for el in self.pbs:
            corresp = (el.get("corresp") or "").lstrip("#")
            if corresp:
                self.by_surface[corresp] = el.get("id")
            self.by_n.setdefault((el.get("n") or "").strip(), []).append(el.get("id"))

    def _numeric(self, label: str) -> Optional[str]:
        if label.isdigit() and f"pb-{int(label)}" in self.by_id:
            return f"pb-{int(label)}"
        return None

    def _by_n(self, label: str) -> Optional[str]:
        ids = self.by_n.get(label) or []
        return ids[0] if len(ids) == 1 else None

    def record_page(self, entry: Dict[str, Any]) -> Optional[str]:
        """surface -> page_index -> numeric key -> ``pb@n``."""
        surface = entry.get("teitok_surface")
        if surface and surface in self.by_surface:
            return self.by_surface[surface]
        index = entry.get("page_index")
        if isinstance(index, int) and not isinstance(index, bool) and f"pb-{index}" in self.by_id:
            return f"pb-{index}"
        label = str(entry.get("page") or "").strip()
        return self._numeric(label) or self._by_n(label)

    def item_page(self, label: str, record_pages: Dict[str, Dict[str, Any]]) -> Optional[str]:
        """An llm-enrich ``page`` label: the record's page of that label, ``pb@n``, a number."""
        label = str(label).strip()
        entry = record_pages.get(label)
        if entry is not None:
            found = self.record_page(entry)
            if found:
                return found
        return self._by_n(label) or self._numeric(label)

    def sort(self, ids) -> List[str]:
        return sorted(set(ids), key=lambda i: self.order.get(i, len(self.order)))


def _corresp(ids: List[str]) -> str:
    return " ".join(f"#{i}" for i in ids)


# ── what the record says ────────────────────────────────────────────────────────────────────


def _page_categories(record: Dict[str, Any]) -> List[Tuple[Dict[str, Any], str]]:
    """``(page entry, category)`` from ``pages[].category``, then ``page_categories`` for the
    pages ``pages[]`` does not categorise. A ``page_categories`` key borrows the ``pages[]`` entry
    of the same label, so its ``teitok_surface`` resolves it."""
    by_label = {str(p.get("page")): p for p in record.get("pages") or [] if isinstance(p, dict)}
    out = []
    seen = set()
    for entry in record.get("pages") or []:
        if isinstance(entry, dict) and entry.get("category"):
            out.append((entry, str(entry["category"])))
            seen.add(str(entry.get("page")))
    for label, code in (record.get("page_categories") or {}).items():
        if str(label) not in seen and code:
            out.append((by_label.get(str(label), {"page": str(label)}), str(code)))
    return out


def _category_uris(ids: Any) -> List[str]:
    uris = []
    for ref in ids or []:
        if isinstance(ref, dict) and ref.get("source") in VOCAB_BASES and ref.get("id"):
            uri = VOCAB_BASES[ref["source"]] + str(ref["id"])
            if uri not in uris:
                uris.append(uri)
    return uris


def _block_run(record: Dict[str, Any], block: str) -> str:
    stamp = ((record.get("assembled") or {}).get("blocks") or {}).get(block) or {}
    return str(stamp.get("run_id") or "")


# ── projection ──────────────────────────────────────────────────────────────────────────────


def _strip(root, header) -> None:
    """Remove everything a previous projection wrote."""
    enc = _child(header, "encodingDesc")
    if enc is not None:
        for cls in _children(enc, "classDecl"):
            _detach(cls)
        app_info = _child(enc, "appInfo")
        if app_info is not None:
            for app in _children(app_info, "application"):
                if app.get("id") in OWNED_APP_IDS:
                    _detach(app)
    profile = _child(header, "profileDesc")
    if profile is not None:
        for tc in _children(profile, "textClass"):
            _detach(tc)
        if len(profile) == 0:
            _detach(profile)
    revision = _child(header, "revisionDesc")
    if revision is not None:
        for change in _children(revision, "change"):
            if change.get("type") == "enriched" and change.get("who") == PROJECTOR:
                _detach(change)
    for el in root.iter():
        if _local(el.tag) == "pb" and (el.get("ana") or "").startswith(f"#{PCAT_PREFIX}"):
            del el.attrib["ana"]


def _check_identity(root, record: Dict[str, Any], paired_as: Optional[str] = None) -> None:
    title = next((el for el in root.iter() if _local(el.tag) == "title"), None)
    title_text = (title.text or "").strip() if title is not None else ""
    expected = {str(record.get("doc_id") or ""), str(paired_as or "")}
    filename = (record.get("source") or {}).get("filename")
    if filename:
        expected.add(canonical_doc_id(str(filename)))
    expected.discard("")
    if expected and title_text not in expected:
        raise ProjectionError(
            f"TEITOK <title> {title_text!r} is not this record's document "
            f"(doc_id / source.filename: {sorted(expected)}); refusing to project."
        )


def project_record(
    xml_text: str,
    record: Optional[Dict[str, Any]],
    *,
    statistical: Optional[Dict[str, Any]] = None,
    when: Optional[str] = None,
    force: bool = False,
    paired_as: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    """Project ``record`` (and optional ``statistical`` keywords) onto a TEITOK document.

    ``statistical`` = ``{"method", "method_version"?, "params"?, "document": [(kw, score)],
    "pages": {pb_id: [(kw, score)]}}`` (see :func:`statistical_block`). Returns ``(xml_text,
    report)``; the text is unchanged when there is nothing to project and nothing to remove.
    Raises :class:`ProjectionError` for another document's TEITOK, for an input that does not
    validate (unless ``force``), and for an output that does not. ``paired_as`` is the id a
    caller paired the two files by (the pipeline: ``<doc_id>.teitok.xml`` with
    ``<doc_id>.document.json``), accepted as the TEITOK's identity too — an AMČR seed keeps its
    own ``doc_id`` while nlp-enrich names its outputs after the upload.
    """
    record = record or {}
    report: Dict[str, Any] = {
        "doc_id": record.get("doc_id"),
        "page_categories": 0,
        "teater_categories": 0,
        "controlled_keywords": {"cs": 0, "en": 0},
        "statistical_keywords": {"document": 0, "pages": 0},
        "unresolved_pages": [],
        "notes": [],
        "changed": False,
    }
    etree = _etree()
    try:
        root = etree.fromstring(xml_text.encode("utf-8"))
    except etree.XMLSyntaxError as exc:
        raise ProjectionError(f"TEITOK is not well-formed XML: {exc}") from exc
    tree = root.getroottree()
    if _local(root.tag) != "TEI":
        raise ProjectionError(f"root element is <{_local(root.tag)}>, not <TEI>")
    if record:
        _check_identity(root, record, paired_as)
    if writer_format(tree) != WRITER_FORMAT and not force:
        raise ProjectionError(
            f"not a {WRITER_FORMAT} document of this repo's writer; projection targets the "
            f"writer's own output (pass force to try anyway)."
        )
    before = validate_xml_text(xml_text, profile="contract")
    if before and not force:
        raise ProjectionError("input TEITOK does not validate: " + "; ".join(before[:5]))

    header = _child(root, "teiHeader")
    text_el = _child(root, "text")
    if header is None or text_el is None:
        raise ProjectionError("TEITOK has no <teiHeader> or no <text>")
    had_projection = _has_projection(root, header)
    _strip(root, header)
    pages = _PageIndex(text_el)
    record_pages = {str(p.get("page")): p for p in record.get("pages") or [] if isinstance(p, dict)}

    # page categories -> pb@ana + the taxonomy
    categories: Dict[str, str] = {}  # pcat id -> code
    for entry, code in _page_categories(record):
        pb_id = pages.record_page(entry)
        if pb_id is None:
            report["unresolved_pages"].append({"from": "page category", "page": entry.get("page")})
            continue
        cat_id = PCAT_PREFIX + _ID_UNSAFE.sub("_", code)
        pages.by_id[pb_id].set("ana", f"#{cat_id}")
        categories[cat_id] = code
        report["page_categories"] += 1
    unknown = sorted(
        {c for c in categories.values() if c not in atrium_vocab.CONCEPTS["page-category"]}
    )
    if unknown:
        report["notes"].append(f"page categories outside atrium_vocab: {unknown}")

    # llm-enrich items -> TEATER categories and controlled keywords
    teater: Dict[str, Dict[str, Any]] = {}
    controlled: Dict[str, Dict[str, List[str]]] = {"cs": {}, "en": {}}
    for item in (record.get("enrichment") or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("teater_category") or "").strip()
        if not label or label == META_TERM:
            continue
        pb_ids: List[str] = []
        if item.get("page") is not None:
            pb_id = pages.item_page(str(item["page"]), record_pages)
            if pb_id is None:
                report["unresolved_pages"].append({"from": "enrichment", "page": item["page"]})
            else:
                pb_ids.append(pb_id)
        slot = teater.setdefault(label, {"uris": [], "cert": None, "pages": []})
        for uri in _category_uris(item.get("teater_category_ids")):
            if uri not in slot["uris"]:
                slot["uris"].append(uri)
        conf = item.get("confidence_score")
        if isinstance(conf, (int, float)) and not isinstance(conf, bool):
            slot["cert"] = max(conf, slot["cert"]) if slot["cert"] is not None else conf
        slot["pages"].extend(pb_ids)
        for lang in ("cs", "en"):
            for kw in item.get(f"extracted_keywords_{lang}") or []:
                kw = str(kw).strip()
                if kw:
                    controlled[lang].setdefault(kw, []).extend(pb_ids)

    stat = statistical or {}
    stat_document = [(k, s) for k, s in stat.get("document") or [] if str(k).strip()]
    stat_pages = {}
    for pb_id, kws in (stat.get("pages") or {}).items():
        if pb_id not in pages.by_id:
            report["unresolved_pages"].append({"from": "statistical keywords", "page": pb_id})
        elif kws:
            stat_pages[pb_id] = kws
    method = str(stat.get("method") or "")
    has_stat = bool(method and (stat_document or stat_pages))

    if not (categories or teater or has_stat):
        if not had_projection:
            report["notes"].append("nothing to project")
            return xml_text, report

    # ── header: applications, classDecl, textClass, change ──
    enc = _child(header, "encodingDesc")
    if enc is None:
        enc = _make(header, "encodingDesc")
        _place(header, enc, 2, before=_header_slot(header, "encodingDesc"))
    app_info = _child(enc, "appInfo")
    if app_info is None:
        app_info = _make(enc, "appInfo")
        _place(enc, app_info, 3, before=enc[0] if len(enc) > 1 else None)

    def application(key: str, version: str = "", desc: str = "") -> None:
        # One line, like the writer's own <application> elements.
        app_id, ident, label = APPLICATIONS[key]
        app = _make(app_info, "application", {"ident": ident, "version": version, "id": app_id})
        _make(app, "label", text=label)
        if desc:
            _make(app, "desc", text=desc)
        _place(app_info, app, 4)

    if categories:
        run = _block_run(record, "page_categories") or _block_run(record, "pages")
        application("pc", desc=f"page categories of the record{f', run {run}' if run else ''}")
    if teater:
        run = _block_run(record, "enrichment")
        application("llm", desc=f"enrichment block of the record{f', run {run}' if run else ''}")
    if has_stat:
        params = stat.get("params") or {}
        application(
            "kw",
            version=str(stat.get("method_version") or method),
            desc=", ".join(f"{k}={v}" for k, v in sorted(params.items())) or f"method={method}",
        )

    formatted = []  # (element, depth) whose inside is indented once built
    if categories or teater or has_stat:
        cls = _make(enc, "classDecl")
        _place(enc, cls, 3)
        formatted.append((cls, 3))
        if categories:
            tax = _make(cls, "taxonomy", {"id": TAX_PAGE_CATEGORY})
            _make(tax, "desc", text="ATRIUM page categories (atrium_vocab scheme page-category)")
            known = atrium_vocab.CONCEPTS["page-category"]
            order = {c: i for i, c in enumerate(atrium_vocab.PAGE_CATEGORIES)}
            for cat_id, code in sorted(
                categories.items(), key=lambda kv: (order.get(kv[1], 99), kv[1])
            ):
                cat = _make(
                    tax,
                    "category",
                    {"id": cat_id, "corresp": atrium_vocab.concept_uri("page-category", code)},
                )
                definition = ((known.get(code) or {}).get("definition") or {}).get("en") or code
                _make(cat, "catDesc", {"lang": "en"}, text=definition)
        if teater:
            tax = _make(cls, "taxonomy", {"id": TAX_VOCAB})
            _make(
                tax,
                "desc",
                text="AMČR heslář and TEATER thesaurus (CC0); term/@ref holds the concept URIs "
                f"({VOCAB_BASES['amcr']}…, {VOCAB_BASES['teater']}…)",
            )
        if has_stat:
            tax = _make(cls, "taxonomy", {"id": KW_SCHEME_PREFIX + _ID_UNSAFE.sub("_", method)})
            _make(
                tax,
                "desc",
                text=f"Statistical keywords, keywords.py --method {method}: @n is the rank, "
                "@score the method's score (higher = more relevant)",
            )

    if teater or has_stat:
        profile = _child(header, "profileDesc")
        if profile is None:
            profile = _make(header, "profileDesc")
            _place(header, profile, 2, before=_header_slot(header, "profileDesc"))
        tc = _make(profile, "textClass")
        _place(profile, tc, 3)
        formatted.append((tc, 3))
        if teater:
            kws = _make(tc, "keywords", {"scheme": f"#{TAX_VOCAB}", "resp": "#app-llm-enrich"})
            for label, slot in teater.items():
                attrs = {
                    "type": "teater-category",
                    "ref": " ".join(slot["uris"]),
                    "cert": _fmt_num(round(slot["cert"], 4)) if slot["cert"] is not None else None,
                    "corresp": _corresp(pages.sort(slot["pages"])),
                }
                _make(kws, "term", attrs, text=label)
            report["teater_categories"] = len(teater)
            for lang in ("cs", "en"):
                if not controlled[lang]:
                    continue
                kws = _make(tc, "keywords", {"resp": "#app-llm-enrich", "lang": lang})
                for kw, pb_ids in controlled[lang].items():
                    _make(
                        kws,
                        "term",
                        {"type": "extracted-keyword", "corresp": _corresp(pages.sort(pb_ids))},
                        text=kw,
                    )
                report["controlled_keywords"][lang] = len(controlled[lang])
        if has_stat:
            scheme = f"#{KW_SCHEME_PREFIX}{_ID_UNSAFE.sub('_', method)}"

            def stat_list(kws: list, corresp: Optional[str] = None) -> None:
                el = _make(
                    tc, "keywords", {"scheme": scheme, "resp": "#app-kw", "corresp": corresp}
                )
                for rank, (kw, score) in enumerate(kws, 1):
                    _make(
                        el,
                        "term",
                        {"type": "statistical-keyword", "n": str(rank), "score": _fmt_num(score)},
                        text=str(kw),
                    )

            if stat_document:
                stat_list(stat_document)
                report["statistical_keywords"]["document"] = len(stat_document)
            for pb_id in pages.sort(stat_pages):
                stat_list(stat_pages[pb_id], f"#{pb_id}")
            report["statistical_keywords"]["pages"] = len(stat_pages)

    if categories or teater or has_stat:
        revision = _child(header, "revisionDesc")
        if revision is None:
            revision = _make(header, "revisionDesc")
            _place(header, revision, 2, before=_header_slot(header, "revisionDesc"))
        parts = []
        if categories:
            parts.append(f"{report['page_categories']} page categories")
        if teater:
            parts.append(f"{len(teater)} TEATER/AMČR categories and their keywords")
        if has_stat:
            parts.append(f"{method} keywords for the document and {len(stat_pages)} pages")
        change = _make(
            revision,
            "change",
            {"when": when or _dt.date.today().isoformat(), "who": PROJECTOR, "type": "enriched"},
            text="projected from the document record: " + "; ".join(parts),
        )
        _place(revision, change, 3)

    for el, depth in formatted:
        etree.indent(el, space="  ", level=depth)
    out = _serialise(xml_text, tree)
    errors = validate_xml_text(out, profile="contract")
    if errors:
        raise ProjectionError("projected TEITOK does not validate: " + "; ".join(errors[:5]))
    report["changed"] = out != xml_text
    return out, report


def _has_projection(root, header) -> bool:
    enc = _child(header, "encodingDesc")
    if enc is not None and _child(enc, "classDecl") is not None:
        return True
    profile = _child(header, "profileDesc")
    if profile is not None and _child(profile, "textClass") is not None:
        return True
    return any(
        _local(el.tag) == "pb" and (el.get("ana") or "").startswith(f"#{PCAT_PREFIX}")
        for el in root.iter()
    )


def _serialise(original: str, tree) -> str:
    """The document with its original XML declaration kept verbatim."""
    etree = _etree()
    body = etree.tostring(tree, encoding="unicode")
    decl = ""
    stripped = original.lstrip()
    if stripped.startswith("<?xml"):
        decl = stripped[: stripped.index("?>") + 2] + "\n"
    return decl + body + ("\n" if original.endswith("\n") else "")


# ── statistical keywords ────────────────────────────────────────────────────────────────────


def _page_conllu(text_el) -> Dict[str, str]:
    """``{pb_id: CoNLL-U}`` built from the TEITOK's own tokens, one page each.

    Same shape as UDPipe's output (a range line + one line per syntactic word for a multi-word
    token, ``SpaceAfter=No`` from ``join="right"``), so every keywords.py backend reads a page
    exactly as it reads a whole document."""
    pages: Dict[str, List[str]] = {}
    words: Dict[str, int] = {}
    current = None
    for el in text_el.iter():
        tag = _local(el.tag)
        if tag == "pb" and el.get("id"):
            current = el.get("id")
            continue
        if tag != "tok" or current is None:
            continue
        lines = pages.setdefault(current, [])
        n = words.get(current, 0) + 1
        misc = "SpaceAfter=No" if el.get("join") == "right" else "_"
        dtoks = [d for d in el if _local(d.tag) == "dtok"]
        if dtoks:
            lines.append(f"{n}-{n + len(dtoks) - 1}\t{_tok_form(el)}\t_\t_\t_\t_\t_\t_\t_\t{misc}")
            for i, d in enumerate(dtoks):
                lines.append(
                    f"{n + i}\t{d.get('form') or '_'}\t{d.get('lemma') or '_'}\t"
                    f"{d.get('upos') or '_'}\t_\t_\t_\t_\t_\t_"
                )
            words[current] = n + len(dtoks) - 1
        else:
            lines.append(
                f"{n}\t{_tok_form(el) or '_'}\t{el.get('lemma') or '_'}\t{el.get('upos') or '_'}"
                f"\t_\t_\t_\t_\t_\t{misc}"
            )
            words[current] = n
    return {
        pb: "# sent_id = 1\n" + "\n".join(lines) + "\n\n" for pb, lines in pages.items() if lines
    }


def page_keywords(
    xml_text: str, method: str, num_keywords: int, **options: Any
) -> Dict[str, List[Tuple[str, float]]]:
    """Per-page statistical keywords with keywords.py's own backend (``method``), top
    ``num_keywords`` clamped to 5-20. ``options`` pass through (``lang``, ``max_words``,
    ``keybert_model``, …). Raises ``keywords.KeywordBackendError`` when the backend is absent."""
    import keywords  # noqa: PLC0415  (heavy, and only needed when asked)

    etree = _etree()
    root = etree.fromstring(xml_text.encode("utf-8"))
    text_el = _child(root, "text")
    if text_el is None:
        return {}
    per_page = _page_conllu(text_el)
    if not per_page:
        return {}
    n = clamp_keywords(num_keywords)
    with tempfile.TemporaryDirectory(prefix="teitok_project_") as tmp:
        ids = list(per_page)
        paths = []
        for pb_id in ids:
            path = Path(tmp) / f"{pb_id}.conllu"
            path.write_text(per_page[pb_id], encoding="utf-8")
            paths.append(str(path))
        results = keywords.extract_keywords(paths, method=method, num_keywords=n, **options)
    return {pb_id: list(kws)[:n] for pb_id, kws in zip(ids, results, strict=True) if kws}


def read_keyword_csv(path: Optional[Path]) -> List[Tuple[str, float]]:
    """A keywords.py per-document CSV (``keyword,score``), best first; [] when absent."""
    if not path or not Path(path).is_file():
        return []
    out = []
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                out.append((row["keyword"], float(row["score"])))
            except (KeyError, TypeError, ValueError):
                continue
    return out


def statistical_block(
    xml_text: str,
    method: str,
    num_keywords: int = 20,
    document_csv: Optional[Path] = None,
    **options: Any,
) -> Dict[str, Any]:
    """The ``statistical`` argument of :func:`project_record`: the document list keywords.py
    already wrote (``document_csv``) and per-page lists computed now. A missing backend leaves
    the pages out and says so in ``params``; the document list still projects."""
    n = clamp_keywords(num_keywords)
    params: Dict[str, Any] = {"method": method, "top_n": n}
    params.update({k: v for k, v in options.items() if isinstance(v, (str, int, float, bool))})
    try:
        pages = page_keywords(xml_text, method, n, **options)
    except Exception as exc:  # KeywordBackendError, ImportError: a missing optional backend
        pages = {}
        params["pages_skipped"] = str(exc).splitlines()[0][:200]
    return {
        "method": method,
        "params": params,
        "document": read_keyword_csv(document_csv)[:n],
        "pages": pages,
    }


# ── files and the CLI ───────────────────────────────────────────────────────────────────────


def _atomic_write(path: Path, text: str) -> None:
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def project_file(
    teitok_path: Path,
    record_path: Optional[Path],
    out_path: Path,
    *,
    kw_method: Optional[str] = None,
    kw_csv: Optional[Path] = None,
    num_keywords: int = 20,
    kw_options: Optional[Dict[str, Any]] = None,
    force: bool = False,
    paired_as: Optional[str] = None,
) -> Dict[str, Any]:
    """Project one file; writes ``out_path`` (atomically) only when the text changed."""
    from atrium_document import load_document  # noqa: PLC0415

    xml_text = Path(teitok_path).read_text(encoding="utf-8")
    record = load_document(str(record_path)) if record_path and Path(record_path).is_file() else {}
    statistical = None
    if kw_method:
        statistical = statistical_block(
            xml_text, kw_method, num_keywords, kw_csv, **(kw_options or {})
        )
    out, report = project_record(
        xml_text, record, statistical=statistical, force=force, paired_as=paired_as
    )
    if out != xml_text or Path(out_path) != Path(teitok_path):
        _atomic_write(Path(out_path), out)
    report["teitok"] = str(out_path)
    return report


def _kw_csv_for(kw_per_doc_dir: Optional[Path], doc_id: str) -> Optional[Path]:
    return Path(kw_per_doc_dir) / f"{doc_id}_keywords.csv" if kw_per_doc_dir else None


def _run_directory(args: argparse.Namespace, kw_options: Dict[str, Any]) -> int:
    from api_util.teitok_read import doc_id_from_path  # noqa: PLC0415

    teitok_dir = Path(args.teitok_dir)
    excluded = [Path(p).resolve() for p in args.exclude]
    files = sorted(
        p
        for p in teitok_dir.rglob("*.teitok.xml")
        if not any(ex == p.resolve() or ex in p.resolve().parents for ex in excluded)
    )
    logger = None
    if args.paradata_dir:
        from atrium_paradata import ParadataLogger  # noqa: PLC0415

        logger = ParadataLogger(
            program="nlp-enrich",
            config={
                "script": "teitok_project",
                "teitok_dir": str(teitok_dir),
                "record_dir": str(args.record_dir or ""),
                "kw_method": args.kw_method or "",
                "num_keywords": clamp_keywords(args.num_keywords),
            },
            paradata_dir=str(args.paradata_dir),
            output_types=["teitok_xml"],
        )
    failed = 0
    try:
        for path in files:
            doc_id = doc_id_from_path(path)
            if not args.force and _file_writer_format(path) != WRITER_FORMAT:
                # A format-1 file left by an earlier run (REGENERATE_TEITOK rewrites it): not an
                # error of this opt-in stage, so it must not fail the pipeline.
                print(f"[SKIP] {path.name}: not {WRITER_FORMAT}; regenerate it to project")
                if logger:
                    logger.log_skip(str(path), f"not {WRITER_FORMAT}")
                continue
            record = Path(args.record_dir) / f"{doc_id}.document.json" if args.record_dir else None
            try:
                report = project_file(
                    path,
                    record,
                    path,
                    kw_method=args.kw_method,
                    kw_csv=_kw_csv_for(args.kw_per_doc_dir, doc_id),
                    num_keywords=args.num_keywords,
                    kw_options=kw_options,
                    force=args.force,
                    paired_as=doc_id,
                )
            except ProjectionError as exc:
                failed += 1
                print(f"[FAIL] {path.name}: {exc}", file=sys.stderr)
                if logger:
                    logger.log_skip(str(path), str(exc))
                continue
            print(f"[OK] {path.name}: {json.dumps(_summary(report), ensure_ascii=False)}")
            if logger:
                logger.log_success("teitok_xml", count=1)
    finally:
        if logger:
            logger.finalize(input_total=len(files))
    print(f"TEITOK projection: {len(files) - failed}/{len(files)} documents")
    return 1 if failed else 0


def _file_writer_format(path: Path) -> Optional[str]:
    etree = _etree()
    try:
        return writer_format(etree.parse(str(path)))
    except etree.XMLSyntaxError:
        return None


def _summary(report: Dict[str, Any]) -> Dict[str, Any]:
    keys = ("page_categories", "teater_categories", "controlled_keywords", "statistical_keywords")
    out = {k: report[k] for k in keys}
    if report["unresolved_pages"]:
        out["unresolved_pages"] = report["unresolved_pages"]
    if report["notes"]:
        out["notes"] = report["notes"]
    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--teitok", type=Path, help="One *.teitok.xml to project onto.")
    src.add_argument("--teitok-dir", type=Path, help="Project every *.teitok.xml under this dir.")
    parser.add_argument("--record", type=Path, help="The document record (--teitok).")
    parser.add_argument(
        "--record-dir", type=Path, help="<doc_id>.document.json files (--teitok-dir)."
    )
    parser.add_argument("--out", type=Path, help="Output file (--teitok; default: stdout).")
    parser.add_argument("--in-place", action="store_true", help="Overwrite the TEITOK file(s).")
    parser.add_argument("--exclude", type=Path, action="append", default=[])
    parser.add_argument("--kw-method", choices=["legacy", "yake", "keybert"], default=None)
    parser.add_argument("--kw-csv", type=Path, help="keywords.py per-document CSV (--teitok).")
    parser.add_argument("--kw-per-doc-dir", type=Path, help="KW_PER_DOC_<M> dir (--teitok-dir).")
    parser.add_argument("-n", "--num-keywords", type=int, default=20, help="Clamped to 5-20.")
    parser.add_argument("-l", "--lang", default="cs")
    parser.add_argument("--paradata-dir", type=Path, default=None)
    parser.add_argument("--force", action="store_true", help="Project a non-writer/invalid input.")
    args = parser.parse_args(argv)
    kw_options = {"lang": args.lang}

    if args.teitok_dir:
        if not args.in_place:
            parser.error("--teitok-dir writes in place; pass --in-place")
        return _run_directory(args, kw_options)

    from atrium_document import load_document  # noqa: PLC0415

    xml_text = args.teitok.read_text(encoding="utf-8")
    record = load_document(str(args.record)) if args.record else {}
    statistical = None
    if args.kw_method:
        statistical = statistical_block(
            xml_text, args.kw_method, args.num_keywords, args.kw_csv, **kw_options
        )
    try:
        out, report = project_record(xml_text, record, statistical=statistical, force=args.force)
    except ProjectionError as exc:
        print(f"[FAIL] {args.teitok.name}: {exc}", file=sys.stderr)
        return 1
    target = args.teitok if args.in_place else args.out
    if target is None:
        sys.stdout.write(out)
    else:
        _atomic_write(target, out)
    print(json.dumps(_summary(report), ensure_ascii=False), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
