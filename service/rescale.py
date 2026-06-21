"""
service/rescale.py — coordinate rescaling for TEITOK facsimile XML.

Pure, dependency-free transform behind the ``POST /rescale`` API endpoint
(issue #8): given a single-page TEITOK document and a target page-image size,
rescale every facsimile coordinate — the hOCR-style ``bbox`` boxes and the
``<surface>`` ``lrx``/``lry`` page extents — so the annotations line up exactly
on top of an image of that size.

It also repairs, by default, the non-well-formed TEITOK named-entity quirk where
elements open with ``<name>`` but close with ``</n>`` (see
``data_samples/TEITOK/``): those stray ``</n>`` closings are rewritten to
``</name>`` so the result is valid XML.

Why a regex rewrite and not ElementTree
---------------------------------------
Because of the ``<name>…</n>`` quirk above, ``xml.etree.ElementTree`` raises
``ParseError`` on real exports — which is exactly why the ``fix_teitok_bboxes.py``
CLI silently no-ops on such files. We therefore do a surgical, text-level rewrite
that only touches the numeric coordinate values (and, optionally, the malformed
closing tags) and leaves every other byte of markup intact.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Tuple

# bbox="x1 y1 x2 y2" — four hOCR-style coordinates (x's scale by sx, y's by sy)
_BBOX_RE = re.compile(r'\bbbox\s*=\s*"([^"]*)"')
# <surface ...> — facsimile surface opening tag; carries lrx/lry page extents
_SURFACE_RE = re.compile(r"<surface\b[^>]*>", re.IGNORECASE)
_LRX_RE = re.compile(r'(\blrx\s*=\s*")([^"]*)(")')
_LRY_RE = re.compile(r'(\blry\s*=\s*")([^"]*)(")')
# Malformed named-entity close tag: TEITOK opens <name> but closes </n>.
# Note: this never matches inside a well-formed </name> (the char after "</n"
# there is "a", not ">"), so the repair is a safe no-op on clean documents.
_NAME_CLOSE_RE = re.compile(r"</n\s*>")


class RescaleError(ValueError):
    """Raised when the request is unprocessable (bad target or no source size)."""


def _scale_coords(value: str, sx: float, sy: float) -> str:
    """Scale a ``"x1 y1 x2 y2"`` bbox string; return it unchanged if not 4 numbers."""
    parts = value.split()
    if len(parts) != 4:
        return value
    try:
        x1, y1, x2, y2 = (float(p) for p in parts)
    except ValueError:
        return value
    return f"{round(x1 * sx)} {round(y1 * sy)} {round(x2 * sx)} {round(y2 * sy)}"


def fix_name_close_tags(xml_text: str) -> Tuple[str, int]:
    """Repair the TEITOK ``<name>…</n>`` quirk, closing entities with ``</name>``.

    Returns ``(fixed_text, count)``. Safe on already-valid documents: clean
    exports contain no stray ``</n>`` (the generator emits ``</name>``), so the
    count is ``0`` and the text is returned unchanged.
    """
    return _NAME_CLOSE_RE.subn("</name>", xml_text)


def detect_source_size(xml_text: str) -> Tuple[int, int, str]:
    """Return ``(width, height, source_kind)`` of the document's coordinate space.

    Preference order:
      1. the first ``<surface>`` declaring both ``lrx`` and ``lry``
         (``source_kind="surface"``) — the authoritative page extent;
      2. otherwise the maximum extent of all ``bbox`` boxes
         (``source_kind="bbox-extent"``) — an approximate fallback.

    Raises :class:`RescaleError` if neither is available.
    """
    for surf in _SURFACE_RE.findall(xml_text):
        mx = _LRX_RE.search(surf)
        my = _LRY_RE.search(surf)
        if mx and my:
            try:
                w = int(round(float(mx.group(2))))
                h = int(round(float(my.group(2))))
            except ValueError:
                continue
            if w > 0 and h > 0:
                return w, h, "surface"

    max_x = max_y = 0.0
    for raw in _BBOX_RE.findall(xml_text):
        parts = raw.split()
        if len(parts) != 4:
            continue
        try:
            _, _, x2, y2 = (float(p) for p in parts)
        except ValueError:
            continue
        max_x = max(max_x, x2)
        max_y = max(max_y, y2)
    if max_x > 0 and max_y > 0:
        return int(round(max_x)), int(round(max_y)), "bbox-extent"

    raise RescaleError(
        "Cannot determine source size: TEITOK has no <surface> lrx/lry and no bbox coordinates."
    )


def rescale_teitok(
    xml_text: str,
    target_w: int,
    target_h: int,
    fix_name_tags: bool = True,
) -> Dict[str, Any]:
    """Rescale a single-page TEITOK document to a ``target_w`` × ``target_h`` image.

    The source coordinate space is taken from the document itself (see
    :func:`detect_source_size`); a uniform ``sx``/``sy`` is applied to every
    ``bbox`` and every ``<surface>`` extent is set to the target size. The
    function assumes a single page — for multi-surface documents the first
    surface's extent defines the scale and all surfaces are set to the target.

    When ``fix_name_tags`` is true (the default) the malformed ``<name>…</n>``
    closings are also repaired to ``</name>`` (see :func:`fix_name_close_tags`).

    Returns a dict with the rewritten ``teitok_xml`` plus ``source``, ``target``,
    ``scale``, ``source_kind``, the ``boxes_rescaled`` count and the
    ``name_tags_fixed`` count.
    """
    if target_w <= 0 or target_h <= 0:
        raise RescaleError("Target width and height must be positive integers.")

    src_w, src_h, source_kind = detect_source_size(xml_text)
    sx = target_w / src_w
    sy = target_h / src_h

    boxes_rescaled = 0

    def _bbox_sub(m: "re.Match[str]") -> str:
        nonlocal boxes_rescaled
        value = m.group(1)
        if len(value.split()) == 4:
            boxes_rescaled += 1
        return f'bbox="{_scale_coords(value, sx, sy)}"'

    out = _BBOX_RE.sub(_bbox_sub, xml_text)

    def _surface_sub(m: "re.Match[str]") -> str:
        tag = _LRX_RE.sub(rf"\g<1>{target_w}\g<3>", m.group(0))
        tag = _LRY_RE.sub(rf"\g<1>{target_h}\g<3>", tag)
        return tag

    out = _SURFACE_RE.sub(_surface_sub, out)

    name_tags_fixed = 0
    if fix_name_tags:
        out, name_tags_fixed = fix_name_close_tags(out)

    return {
        "teitok_xml": out,
        "source": {"width": src_w, "height": src_h},
        "source_kind": source_kind,
        "target": {"width": target_w, "height": target_h},
        "scale": {"sx": round(sx, 6), "sy": round(sy, 6)},
        "boxes_rescaled": boxes_rescaled,
        "name_tags_fixed": name_tags_fixed,
    }
