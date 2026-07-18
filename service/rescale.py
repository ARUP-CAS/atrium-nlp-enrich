"""
service/rescale.py — coordinate rescaling for TEITOK facsimile XML.

Pure transform behind the ``POST /rescale`` API endpoint.
Delegates heavy lifting to api_util.bbox_scale shared primitives.
"""

from __future__ import annotations

import re
from typing import Any, Dict

from api_util.bbox_scale import (
    BBOX_RE,
    LRX_RE,
    LRY_RE,
    SURFACE_RE,
    detect_source_size,
    fix_name_close_tags,
    scale_bbox_coords,
)


class RescaleError(ValueError):
    """Raised when the request is unprocessable (bad target or no source size)."""


def rescale_teitok(
    xml_text: str,
    target_w: int,
    target_h: int,
    fix_name_tags: bool = True,
) -> Dict[str, Any]:
    """Rescale a single-page TEITOK document to a ``target_w`` × ``target_h`` image."""
    if target_w <= 0 or target_h <= 0:
        raise RescaleError("Target width and height must be positive integers.")

    src_w, src_h, source_kind = detect_source_size(xml_text)
    if src_w is None or src_h is None:
        raise RescaleError(
            "Cannot determine source size: TEITOK has no <surface> lrx/lry and no bbox coordinates."
        )

    sx = target_w / src_w
    sy = target_h / src_h

    boxes_rescaled = 0

    def _bbox_sub(m: "re.Match[str]") -> str:
        nonlocal boxes_rescaled
        value = m.group(1)
        if len(value.split()) == 4:
            boxes_rescaled += 1
        return f'bbox="{scale_bbox_coords(value, sx, sy)}"'

    out = BBOX_RE.sub(_bbox_sub, xml_text)

    def _surface_sub(m: "re.Match[str]") -> str:
        tag = LRX_RE.sub(rf"\g<1>{target_w}\g<3>", m.group(0))
        tag = LRY_RE.sub(rf"\g<1>{target_h}\g<3>", tag)
        return tag

    out = SURFACE_RE.sub(_surface_sub, out)

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
