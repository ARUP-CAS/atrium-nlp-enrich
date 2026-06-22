#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

from api_util.bbox_scale import (
    detect_source_size,
    dpi_scale,
    fix_name_close_tags,
    rewrite_bboxes,
    scale_bbox_coords,
    set_surface_extent,
)


def process_teitok_file(file_path, args, sx, sy):
    """Parses an existing TEITOK XML file and rewrites updated bbox fields using regex primitives."""
    print(f"Processing: {file_path}")

    with open(file_path, 'r', encoding='utf-8') as f:
        xml_text = f.read()

    xml_text = fix_name_close_tags(xml_text)

    # Maintain the offset sign logic per the TODO requirement
    def scale_fn(bbox_str):
        return scale_bbox_coords(bbox_str, sx, sy, dx=-args.dx, dy=-args.dy)

    xml_text = rewrite_bboxes(xml_text, scale_fn)

    # Process <surface> scaling
    w, h = detect_source_size(xml_text)
    if w is not None and h is not None:
        xml_text = set_surface_extent(xml_text, round(w * sx), round(h * sy))

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(xml_text)


def main():
    parser = argparse.ArgumentParser(
        description="Retroactively adjust bounding box displacements in generated TEITOK XML files."
    )
    parser.add_argument(
        "-i", "--input", required=True, help="Path to TEITOK XML file or directory of files."
    )
    parser.add_argument(
        "--dx",
        type=float,
        default=0.0,
        help="Horizontal shift adjustment (e.g. -297 to remove left margin).",
    )
    parser.add_argument(
        "--dy",
        type=float,
        default=0.0,
        help="Vertical shift adjustment (e.g. -80 to remove top margin).",
    )
    parser.add_argument(
        "--sx",
        type=float,
        default=1.0,
        help="Optional horizontal scaling scale-factor multiplier ratio.",
    )
    parser.add_argument(
        "--sy",
        type=float,
        default=1.0,
        help="Optional vertical scaling scale-factor multiplier ratio.",
    )
    parser.add_argument(
        "--dpi",
        type=float,
        default=None,
        help="Target DPI to automatically calculate scale factor.",
    )
    parser.add_argument(
        "--alto-dpi",
        type=float,
        default=None,
        help="Source DPI of ALTO pixel coordinates (used if unit is pixel).",
    )
    parser.add_argument(
        "--unit", type=str, default="pixel", help="ALTO MeasurementUnit (pixel, mm10, inch1200)."
    )

    args = parser.parse_args()

    # Calculate scales via unified primitive (replaces the old if/elif blocks)
    sx, sy = dpi_scale(args.unit, args.dpi, args.alto_dpi) if args.dpi else (args.sx, args.sy)

    input_path = Path(args.input)

    # Dispatch to either single file or directory batch processing
    if input_path.is_file():
        process_teitok_file(input_path, args, sx, sy)
    elif input_path.is_dir():
        for xml_file in input_path.glob("*.xml"):
            process_teitok_file(xml_file, args, sx, sy)
    else:
        print(f"Error: Specified path target {args.input} does not exist.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
