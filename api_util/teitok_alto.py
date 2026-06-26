"""teitok_alto.py — Produce TEITOK XML from a NER-enriched CoNLL-U + ALTO file."""

import collections
import datetime
import difflib
import struct
import sys
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape

from api_util.bbox_scale import dpi_scale, scale_bbox_coords

_CNEC_TO_CONLL = {
    "p": "PER",
    "p_": "PER",
    "P": "PER",
    "pf": "PER",
    "ps": "PER",
    "pm": "PER",
    "ph": "PER",
    "pc": "PER",
    "pd": "PER",
    "pp": "PER",
    "i": "ORG",
    "i_": "ORG",
    "I": "ORG",
    "ia": "ORG",
    "if": "ORG",
    "io": "ORG",
    "ic": "ORG",
    "g": "LOC",
    "G": "LOC",
    "g_": "LOC",
    "gu": "LOC",
    "gl": "LOC",
    "gq": "LOC",
    "gr": "LOC",
    "gs": "LOC",
    "gc": "LOC",
    "gt": "LOC",
    "gh": "LOC",
}

_IMAGE_EXTS = (".png", ".PNG", ".jpg", ".JPG", ".jpeg", ".JPEG", ".tiff", ".TIFF", ".tif", ".TIF")


# Local wrapper to avoid rewriting complex formatting clusters in the loop
def _scale_bbox_str(x1, y1, x2, y2, sx, sy, dx=0, dy=0):
    return scale_bbox_coords(f"{x1} {y1} {x2} {y2}", sx, sy, dx, dy)


def _scale_bbox_tuple(bbox_tuple, sx, sy, dx=0, dy=0):
    x1, y1, x2, y2 = bbox_tuple
    return scale_bbox_coords(f"{x1} {y1} {x2} {y2}", sx, sy, dx, dy)


def _build_page_scale_map(
    alto_pages, image_dir, doc_id, measurement_unit="pixel", dpi=None, alto_dpi=None
):
    scale_map = {}
    for pg in alto_pages:
        idx = pg["idx"]
        dx = pg.get("ps_hpos", 0)
        dy = pg.get("ps_vpos", 0)
        try:
            alto_w = float(pg.get("width") or 0)
            alto_h = float(pg.get("height") or 0)
        except (ValueError, TypeError):
            alto_w = alto_h = 0.0

        img_dims = None
        img_ext = ".png"  # Default fallback

        img_path = _find_page_image(image_dir, doc_id, idx)
        if img_path:
            img_dims = _read_image_dimensions(img_path)
            img_ext = img_path.suffix  # Dynamically capture extension

        # Tier 1: Companion image present
        if img_dims and alto_w > 0 and alto_h > 0:
            sx = img_dims[0] / alto_w
            sy = img_dims[1] / alto_h
            scale_map[idx] = (sx, sy, img_dims[0], img_dims[1], dx, dy, img_ext)

        # Tier 2: User-set DPI -> math delegated to bbox_scale
        elif dpi and alto_w > 0 and alto_h > 0:
            sx, sy = dpi_scale(measurement_unit, dpi, alto_dpi)
            scale_map[idx] = (sx, sy, round(alto_w * sx), round(alto_h * sy), dx, dy, img_ext)

        # Tier 3: Fallback
        else:
            scale_map[idx] = (
                1.0,
                1.0,
                int(alto_w) if alto_w else None,
                int(alto_h) if alto_h else None,
                dx,
                dy,
                img_ext,
            )
    return scale_map


def _attr(value: str) -> str:
    return escape(value, {'"': "&quot;"})


def _unit_per_inch(unit):
    return {"inch1200": 1200, "mm10": 254}.get(unit, None)


def _read_image_dimensions(path):
    path = Path(path)
    if not path.exists():
        return None
    try:
        with open(path, "rb") as fh:
            header = fh.read(26)
            if header[:8] == b"\x89PNG\r\n\x1a\n":
                w = struct.unpack(">I", header[16:20])[0]
                h = struct.unpack(">I", header[20:24])[0]
                return (w, h)
            if header[:2] == b"\xff\xd8":
                fh.seek(2)
                while True:
                    marker = fh.read(2)
                    if len(marker) < 2:
                        break
                    if marker[0] != 0xFF:
                        break
                    seg_len = struct.unpack(">H", fh.read(2))[0]
                    if marker[1] in (
                        0xC0,
                        0xC1,
                        0xC2,
                        0xC3,
                        0xC5,
                        0xC6,
                        0xC7,
                        0xC9,
                        0xCA,
                        0xCB,
                        0xCD,
                        0xCE,
                        0xCF,
                    ):
                        fh.read(1)
                        h = struct.unpack(">H", fh.read(2))[0]
                        w = struct.unpack(">H", fh.read(2))[0]
                        return (w, h)
                    fh.read(seg_len - 2)
                return None
            if header[:2] in (b"II", b"MM"):
                endian = "<" if header[:2] == b"II" else ">"
                fh.seek(4)
                ifd_offset = struct.unpack(endian + "I", fh.read(4))[0]
                fh.seek(ifd_offset)
                num_entries = struct.unpack(endian + "H", fh.read(2))[0]
                w = h = None
                for _ in range(num_entries):
                    tag = struct.unpack(endian + "H", fh.read(2))[0]
                    typ = struct.unpack(endian + "H", fh.read(2))[0]
                    fh.read(4)
                    val_bytes = fh.read(4)
                    fmt = endian + ("H" if typ == 3 else "I")
                    val = struct.unpack(fmt, val_bytes[: struct.calcsize(fmt)])[0]
                    if tag == 256:
                        w = val
                    elif tag == 257:
                        h = val
                    if w is not None and h is not None:
                        return (w, h)
        return None
    except Exception:
        return None


def _find_page_image(image_dir, doc_id, page_idx):
    if not image_dir:
        return None
    base = Path(image_dir)
    for ext in _IMAGE_EXTS:
        candidate = base / f"{doc_id}-{page_idx}{ext}"
        if candidate.exists():
            return candidate
    return None


def _parse_alto(alto_path):
    alto_strings = []
    alto_pages = []
    alto_graphics = []
    alto_blocks = {}
    alto_meta = {
        "source_image": "",
        "ocr_software": "",
        "ocr_version": "",
        "ocr_date": "",
        "measurement_unit": "pixel",
    }

    if not (alto_path and Path(alto_path).exists()):
        return alto_strings, alto_pages, alto_graphics, alto_blocks, alto_meta

    try:
        tree = ET.parse(alto_path)
        root = tree.getroot()
        ns_uri = ""
        if root.tag.startswith("{"):
            ns_uri = root.tag[1 : root.tag.index("}")]

        def _tag(local):
            return f"{{{ns_uri}}}{local}" if ns_uri else local

        for desc in root.iter(_tag("Description")):
            for img_info in desc.iter(_tag("fileName")):
                if img_info.text:
                    alto_meta["source_image"] = img_info.text.strip()
            for mu in desc.iter(_tag("MeasurementUnit")):
                if mu.text:
                    alto_meta["measurement_unit"] = mu.text.strip()
            for ocr in desc.iter(_tag("ocrProcessingStep")):
                for dt in ocr.iter(_tag("processingDateTime")):
                    if dt.text:
                        alto_meta["ocr_date"] = dt.text.strip()
                for sw in ocr.iter(_tag("softwareName")):
                    if sw.text:
                        alto_meta["ocr_software"] = sw.text.strip()
                for swv in ocr.iter(_tag("softwareVersion")):
                    if swv.text:
                        alto_meta["ocr_version"] = swv.text.strip()

        for page_idx, page in enumerate(root.iter(_tag("Page")), start=1):
            page_w_str = page.get("WIDTH", "") or ""
            page_h_str = page.get("HEIGHT", "") or ""
            ps_hpos = ps_vpos = ps_w = ps_h = 0
            for ps in page.iter(_tag("PrintSpace")):
                try:
                    ps_hpos = int(float(ps.get("HPOS", 0) or 0))
                    ps_vpos = int(float(ps.get("VPOS", 0) or 0))
                    ps_w = int(float(ps.get("WIDTH", 0) or 0))
                    ps_h = int(float(ps.get("HEIGHT", 0) or 0))
                except (ValueError, TypeError):
                    pass
                break
            alto_pages.append(
                {
                    "id": page.get("ID", f"Page{page_idx}"),
                    "width": page_w_str,
                    "height": page_h_str,
                    "idx": page_idx,
                    "ps_hpos": ps_hpos,
                    "ps_vpos": ps_vpos,
                    "ps_width": ps_w,
                    "ps_height": ps_h,
                }
            )
            for block in page.iter(_tag("TextBlock")):
                block_id = block.get("ID", "")
                try:
                    b_hpos = float(block.get("HPOS", 0) or 0)
                    b_vpos = float(block.get("VPOS", 0) or 0)
                    b_width = float(block.get("WIDTH", 0) or 0)
                    b_height = float(block.get("HEIGHT", 0) or 0)
                    alto_blocks[block_id] = (
                        f"{int(b_hpos)} {int(b_vpos)} "
                        f"{int(b_hpos + b_width)} {int(b_vpos + b_height)}"
                    )
                except (ValueError, TypeError):
                    pass
                for line in block.iter(_tag("TextLine")):
                    line_id = line.get("ID", "")
                    try:
                        l_h = float(line.get("HPOS", 0) or 0)
                        l_v = float(line.get("VPOS", 0) or 0)
                        l_w = float(line.get("WIDTH", 0) or 0)
                        l_e = float(line.get("HEIGHT", 0) or 0)
                        line_bbox = f"{int(l_h)} {int(l_v)} {int(l_h + l_w)} {int(l_v + l_e)}"
                    except (ValueError, TypeError):
                        line_bbox = ""
                    for string in line.iter(_tag("String")):
                        content = string.get("CONTENT", "")
                        if not content:
                            continue
                        try:
                            hpos = float(string.get("HPOS", 0) or 0)
                            vpos = float(string.get("VPOS", 0) or 0)
                            width = float(string.get("WIDTH", 0) or 0)
                            height = float(string.get("HEIGHT", 0) or 0)
                            alto_strings.append(
                                {
                                    "content": content,
                                    "left": int(hpos),
                                    "top": int(vpos),
                                    "right": int(hpos + width),
                                    "bottom": int(vpos + height),
                                    "page_idx": page_idx,
                                    "block_id": block_id,
                                    "line_id": line_id,
                                    "line_bbox": line_bbox,
                                }
                            )
                        except (ValueError, TypeError):
                            pass
            for gtag in ("Illustration", "GraphicalElement"):
                for graphic in page.iter(_tag(gtag)):
                    try:
                        hpos = float(graphic.get("HPOS", 0) or 0)
                        vpos = float(graphic.get("VPOS", 0) or 0)
                        width = float(graphic.get("WIDTH", 0) or 0)
                        height = float(graphic.get("HEIGHT", 0) or 0)
                        alto_graphics.append(
                            {
                                "type": gtag,
                                "id": graphic.get("ID", ""),
                                "bbox": (
                                    int(hpos),
                                    int(vpos),
                                    int(hpos + width),
                                    int(vpos + height),
                                ),
                                "page_idx": page_idx,
                            }
                        )
                    except (ValueError, TypeError):
                        pass
    except Exception as exc:
        print(f"  [Warn] Failed to parse ALTO {alto_path}: {exc}", file=sys.stderr)
    return alto_strings, alto_pages, alto_graphics, alto_blocks, alto_meta


def _align_tokens_to_alto(tokens, alto_strings):
    if not alto_strings or not tokens:
        return [None] * len(tokens)

    def norm(s):
        return unicodedata.normalize("NFC", s).lower()

    alto_char_list = []
    alto_char_to_idx = []
    for idx, s in enumerate(alto_strings):
        for ch in norm(s["content"]):
            if ch.strip():
                alto_char_list.append(ch)
                alto_char_to_idx.append(idx)

    CHUNK_SIZE = 5000
    bboxes = [None] * len(tokens)
    tok_char_list = []
    tok_char_to_tok_idx = []
    for t_idx, tok in enumerate(tokens):
        for ch in norm(tok.get("form", "")):
            if ch.strip():
                tok_char_list.append(ch)
                tok_char_to_tok_idx.append(t_idx)

    tok_str = "".join(tok_char_list)
    alto_str = "".join(alto_char_list)
    tok_to_alto_indices = collections.defaultdict(list)

    for i in range(0, len(tok_str), CHUNK_SIZE):
        tok_chunk = tok_str[i : i + CHUNK_SIZE]
        window_start = max(0, i - 1000)
        window_end = min(len(alto_str), i + CHUNK_SIZE + 1000)
        alto_chunk = alto_str[window_start:window_end]
        sm = difflib.SequenceMatcher(None, tok_chunk, alto_chunk, autojunk=False)
        for block in sm.get_matching_blocks():
            i_chunk, j_chunk, n = block
            for k in range(n):
                global_t_idx = i + i_chunk + k
                global_a_idx = window_start + j_chunk + k
                if global_t_idx < len(tok_char_to_tok_idx) and global_a_idx < len(alto_char_to_idx):
                    t_idx = tok_char_to_tok_idx[global_t_idx]
                    a_idx = alto_char_to_idx[global_a_idx]
                    tok_to_alto_indices[t_idx].append(a_idx)

    for t_idx in range(len(tokens)):
        a_indices = tok_to_alto_indices.get(t_idx)
        if not a_indices:
            continue
        first_a = alto_strings[a_indices[0]]
        page_indices = set(alto_strings[a]["page_idx"] for a in a_indices)
        if len(page_indices) > 1:
            form = tokens[t_idx].get("form", "?")
            print(
                f"  [Warn] Token '{form}' spans pages {sorted(page_indices)}; "
                "using first matched page for bbox assignment.",
                file=sys.stderr,
            )
        bboxes[t_idx] = {
            "left": min(alto_strings[a]["left"] for a in a_indices),
            "top": min(alto_strings[a]["top"] for a in a_indices),
            "right": max(alto_strings[a]["right"] for a in a_indices),
            "bottom": max(alto_strings[a]["bottom"] for a in a_indices),
            "page_idx": first_a["page_idx"],
            "block_id": first_a["block_id"],
            "line_id": first_a["line_id"],
            "line_bbox": first_a["line_bbox"],
        }
    return bboxes


def _bio_to_code(ner_tag):
    if not ner_tag or ner_tag in ("O", "_"):
        return ""
    primary = ner_tag.split("|")[0]
    return primary[2:] if primary.startswith(("B-", "I-")) else ""


def _group_ner_spans(tokens):
    groups = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        ner = tok.get("ner", "")
        if ner and ner not in ("O", "_") and ner.startswith("B-"):
            span = [tok]
            i += 1
            while i < len(tokens):
                nxt = tokens[i].get("ner", "")
                if nxt and nxt.startswith("I-"):
                    span.append(tokens[i])
                    i += 1
                else:
                    break
            groups.append({"kind": "name", "tokens": span, "code": _bio_to_code(ner)})
        else:
            groups.append({"kind": "plain", "tokens": [tok]})
            i += 1
    return groups


def _parse_misc(misc_str):
    if misc_str == "_" or not misc_str:
        return {}
    misc = {}
    for item in misc_str.split("|"):
        if "=" in item:
            k, v = item.split("=", 1)
            misc[k] = v
        else:
            misc[item] = "Yes"
    return misc


def _tok_xml(tok, id_map, sx=1.0, sy=1.0, dx=0, dy=0, indent=10):
    wid = id_map.get(tok["id"], tok["id"])
    head_ref = None
    if tok.get("head") and tok["head"] != "0":
        head_ref = id_map.get(tok["head"], tok["head"])
    tok_type = "pc" if tok.get("upos") == "PUNCT" else "w"
    attrs = [f'id="{wid}"', f'type="{tok_type}"']
    if tok.get("lemma") and tok["lemma"] != "_":
        attrs.append(f'lemma="{_attr(tok["lemma"])}"')
    if tok.get("upos") and tok["upos"] != "_":
        attrs.append(f'upos="{_attr(tok["upos"])}"')
    if tok.get("xpos") and tok["xpos"] != "_":
        attrs.append(f'xpos="{_attr(tok["xpos"])}"')
    if tok.get("feats") and tok["feats"] != "_":
        attrs.append(f'feats="{_attr(tok["feats"])}"')
    if head_ref is not None:
        attrs.append(f'head="{head_ref}"')
    if tok.get("deprel") and tok["deprel"] != "_":
        attrs.append(f'deprel="{_attr(tok["deprel"])}"')
    if not tok.get("space_after", True):
        attrs.append('join="right"')
    bbox = tok.get("_bbox")
    if bbox:
        attrs.append(
            f'bbox="{_scale_bbox_str(bbox["left"], bbox["top"], bbox["right"], bbox["bottom"], sx, sy, dx, dy)}"'
        )
    pad = " " * indent
    return f"{pad}<tok {' '.join(attrs)}>{escape(tok['form'])}</tok>\n"


def write_teitok_merged(
    conllu_path,
    teitok_path,
    alto_path=None,
    doc_id=None,
    model_udpipe=None,
    model_nametag=None,
    image_dir=None,
    dpi=None,
    alto_dpi=None,
):
    alto_strings, alto_pages, alto_graphics, alto_blocks, alto_meta = _parse_alto(alto_path)

    _doc_id = doc_id or Path(teitok_path).stem
    if not alto_strings:
        print(
            f"  [TEITOK] No ALTO input for {_doc_id}; producing text-only XML without bboxes.",
            file=sys.stderr,
        )

    effective_image_dir = image_dir
    if not effective_image_dir and alto_path:
        candidate = Path(alto_path).parent
        if any(candidate.glob("*.png")) or any(candidate.glob("*.jpg")):
            effective_image_dir = candidate

    scale_map = _build_page_scale_map(
        alto_pages,
        effective_image_dir,
        _doc_id,
        measurement_unit=alto_meta.get("measurement_unit", "pixel"),
        dpi=dpi,
        alto_dpi=alto_dpi,
    )

    sentences = []
    current_tok = []
    sent_id = sent_text = None
    conllu_meta = {}
    pending_page_break = False

    try:
        with open(conllu_path, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.rstrip("\n")
                if line.startswith("# generator ="):
                    conllu_meta["generator"] = line.split("=", 1)[1].strip()
                if line.startswith("# udpipe_model ="):
                    conllu_meta["udpipe_model"] = line.split("=", 1)[1].strip()
                if line.startswith("# udpipe_model_licence ="):
                    conllu_meta["udpipe_model_licence"] = line.split("=", 1)[1].strip()
                if line.strip() == "# page_break = true":
                    pending_page_break = True
                    continue
                if line.startswith("# sent_id"):
                    sent_id = line.split("=", 1)[1].strip() if "=" in line else None
                    continue
                if line.startswith("# text"):
                    sent_text = line.split("=", 1)[1].strip() if "=" in line else None
                    continue
                if not line.strip() or line.startswith("#"):
                    if not line.strip() and current_tok:
                        sentences.append(
                            {
                                "id": sent_id,
                                "text": sent_text,
                                "tokens": current_tok,
                                "page_break": pending_page_break,
                            }
                        )
                        current_tok = []
                        pending_page_break = False
                    continue
                cols = line.split("\t")
                if len(cols) < 10 or "-" in cols[0] or "." in cols[0]:
                    continue
                misc = _parse_misc(cols[9])
                current_tok.append(
                    {
                        "id": cols[0],
                        "form": cols[1],
                        "lemma": cols[2],
                        "upos": cols[3],
                        "xpos": cols[4],
                        "feats": cols[5],
                        "head": cols[6],
                        "deprel": cols[7],
                        "space_after": misc.get("SpaceAfter", "Yes") != "No",
                        "ner": misc.get("NER", ""),
                    }
                )
        if current_tok:
            sentences.append(
                {
                    "id": sent_id,
                    "text": sent_text,
                    "tokens": current_tok,
                    "page_break": pending_page_break,
                }
            )
    except Exception as exc:
        print(f"  [Error] Reading CoNLL-U {conllu_path}: {exc}", file=sys.stderr)
        return False

    all_tokens = [tok for sent in sentences for tok in sent["tokens"]]
    all_bboxes = _align_tokens_to_alto(all_tokens, alto_strings)
    tok_ptr = 0
    for sent in sentences:
        for tok in sent["tokens"]:
            tok["_bbox"] = all_bboxes[tok_ptr]
            tok_ptr += 1

    matched = sum(1 for b in all_bboxes if b is not None)
    print(f"  [ALTO] matched {matched}/{len(all_tokens)} tokens to ALTO bboxes")

    doc_id_safe = escape(_doc_id)
    alto_filename = Path(alto_path).name if alto_path else "Unknown"
    current_date = datetime.date.today().isoformat()

    try:
        with open(teitok_path, "w", encoding="utf-8") as out:
            out.write('<?xml version="1.0" encoding="utf-8"?>\n')
            out.write('<TEI xmlnsoff="http://www.tei-c.org/ns/1.0" lang="cs">\n')
            out.write("  <teiHeader>\n")
            out.write("    <fileDesc>\n")
            out.write(f"      <titleStmt><title>{doc_id_safe}</title></titleStmt>\n")
            out.write("      <publicationStmt><p>Unpublished</p></publicationStmt>\n")
            source_info = alto_meta.get("source_image", "")
            out.write(
                f"      <sourceDesc><p>Source image: {escape(source_info)}</p></sourceDesc>\n"
                if source_info
                else "      <sourceDesc><p>Unknown source</p></sourceDesc>\n"
            )
            out.write("    </fileDesc>\n")
            out.write("    <encodingDesc>\n      <appInfo>\n")
            udpipe_model_name = conllu_meta.get("udpipe_model") or model_udpipe or ""
            udpipe_generator = conllu_meta.get("generator", "UDPipe")
            if udpipe_model_name or conllu_meta.get("generator"):
                out.write(
                    f'        <application ident="udpipe" version="2">'
                    f"<label>{escape(udpipe_generator)}</label>"
                    f"<desc>Model: {escape(udpipe_model_name)}</desc>"
                    f"</application>\n"
                )
            if model_nametag:
                out.write(
                    f'        <application ident="nametag">'
                    f"<label>NameTag NER</label>"
                    f"<desc>Model: {escape(model_nametag)}</desc>"
                    f"</application>\n"
                )
            if alto_meta.get("ocr_software"):
                out.write(
                    f'        <application ident="ocr">'
                    f"<label>{escape(alto_meta['ocr_software'])} "
                    f"{escape(alto_meta.get('ocr_version', ''))}</label>"
                    f"</application>\n"
                )
            out.write("      </appInfo>\n    </encodingDesc>\n")
            out.write("    <revisionDesc>\n")
            out.write(
                f'      <change when="{current_date}" who="altoconvert">'
                f"Converted from ALTO file {escape(alto_filename)}</change>\n"
            )
            if alto_meta.get("ocr_date") and alto_meta.get("ocr_software"):
                out.write(
                    f'      <change when="{escape(alto_meta["ocr_date"])}" '
                    f'who="{escape(alto_meta["ocr_software"])}">OCR processing</change>\n'
                )
            if conllu_meta.get("generator"):
                out.write(
                    f'      <change when="{current_date}" who="udpipe">'
                    f"NLP enrichment by {escape(conllu_meta['generator'])}</change>\n"
                )
            out.write("    </revisionDesc>\n  </teiHeader>\n")

            # 1. Inside the <facsimile> generator block:
            if alto_pages:
                out.write("  <facsimile>\n")
                for pg in alto_pages:
                    idx = pg["idx"]
                    surf_id = f"{doc_id_safe}.surface{idx}"
                    # FIXED: Added ".png" to fallback tuple
                    sx, sy, img_w, img_h, dx, dy, img_ext = scale_map.get(
                        idx, (1.0, 1.0, None, None, 0, 0, ".png")
                    )
                    facs_img = f"{doc_id_safe}-{idx}{img_ext}"
                    lrx_attr = f' lrx="{img_w}"' if img_w is not None else ""
                    lry_attr = f' lry="{img_h}"' if img_h is not None else ""
                    out.write(f'    <surface id="{surf_id}"{lrx_attr}{lry_attr}>\n')
                    out.write(f'      <graphic url="{facs_img}"/>\n')
                    out.write("    </surface>\n")
                out.write("  </facsimile>\n")

            out.write("  <text>\n    <body>\n")
            current_page = 0
            current_block = None
            current_line = None

            for s_idx, sent in enumerate(sentences, start=1):
                first_bbox = next((t["_bbox"] for t in sent["tokens"] if t.get("_bbox")), None)
                sent_page_trigger = (sent.get("id") == "1") or sent.get("page_break", False)
                if (
                    first_bbox
                    and first_bbox.get("page_idx")
                    and first_bbox["page_idx"] != current_page
                ):
                    sent_page_trigger = True
                    new_page_num = first_bbox["page_idx"]
                else:
                    new_page_num = current_page + 1 if sent_page_trigger else current_page

                # 2. Inside the "if sent_page_trigger:" block:
                if sent_page_trigger:
                    if current_block is not None:
                        out.write("      </div>\n")
                        current_block = None
                    current_page = new_page_num

                    # FIXED: Added ".png" to fallback tuple
                    sx, sy, _, _, dx, dy, img_ext = scale_map.get(
                        current_page, (1.0, 1.0, None, None, 0, 0, ".png")
                    )

                    pb_id = f"{doc_id_safe}.pb{current_page}"
                    facs_img = f"{doc_id_safe}-{current_page}{img_ext}"
                    out.write(f'      <pb n="{current_page}" id="{pb_id}" facs="{facs_img}"/>\n')

                    for g in alto_graphics:
                        if g["page_idx"] == current_page:
                            gid = (
                                escape(g["id"])
                                if g.get("id")
                                else f"{doc_id_safe}.g{abs(hash(g['bbox'])) % 10000}"
                            )
                            scaled_gbbox = _scale_bbox_tuple(g["bbox"], sx, sy, dx, dy)
                            out.write(
                                f'      <figure type="{escape(g["type"])}" '
                                f'id="{gid}" bbox="{scaled_gbbox}"/>\n'
                            )
                            pass
                    else:
                        sx, sy, _, _, dx, dy, _ = scale_map.get(
                            current_page, (1.0, 1.0, None, None, 0, 0, ".png")
                        )

                sent_block = (
                    first_bbox.get("block_id") if first_bbox else None
                ) or f"block_{s_idx}"
                if sent_block != current_block:
                    if current_block is not None:
                        out.write("      </div>\n")
                    current_block = sent_block
                    div_id = escape(f"{doc_id_safe}.{current_block}")
                    raw_block_bbox = alto_blocks.get(current_block, "")
                    if raw_block_bbox:
                        parts = raw_block_bbox.split()
                        if len(parts) == 4:
                            scaled_div_bbox = _scale_bbox_str(
                                int(parts[0]),
                                int(parts[1]),
                                int(parts[2]),
                                int(parts[3]),
                                sx,
                                sy,
                                dx,
                                dy,
                            )
                            bbox_attr = f' bbox="{scaled_div_bbox}"'
                        else:
                            bbox_attr = ""
                    else:
                        bbox_attr = ""
                    out.write(f'      <div type="MarginTextZone-P" id="{div_id}"{bbox_attr}>\n')

                sid = escape(f"{doc_id_safe}.s{s_idx}")
                text_attr = f' text="{_attr(sent["text"])}"' if sent.get("text") else ""
                out.write(f'        <s id="{sid}"{text_attr}>\n')
                id_map = {t["id"]: f"{sid}.w{t['id']}" for t in sent["tokens"]}
                groups = _group_ner_spans(sent["tokens"])

                def _emit_lb_if_changed(tk, base_indent, sx=sx, sy=sy, dx=dx, dy=dy):
                    nonlocal current_line
                    b = tk.get("_bbox")
                    if b and b.get("line_id") and b["line_id"] != current_line:
                        current_line = b["line_id"]
                        lb_id = escape(f"{doc_id_safe}.{current_line}")
                        raw_lb = b.get("line_bbox", "")
                        if raw_lb:
                            parts = raw_lb.split()
                            scaled_lb = (
                                _scale_bbox_str(
                                    int(parts[0]),
                                    int(parts[1]),
                                    int(parts[2]),
                                    int(parts[3]),
                                    sx=sx,
                                    sy=sy,
                                    dx=dx,
                                    dy=dy,
                                )
                                if len(parts) == 4
                                else raw_lb
                            )
                        else:
                            scaled_lb = ""
                        out.write(
                            f'{" " * base_indent}<lb id="{lb_id}"'
                            f"{' bbox=' + chr(34) + scaled_lb + chr(34) if scaled_lb else ''}"
                            f"/>\n"
                        )

                for grp in groups:
                    if grp["kind"] == "name":
                        code = grp["code"]
                        conll_cat = _CNEC_TO_CONLL.get(code, "MISC")
                        out.write(
                            f'          <name type="{escape(conll_cat)}" cnec="{escape(code)}">\n'
                        )
                        for tok in grp["tokens"]:
                            _emit_lb_if_changed(tok, 12)
                            out.write(
                                "  " + _tok_xml(tok, id_map, sx=sx, sy=sy, dx=dx, dy=dy, indent=12)
                            )
                        out.write("          </name>\n")
                    else:
                        tok = grp["tokens"][0]
                        _emit_lb_if_changed(tok, 10)
                        out.write(_tok_xml(tok, id_map, sx=sx, sy=sy, dx=dx, dy=dy, indent=10))

                out.write("        </s>\n")

            if current_block is not None:
                out.write("      </div>\n")
            out.write("    </body>\n  </text>\n</TEI>\n")
        return True
    except Exception as exc:
        print(f"  [Error] Writing TEITOK {teitok_path}: {exc}", file=sys.stderr)
        return False
