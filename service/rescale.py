"""
service/rescale.py — coordinate rescaling for TEITOK facsimile XML.

Pure transform behind the ``POST /rescale`` API endpoint. Page by page (issue #38, C):
every ``bbox`` is scaled by the size of the ``<surface>`` of the page it is on, so the
pages of a document may differ in size; boxes are clamped to their page; a ``<change
type="rescaled">`` in ``<revisionDesc>`` records the transform. Regex primitives
(``api_util/bbox_scale.py``, ``api_util/page_boxes.py``), no XML parser: legacy exports that
close ``<name>`` with ``</n>`` are rescaled too (and repaired by default).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from api_util.bbox_scale import detect_source_size, fix_name_close_tags, scale_bbox_coords
from api_util.page_boxes import (
    Surface,
    add_change,
    clamp_box,
    rewrite_page_boxes,
    set_surface_sizes,
    surfaces,
)


class RescaleError(ValueError):
    """Raised when the request is unprocessable (bad target or no source size)."""


class RescaleTooLarge(RescaleError):
    """A page's target size is over ``max_dim`` (MAX_RESCALE_DIM); ``observed`` is its
    largest side in pixels."""

    def __init__(self, message: str, observed: int) -> None:
        super().__init__(message)
        self.observed = observed


def rescale_teitok(
    xml_text: str,
    target_w: Optional[int] = None,
    target_h: Optional[int] = None,
    fix_name_tags: bool = True,
    scale: Optional[float] = None,
    max_dim: Optional[int] = None,
) -> Dict[str, Any]:
    """Rescale a TEITOK document's coordinates to page images of another size.

    ``target_w`` × ``target_h``: every page image has that size (a single-page document,
    or one whose pages all share a size). ``scale``: every page image is ``scale`` times
    its ``<surface>`` -- the form for documents whose pages differ in size. With
    ``max_dim``, a page whose target width or height would be over it raises
    :class:`RescaleTooLarge` (MAX_RESCALE_DIM covers ``scale`` too, atrium-project#53).
    """
    if scale is not None:
        if target_w is not None or target_h is not None:
            raise RescaleError("Give either width and height, or scale -- not both.")
        if not scale > 0:
            raise RescaleError("Scale must be a positive number.")
    elif target_w is None or target_h is None:
        raise RescaleError("Give width and height, or scale.")
    elif target_w <= 0 or target_h <= 0:
        raise RescaleError("Target width and height must be positive integers.")

    sized = [s for s in surfaces(xml_text) if s.width and s.height]
    if sized:
        source_kind = "surface"
        default = sized[0]
        if scale is None and len({(s.width, s.height) for s in sized}) > 1:
            raise RescaleError(
                f"The document's {len(sized)} pages differ in size; width and height would "
                "distort some of them. Give scale instead."
            )
    else:
        src_w, src_h, source_kind = detect_source_size(xml_text)
        if src_w is None or src_h is None:
            raise RescaleError(
                "Cannot determine source size: TEITOK has no <surface> lrx/lry and no bbox "
                "coordinates."
            )
        default = Surface(id="", width=src_w, height=src_h)

    def target(surface: Surface):
        if scale is not None:
            return round(surface.width * scale), round(surface.height * scale)
        return target_w, target_h

    if max_dim is not None:
        biggest = max(max(target(s)) for s in (sized or [default]))
        if biggest > max_dim:
            raise RescaleTooLarge(
                f"The rescaled page images would be {biggest} px on a side; the limit is "
                f"{max_dim} px (MAX_RESCALE_DIM).",
                biggest,
            )

    boxes_rescaled = 0

    def transform(value: str, surface: Optional[Surface]):
        nonlocal boxes_rescaled
        page = surface if surface is not None and surface.width and surface.height else default
        tw, th = target(page)
        if len(value.split()) != 4:
            return value, 0
        boxes_rescaled += 1
        scaled = scale_bbox_coords(value, tw / page.width, th / page.height)
        return clamp_box(scaled, tw, th)

    out, clamped = rewrite_page_boxes(xml_text, transform)
    out = set_surface_sizes(out, target)

    name_tags_fixed = 0
    if fix_name_tags:
        out, name_tags_fixed = fix_name_close_tags(out)

    first_w, first_h = target(default)
    how = f"scale {scale:g}" if scale is not None else f"{target_w}x{target_h}"
    out = add_change(
        out,
        "rescaled",
        f"coordinates rescaled to page images of {how}"
        + (f"; {clamped} coordinate(s) clamped to the page" if clamped else ""),
    )
    return {
        "teitok_xml": out,
        "source": {"width": default.width, "height": default.height},
        "source_kind": source_kind,
        "target": {"width": first_w, "height": first_h},
        "scale": {
            "sx": round(first_w / default.width, 6),
            "sy": round(first_h / default.height, 6),
        },
        "pages": [
            {
                "surface": s.id,
                "source": {"width": s.width, "height": s.height},
                "target": dict(zip(("width", "height"), target(s), strict=True)),
            }
            for s in sized
        ],
        "boxes_rescaled": boxes_rescaled,
        "clamped": clamped,
        "name_tags_fixed": name_tags_fixed,
    }
