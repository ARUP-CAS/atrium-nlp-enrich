#!/usr/bin/env python3
import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def adjust_bbox_string(bbox_str, dx, dy, sx=1.0, sy=1.0):
    """Parses a TEITOK hOCR-style bbox string 'x1 y1 x2 y2' and recalculates position."""
    try:
        parts = [float(c) for c in bbox_str.strip().split()]
        if len(parts) != 4:
            return bbox_str

        x1, y1, x2, y2 = parts

        # Apply displacement translation offsets and scaling factor transformations
        x1_new = round((x1 + dx) * sx)
        y1_new = round((y1 + dy) * sy)
        x2_new = round((x2 + dx) * sx)
        y2_new = round((y2 + dy) * sy)

        return f"{x1_new} {y1_new} {x2_new} {y2_new}"
    except (ValueError, TypeError):
        return bbox_str


def process_teitok_file(file_path, dx, dy, sx, sy):
    """Parses an existing TEITOK XML file and rewrites updated bbox fields."""
    print(f"Processing: {file_path}")

    # Read raw text to handle unique non-standard markup like xmlnsoff cleanly
    with open(file_path, "r", encoding="utf-8") as f:
        xml_content = f.read()

    # Temporary placeholder modification to preserve custom attributes during standard parsing
    has_xmlnsoff = "xmlnsoff=" in xml_content
    if has_xmlnsoff:
        xml_content = xml_content.replace("xmlnsoff=", "xmlns=")

    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as e:
        print(f"  [Error] Failed parsing XML for {file_path}: {e}", file=sys.stderr)
        return False

    mutated = False

    # Traverse all elements containing bbox properties (tok, lb, div, figure)
    for elem in root.iter():
        if "bbox" in elem.attrib:
            old_bbox = elem.attrib["bbox"]
            new_bbox = adjust_bbox_string(old_bbox, dx, dy, sx, sy)
            if old_bbox != new_bbox:
                elem.attrib["bbox"] = new_bbox
                mutated = True

    if mutated:
        # Export back into raw string
        out_bytes = ET.tostring(root, encoding="utf-8")
        out_str = out_bytes.decode("utf-8")

        # Restore the specific naming format requested by TEITOK system requirements
        if has_xmlnsoff:
            out_str = out_str.replace("xmlns=", "xmlnsoff=")

        # Write clean, updated header records back out safely
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(out_str)
        print("  [Success] Patched bounding box offsets.")
    else:
        print("  [Info] No modifications needed.")
    return True


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

    if args.dpi:
        if args.unit == "inch1200":
            args.sx = args.sy = args.dpi / 1200.0
        elif args.unit == "mm10":
            args.sx = args.sy = args.dpi / 254.0
        elif args.unit == "pixel":
            a_dpi = args.alto_dpi if args.alto_dpi else args.dpi
            args.sx = args.sy = args.dpi / a_dpi

    input_path = Path(args.input)

    if input_path.is_file():
        process_teitok_file(input_path, args.dx, args.dy, args.sx, args.sy)
    elif input_path.is_dir():
        for xml_file in input_path.glob("*.xml"):
            process_teitok_file(xml_file, args.dx, args.dy, args.sx, args.sy)
    else:
        print(f"Error: Specified path target {args.input} does not exist.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
