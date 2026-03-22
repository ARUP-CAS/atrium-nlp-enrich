"""teitok_alto.py — Produce TEITOK XML from a NER-enriched CoNLL-U + ALTO file.

Coordinate-system notes
-----------------------
ABBYY ALTO stores all HPOS/VPOS values as absolute pixel coordinates measured
from the top-left corner of the *full scanned page* (not from the PrintSpace
origin).  The PrintSpace element merely declares a rectangular region of
interest; it does NOT shift the coordinate origin.

The PNG images used by TEITOK may have been produced at a different DPI than
the resolution ABBYY used internally (e.g. ABBYY scanned at 300 DPI → page
2480 × 3507 px, but the stored PNG was down-sampled to 1240 × 1754 px at
150 DPI).  In that case every bbox coordinate must be multiplied by the ratio
png_width / alto_page_width (and equivalently for the vertical axis) before
being written to the TEITOK XML.

The function ``write_teitok_merged`` accepts an optional ``image_dir``
parameter.  When supplied it looks for the companion PNG (or JPEG/TIFF) for
each page and computes exact per-page scale factors.  When the image is not
found it falls back to the raw ALTO coordinates (scale = 1 × 1) and writes the
ALTO page dimensions into the ``<surface lrx= lry=>`` attributes so that a
TEITOK viewer that understands those attributes can still perform the scaling
itself.
"""

import struct
import sys
import os
from pathlib import Path
import re
from xml.sax.saxutils import escape
import difflib
import collections
import unicodedata
import xml.etree.ElementTree as ET
import datetime

# Maps CNEC 2.0 type codes to the four CoNLL-style categories used in @type.
# @cnec carries the raw CNEC code; @type is used for querying / interop.
_CNEC_TO_CONLL = {
    'p': 'PER', 'p_': 'PER', 'P': 'PER', 'pf': 'PER', 'ps': 'PER', 'pm': 'PER',
    'ph': 'PER', 'pc': 'PER', 'pd': 'PER', 'pp': 'PER',
    'i': 'ORG', 'i_': 'ORG', 'I': 'ORG', 'ia': 'ORG', 'if': 'ORG', 'io': 'ORG', 'ic': 'ORG',
    'g': 'LOC', 'G': 'LOC', 'g_': 'LOC', 'gu': 'LOC', 'gl': 'LOC', 'gq': 'LOC', 'gr': 'LOC',
    'gs': 'LOC', 'gc': 'LOC', 'gt': 'LOC', 'gh': 'LOC',
}

# Image file extensions to probe when searching for companion images.
_IMAGE_EXTS = ('.png', '.PNG', '.jpg', '.JPG', '.jpeg', '.JPEG', '.tiff', '.TIFF', '.tif', '.TIF')


def _attr(value: str) -> str:
    """Escape a string for use inside an XML attribute value (double-quoted)."""
    return escape(value, {'"': '&quot;'})


# ---------------------------------------------------------------------------
# Image dimension utilities (no PIL dependency)
# ---------------------------------------------------------------------------

def _read_image_dimensions(path):
    """Return (width, height) in pixels for PNG, JPEG, or TIFF — no PIL needed.

    Returns ``None`` if the file cannot be read or the format is not
    recognised.
    """
    path = Path(path)
    if not path.exists():
        return None
    try:
        # FIX #6: Open the file exactly once and keep the handle open for the
        # full duration of format detection.  The previous implementation
        # opened a second file handle for JPEG parsing even though the first
        # handle was still in scope, wasting an OS file descriptor and making
        # the code harder to follow.
        with open(path, 'rb') as fh:
            header = fh.read(26)

            # PNG: 8-byte signature + IHDR chunk (4 len + 4 type + 4 width + 4 height)
            if header[:8] == b'\x89PNG\r\n\x1a\n':
                w = struct.unpack('>I', header[16:20])[0]
                h = struct.unpack('>I', header[20:24])[0]
                return (w, h)

            # JPEG: starts with FF D8.
            # FIX #6: reuse the already-open handle; seek past the two SOI
            # bytes we consumed as part of the initial 26-byte header read.
            if header[:2] == b'\xff\xd8':
                fh.seek(2)  # skip SOI marker (already read in the header block)
                while True:
                    marker = fh.read(2)
                    if len(marker) < 2:
                        break
                    if marker[0] != 0xFF:
                        break
                    seg_len = struct.unpack('>H', fh.read(2))[0]
                    # SOF markers: C0..C3, C5..C7, C9..CB, CD..CF
                    if marker[1] in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                                     0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                        fh.read(1)  # precision
                        h = struct.unpack('>H', fh.read(2))[0]
                        w = struct.unpack('>H', fh.read(2))[0]
                        return (w, h)
                    fh.read(seg_len - 2)
                return None

            # TIFF: little-endian (II) or big-endian (MM)
            if header[:2] in (b'II', b'MM'):
                endian = '<' if header[:2] == b'II' else '>'
                fh.seek(4)  # skip magic + offset placeholder already in header
                ifd_offset = struct.unpack(endian + 'I', fh.read(4))[0]
                fh.seek(ifd_offset)
                num_entries = struct.unpack(endian + 'H', fh.read(2))[0]
                w = h = None
                for _ in range(num_entries):
                    tag = struct.unpack(endian + 'H', fh.read(2))[0]
                    typ = struct.unpack(endian + 'H', fh.read(2))[0]
                    fh.read(4)  # count
                    val_bytes = fh.read(4)
                    fmt = endian + ('H' if typ == 3 else 'I')
                    val = struct.unpack(fmt, val_bytes[:struct.calcsize(fmt)])[0]
                    if tag == 256:  # ImageWidth
                        w = val
                    elif tag == 257:  # ImageLength (height)
                        h = val
                    if w is not None and h is not None:
                        return (w, h)
        return None
    except Exception:
        return None


def _find_page_image(image_dir, doc_id, page_idx):
    """Return the path to the companion image for a given page, or None."""
    if not image_dir:
        return None
    base = Path(image_dir)
    for ext in _IMAGE_EXTS:
        candidate = base / f'{doc_id}-{page_idx}{ext}'
        if candidate.exists():
            return candidate
    return None


# ---------------------------------------------------------------------------
# ALTO parsing
# ---------------------------------------------------------------------------

def _parse_alto(alto_path):
    """Parse ALTO XML (v2 / v3 / v4 / no-namespace).

    Returns
    -------
    alto_strings : list[dict]
        One dict per ``<String>`` element with keys:
        ``content``, ``left``, ``top``, ``right``, ``bottom``,
        ``page_idx``, ``block_id``, ``line_id``, ``line_bbox``.
        All coordinate values are raw ALTO pixels (absolute page origin).
    alto_pages : list[dict]
        One dict per ``<Page>`` with keys:
        ``id``, ``width``, ``height``, ``idx``,
        ``ps_hpos``, ``ps_vpos``, ``ps_width``, ``ps_height``
        (PrintSpace offset and dimensions, all as ``int`` or 0).
    alto_graphics : list[dict]
        ``Illustration`` and ``GraphicalElement`` blocks.
    alto_blocks : dict[str, str]
        Block ID → ``"x1 y1 x2 y2"`` bbox string (raw ALTO coords).
    alto_meta : dict
        OCR metadata from the ``<Description>`` section.
    """
    alto_strings = []
    alto_pages   = []
    alto_graphics = []
    alto_blocks  = {}
    alto_meta = {'source_image': '', 'ocr_software': '', 'ocr_version': '', 'ocr_date': ''}

    if not (alto_path and Path(alto_path).exists()):
        return alto_strings, alto_pages, alto_graphics, alto_blocks, alto_meta

    try:
        tree = ET.parse(alto_path)
        root = tree.getroot()

        ns_uri = ''
        if root.tag.startswith('{'):
            ns_uri = root.tag[1:root.tag.index('}')]

        def _tag(local):
            return f'{{{ns_uri}}}{local}' if ns_uri else local

        # ── header metadata ────────────────────────────────────────────────
        for desc in root.iter(_tag('Description')):
            for img_info in desc.iter(_tag('fileName')):
                if img_info.text:
                    alto_meta['source_image'] = img_info.text.strip()
            for ocr in desc.iter(_tag('ocrProcessingStep')):
                for dt in ocr.iter(_tag('processingDateTime')):
                    if dt.text: alto_meta['ocr_date'] = dt.text.strip()
                for sw in ocr.iter(_tag('softwareName')):
                    if sw.text: alto_meta['ocr_software'] = sw.text.strip()
                for swv in ocr.iter(_tag('softwareVersion')):
                    if swv.text: alto_meta['ocr_version'] = swv.text.strip()

        # ── pages ──────────────────────────────────────────────────────────
        for page_idx, page in enumerate(root.iter(_tag('Page')), start=1):
            page_w_str = page.get('WIDTH',  '') or ''
            page_h_str = page.get('HEIGHT', '') or ''

            # Extract PrintSpace offset so we can detect and warn about any
            # relative-coordinate ALTO variant, and record it for callers.
            ps_hpos = ps_vpos = ps_w = ps_h = 0
            for ps in page.iter(_tag('PrintSpace')):
                try:
                    ps_hpos = int(float(ps.get('HPOS',   0) or 0))
                    ps_vpos = int(float(ps.get('VPOS',   0) or 0))
                    ps_w    = int(float(ps.get('WIDTH',  0) or 0))
                    ps_h    = int(float(ps.get('HEIGHT', 0) or 0))
                except (ValueError, TypeError):
                    pass
                break  # only one PrintSpace per page in well-formed ALTO

            alto_pages.append({
                'id':        page.get('ID', f'Page{page_idx}'),
                'width':     page_w_str,
                'height':    page_h_str,
                'idx':       page_idx,
                'ps_hpos':   ps_hpos,
                'ps_vpos':   ps_vpos,
                'ps_width':  ps_w,
                'ps_height': ps_h,
            })

            # ── text blocks, lines, strings ────────────────────────────────
            for block in page.iter(_tag('TextBlock')):
                block_id = block.get('ID', '')
                try:
                    b_hpos   = float(block.get('HPOS',   0) or 0)
                    b_vpos   = float(block.get('VPOS',   0) or 0)
                    b_width  = float(block.get('WIDTH',  0) or 0)
                    b_height = float(block.get('HEIGHT', 0) or 0)
                    alto_blocks[block_id] = (
                        f"{int(b_hpos)} {int(b_vpos)} "
                        f"{int(b_hpos + b_width)} {int(b_vpos + b_height)}"
                    )
                except (ValueError, TypeError):
                    pass

                for line in block.iter(_tag('TextLine')):
                    line_id = line.get('ID', '')
                    try:
                        l_h = float(line.get('HPOS',   0) or 0)
                        l_v = float(line.get('VPOS',   0) or 0)
                        l_w = float(line.get('WIDTH',  0) or 0)
                        l_e = float(line.get('HEIGHT', 0) or 0)
                        line_bbox = f"{int(l_h)} {int(l_v)} {int(l_h + l_w)} {int(l_v + l_e)}"
                    except (ValueError, TypeError):
                        line_bbox = ''

                    for string in line.iter(_tag('String')):
                        content = string.get('CONTENT', '')
                        if not content:
                            continue
                        try:
                            hpos   = float(string.get('HPOS',   0) or 0)
                            vpos   = float(string.get('VPOS',   0) or 0)
                            width  = float(string.get('WIDTH',  0) or 0)
                            height = float(string.get('HEIGHT', 0) or 0)
                            alto_strings.append({
                                'content':   content,
                                'left':      int(hpos),
                                'top':       int(vpos),
                                'right':     int(hpos + width),
                                'bottom':    int(vpos + height),
                                'page_idx':  page_idx,
                                'block_id':  block_id,
                                'line_id':   line_id,
                                'line_bbox': line_bbox,
                            })
                        except (ValueError, TypeError):
                            pass

            # ── graphical elements ─────────────────────────────────────────
            for gtag in ('Illustration', 'GraphicalElement'):
                for graphic in page.iter(_tag(gtag)):
                    try:
                        hpos   = float(graphic.get('HPOS',   0) or 0)
                        vpos   = float(graphic.get('VPOS',   0) or 0)
                        width  = float(graphic.get('WIDTH',  0) or 0)
                        height = float(graphic.get('HEIGHT', 0) or 0)
                        alto_graphics.append({
                            'type':     gtag,
                            'id':       graphic.get('ID', ''),
                            'bbox':     (int(hpos), int(vpos),
                                         int(hpos + width), int(vpos + height)),
                            'page_idx': page_idx,
                        })
                    except (ValueError, TypeError):
                        pass

    except Exception as exc:
        print(f'  [Warn] Failed to parse ALTO {alto_path}: {exc}', file=sys.stderr)

    return alto_strings, alto_pages, alto_graphics, alto_blocks, alto_meta


# ---------------------------------------------------------------------------
# Per-page coordinate scaling
# ---------------------------------------------------------------------------

def _build_page_scale_map(alto_pages, image_dir, doc_id):
    """Return a dict mapping page_idx → (sx, sy, img_w, img_h).

    ``sx`` and ``sy`` are the factors to multiply raw ALTO pixel coordinates
    by in order to obtain the corresponding pixel position in the companion
    PNG image.  When no image is found they default to 1.0 / 1.0 and
    ``img_w`` / ``img_h`` reflect the ALTO page dimensions.

    The function also emits a diagnostic line for every page where the image
    was found, showing the detected scale.
    """
    scale_map = {}
    for pg in alto_pages:
        idx = pg['idx']
        try:
            alto_w = float(pg.get('width')  or 0)
            alto_h = float(pg.get('height') or 0)
        except (ValueError, TypeError):
            alto_w = alto_h = 0.0

        img_dims = None
        img_path = _find_page_image(image_dir, doc_id, idx)
        if img_path:
            img_dims = _read_image_dimensions(img_path)

        if img_dims and alto_w > 0 and alto_h > 0:
            sx = img_dims[0] / alto_w
            sy = img_dims[1] / alto_h
            if abs(sx - 1.0) > 0.01 or abs(sy - 1.0) > 0.01:
                print(
                    f'  [Scale] Page {idx}: ALTO {int(alto_w)}×{int(alto_h)} px → '
                    f'image {img_dims[0]}×{img_dims[1]} px  (sx={sx:.4f}, sy={sy:.4f})',
                    file=sys.stderr,
                )
            scale_map[idx] = (sx, sy, img_dims[0], img_dims[1])
        else:
            # No image or zero ALTO dimensions: keep raw coordinates.
            scale_map[idx] = (1.0, 1.0,
                               int(alto_w)  if alto_w  else None,
                               int(alto_h)  if alto_h  else None)
    return scale_map


def _scale_bbox_str(x1, y1, x2, y2, sx, sy):
    """Return a scaled ``"x1 y1 x2 y2"`` string (values rounded to int)."""
    return (f"{round(x1 * sx)} {round(y1 * sy)} "
            f"{round(x2 * sx)} {round(y2 * sy)}")


def _scale_bbox_tuple(bbox_tuple, sx, sy):
    """Scale a (x1, y1, x2, y2) tuple and return an ``"x1 y1 x2 y2"`` string."""
    x1, y1, x2, y2 = bbox_tuple
    return _scale_bbox_str(x1, y1, x2, y2, sx, sy)


# ---------------------------------------------------------------------------
# Token alignment
# ---------------------------------------------------------------------------

def _align_tokens_to_alto(tokens, alto_strings):
    """Match UDPipe tokens to ALTO String elements via difflib SequenceMatcher.

    Normalises both sides to lowercase NFC before matching so that OCR
    capitalisation quirks and tokeniser splitting differences do not break
    the alignment.

    Returns a list of bbox dicts (or ``None``) parallel to ``tokens``.
    Each bbox dict has keys: ``left``, ``top``, ``right``, ``bottom``,
    ``page_idx``, ``block_id``, ``line_id``, ``line_bbox``.
    All coordinate values are *raw ALTO pixels* (no scaling applied here).
    """
    if not alto_strings:
        return [None] * len(tokens)

    def norm(s):
        return unicodedata.normalize('NFC', s).lower()

    # Build flat character sequences for the ALTO side.
    alto_char_list   = []
    alto_char_to_idx = []
    for idx, s in enumerate(alto_strings):
        for ch in norm(s['content']):
            if ch.strip():
                alto_char_list.append(ch)
                alto_char_to_idx.append(idx)
    alto_str = ''.join(alto_char_list)

    # Build flat character sequences for the token side.
    tok_char_list      = []
    tok_char_to_tok_idx = []
    for t_idx, tok in enumerate(tokens):
        for ch in norm(tok.get('form', '')):
            if ch.strip():
                tok_char_list.append(ch)
                tok_char_to_tok_idx.append(t_idx)
    tok_str = ''.join(tok_char_list)

    sm = difflib.SequenceMatcher(None, tok_str, alto_str, autojunk=False)
    tok_to_alto_indices = collections.defaultdict(list)

    for block in sm.get_matching_blocks():
        i, j, n = block
        for k in range(n):
            t_idx = tok_char_to_tok_idx[i + k]
            a_idx = alto_char_to_idx[j + k]
            tok_to_alto_indices[t_idx].append(a_idx)

    bboxes = [None] * len(tokens)
    for t_idx in range(len(tokens)):
        a_indices = tok_to_alto_indices.get(t_idx)
        if not a_indices:
            continue
        first_a = alto_strings[a_indices[0]]

        # FIX #12: warn when a single token spans ALTO strings from different
        # pages (typically caused by OCR errors near page boundaries).  We
        # still use the first matched string's page_idx, but the diagnostic
        # helps the operator identify problematic regions quickly.
        page_indices = set(alto_strings[a]['page_idx'] for a in a_indices)
        if len(page_indices) > 1:
            form = tokens[t_idx].get('form', '?')
            print(
                f"  [Warn] Token '{form}' spans pages {sorted(page_indices)}; "
                "using first matched page for bbox assignment.",
                file=sys.stderr,
            )

        bboxes[t_idx] = {
            'left':      min(alto_strings[a]['left']   for a in a_indices),
            'top':       min(alto_strings[a]['top']    for a in a_indices),
            'right':     max(alto_strings[a]['right']  for a in a_indices),
            'bottom':    max(alto_strings[a]['bottom'] for a in a_indices),
            'page_idx':  first_a['page_idx'],
            'block_id':  first_a['block_id'],
            'line_id':   first_a['line_id'],
            'line_bbox': first_a['line_bbox'],
        }

    return bboxes


# ---------------------------------------------------------------------------
# NER span helpers
# ---------------------------------------------------------------------------

def _bio_to_code(ner_tag):
    if not ner_tag or ner_tag in ('O', '_'):
        return ''
    primary = ner_tag.split('|')[0]
    return primary[2:] if primary.startswith(('B-', 'I-')) else ''


def _group_ner_spans(tokens):
    """Group tokens into plain / named-entity spans, returning a list of dicts."""
    groups = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        ner = tok.get('ner', '')
        if ner and ner not in ('O', '_') and ner.startswith('B-'):
            span = [tok]
            i += 1
            while i < len(tokens):
                nxt = tokens[i].get('ner', '')
                if nxt and nxt.startswith('I-'):
                    span.append(tokens[i])
                    i += 1
                else:
                    break
            groups.append({'kind': 'name', 'tokens': span, 'code': _bio_to_code(ner)})
        else:
            groups.append({'kind': 'plain', 'tokens': [tok]})
            i += 1
    return groups


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------

def _parse_misc(misc_str):
    if misc_str == '_' or not misc_str:
        return {}
    misc = {}
    for item in misc_str.split('|'):
        if '=' in item:
            k, v = item.split('=', 1)
            misc[k] = v
        else:
            misc[item] = 'Yes'
    return misc


def _tok_xml(tok, id_map, sx=1.0, sy=1.0, indent=10):
    """Render one ``<tok>`` element.

    Bboxes stored in ``tok['_bbox']`` are in raw ALTO pixels; ``sx`` / ``sy``
    scale them to the companion image's pixel space before writing.
    """
    wid = id_map.get(tok['id'], tok['id'])
    head_ref = None
    if tok.get('head') and tok['head'] != '0':
        head_ref = id_map.get(tok['head'], tok['head'])

    tok_type = 'pc' if tok.get('upos') == 'PUNCT' else 'w'
    attrs = [f'id="{wid}"', f'type="{tok_type}"']

    if tok.get('lemma')  and tok['lemma']  != '_': attrs.append(f'lemma="{_attr(tok["lemma"])}"')
    if tok.get('upos')   and tok['upos']   != '_': attrs.append(f'upos="{_attr(tok["upos"])}"')
    if tok.get('xpos')   and tok['xpos']   != '_': attrs.append(f'xpos="{_attr(tok["xpos"])}"')
    if tok.get('feats')  and tok['feats']  != '_': attrs.append(f'feats="{_attr(tok["feats"])}"')
    if head_ref is not None:                        attrs.append(f'head="{head_ref}"')
    if tok.get('deprel') and tok['deprel'] != '_': attrs.append(f'deprel="{_attr(tok["deprel"])}"')
    if not tok.get('space_after', True):            attrs.append('join="right"')

    bbox = tok.get('_bbox')
    if bbox:
        attrs.append(f'bbox="{_scale_bbox_str(bbox["left"], bbox["top"], bbox["right"], bbox["bottom"], sx, sy)}"')

    pad = ' ' * indent
    return f'{pad}<tok {" ".join(attrs)}>{escape(tok["form"])}</tok>\n'


# ---------------------------------------------------------------------------
# Main public entry point
# ---------------------------------------------------------------------------

def write_teitok_merged(conllu_path, teitok_path, alto_path=None, doc_id=None,
                        model_udpipe=None, model_nametag=None,
                        image_dir=None):
    """Produce TEITOK XML from a NER-enriched CoNLL-U and structural ALTO file.

    Parameters
    ----------
    conllu_path : str | Path
        Path to the merged CoNLL-U file (with NER in MISC column).
    teitok_path : str | Path
        Destination TEITOK XML file.
    alto_path : str | Path | None
        Source ALTO XML file.  When ``None`` the output contains no bboxes.
    doc_id : str | None
        Document identifier used to prefix XML IDs.  Defaults to the stem
        of ``teitok_path``.
    model_udpipe : str | None
        UDPipe model name written into ``<encodingDesc>``.
    model_nametag : str | None
        NameTag model name written into ``<encodingDesc>``.
    image_dir : str | Path | None
        Directory that contains the companion page images
        (``<doc_id>-1.png``, ``<doc_id>-2.png`` …).  When supplied the
        function reads each image's actual pixel dimensions and scales all
        ALTO bbox coordinates accordingly so they align with the PNG even
        if the PDF → PNG conversion changed the resolution.
    """
    alto_strings, alto_pages, alto_graphics, alto_blocks, alto_meta = _parse_alto(alto_path)

    # ── build per-page coordinate scale factors ────────────────────────────
    # image_dir may also be inferred from the ALTO path (sibling directory)
    effective_image_dir = image_dir
    if not effective_image_dir and alto_path:
        candidate = Path(alto_path).parent
        if any(candidate.glob('*.png')) or any(candidate.glob('*.jpg')):
            effective_image_dir = candidate

    _doc_id = doc_id or Path(teitok_path).stem
    scale_map = _build_page_scale_map(alto_pages, effective_image_dir, _doc_id)

    # Warn when PrintSpace offset is large relative to page size — a hint that
    # something unusual is happening with the margins.
    for pg in alto_pages:
        try:
            pw = float(pg.get('width')  or 0)
            ph = float(pg.get('height') or 0)
            ps_h = pg['ps_hpos']
            ps_v = pg['ps_vpos']
            if pw > 0 and ph > 0:
                if ps_h / pw > 0.05 or ps_v / ph > 0.05:
                    print(
                        f'  [Margins] Page {pg["idx"]}: PrintSpace offset '
                        f'({ps_h} px left, {ps_v} px top) is '
                        f'{ps_h/pw*100:.1f}% / {ps_v/ph*100:.1f}% of page — '
                        f'ALTO coords are absolute (origin = page corner), '
                        f'but verify PNG covers the full page including margins.',
                        file=sys.stderr,
                    )
        except (ValueError, TypeError, KeyError):
            pass

    # ── parse CoNLL-U ──────────────────────────────────────────────────────
    sentences  = []
    current_tok = []
    sent_id = sent_text = None
    conllu_meta = {}
    # FIX (page_break propagation): track pending page_break comment so that
    # merged files produced by call_udpipe.py are handled correctly.
    pending_page_break = False

    try:
        with open(conllu_path, 'r', encoding='utf-8') as fh:
            for raw in fh:
                line = raw.rstrip('\n')

                if line.startswith('# generator ='):
                    conllu_meta['generator'] = line.split('=', 1)[1].strip()
                if line.startswith('# udpipe_model ='):
                    conllu_meta['udpipe_model'] = line.split('=', 1)[1].strip()
                if line.startswith('# udpipe_model_licence ='):
                    conllu_meta['udpipe_model_licence'] = line.split('=', 1)[1].strip()

                # FIX (page_break propagation): detect explicit page-break
                # marker injected by call_udpipe.py when merging chunks.
                if line.strip() == '# page_break = true':
                    pending_page_break = True
                    continue

                if line.startswith('# sent_id'):
                    sent_id = line.split('=', 1)[1].strip() if '=' in line else None
                    continue
                if line.startswith('# text'):
                    sent_text = line.split('=', 1)[1].strip() if '=' in line else None
                    continue
                if not line.strip() or line.startswith('#'):
                    if not line.strip() and current_tok:
                        sentences.append({
                            'id':         sent_id,
                            'text':       sent_text,
                            'tokens':     current_tok,
                            'page_break': pending_page_break,
                        })
                        current_tok = []
                        pending_page_break = False
                    continue

                cols = line.split('\t')
                if len(cols) < 10 or '-' in cols[0] or '.' in cols[0]:
                    continue
                misc = _parse_misc(cols[9])
                current_tok.append({
                    'id':          cols[0],
                    'form':        cols[1],
                    'lemma':       cols[2],
                    'upos':        cols[3],
                    'xpos':        cols[4],
                    'feats':       cols[5],
                    'head':        cols[6],
                    'deprel':      cols[7],
                    'space_after': misc.get('SpaceAfter', 'Yes') != 'No',
                    'ner':         misc.get('NER', ''),
                })
        if current_tok:
            sentences.append({
                'id':         sent_id,
                'text':       sent_text,
                'tokens':     current_tok,
                'page_break': pending_page_break,
            })
    except Exception as exc:
        print(f'  [Error] Reading CoNLL-U {conllu_path}: {exc}', file=sys.stderr)
        return False

    # ── align tokens → ALTO bboxes ─────────────────────────────────────────
    all_tokens  = [tok for sent in sentences for tok in sent['tokens']]
    all_bboxes  = _align_tokens_to_alto(all_tokens, alto_strings)
    tok_ptr = 0
    for sent in sentences:
        for tok in sent['tokens']:
            tok['_bbox'] = all_bboxes[tok_ptr]
            tok_ptr += 1

    matched = sum(1 for b in all_bboxes if b is not None)
    print(f'  [ALTO] matched {matched}/{len(all_tokens)} tokens to ALTO bboxes')

    # ── write TEITOK XML ───────────────────────────────────────────────────
    doc_id_safe   = escape(_doc_id)
    alto_filename = Path(alto_path).name if alto_path else 'Unknown'
    current_date  = datetime.date.today().isoformat()

    try:
        with open(teitok_path, 'w', encoding='utf-8') as out:
            out.write('<?xml version="1.0" encoding="utf-8"?>\n')
            out.write('<TEI xmlns="http://www.tei-c.org/ns/1.0" xml:lang="cs">\n')

            # ── TEI header ────────────────────────────────────────────────
            out.write('  <teiHeader>\n')
            out.write('    <fileDesc>\n')
            out.write(f'      <titleStmt><title>{doc_id_safe}</title></titleStmt>\n')
            out.write('      <publicationStmt><p>Unpublished</p></publicationStmt>\n')

            source_info = alto_meta.get('source_image', '')
            out.write(
                f'      <sourceDesc><p>Source image: {escape(source_info)}</p></sourceDesc>\n'
                if source_info else
                '      <sourceDesc><p>Unknown source</p></sourceDesc>\n'
            )
            out.write('    </fileDesc>\n')

            out.write('    <encodingDesc>\n      <appInfo>\n')

            udpipe_model_name = conllu_meta.get('udpipe_model') or model_udpipe or ''
            udpipe_generator  = conllu_meta.get('generator', 'UDPipe')
            if udpipe_model_name or conllu_meta.get('generator'):
                out.write(
                    f'        <application ident="udpipe" version="2">'
                    f'<label>{escape(udpipe_generator)}</label>'
                    f'<desc>Model: {escape(udpipe_model_name)}</desc>'
                    f'</application>\n'
                )
            if model_nametag:
                out.write(
                    f'        <application ident="nametag">'
                    f'<label>NameTag NER</label>'
                    f'<desc>Model: {escape(model_nametag)}</desc>'
                    f'</application>\n'
                )
            if alto_meta.get('ocr_software'):
                out.write(
                    f'        <application ident="ocr">'
                    f'<label>{escape(alto_meta["ocr_software"])} '
                    f'{escape(alto_meta.get("ocr_version", ""))}</label>'
                    f'</application>\n'
                )
            out.write('      </appInfo>\n    </encodingDesc>\n')

            out.write('    <revisionDesc>\n')
            out.write(
                f'      <change when="{current_date}" who="altoconvert">'
                f'Converted from ALTO file {escape(alto_filename)}</change>\n'
            )
            if alto_meta.get('ocr_date') and alto_meta.get('ocr_software'):
                out.write(
                    f'      <change when="{escape(alto_meta["ocr_date"])}" '
                    f'who="{escape(alto_meta["ocr_software"])}">OCR processing</change>\n'
                )
            if conllu_meta.get('generator'):
                out.write(
                    f'      <change when="{current_date}" who="udpipe">'
                    f'NLP enrichment by {escape(conllu_meta["generator"])}</change>\n'
                )
            out.write('    </revisionDesc>\n  </teiHeader>\n')

            # ── facsimile surfaces ─────────────────────────────────────────
            if alto_pages:
                out.write('  <facsimile>\n')
                for pg in alto_pages:
                    idx       = pg['idx']
                    surf_id   = f'{doc_id_safe}.surface{idx}'
                    facs_img  = f'{doc_id_safe}-{idx}.png'
                    sx, sy, img_w, img_h = scale_map.get(idx, (1.0, 1.0, None, None))
                    lrx_attr  = f' lrx="{img_w}"'  if img_w  is not None else ''
                    lry_attr  = f' lry="{img_h}"'  if img_h  is not None else ''
                    out.write(f'    <surface id="{surf_id}"{lrx_attr}{lry_attr}>\n')
                    out.write(f'      <graphic url="{facs_img}"/>\n')
                    out.write('    </surface>\n')
                out.write('  </facsimile>\n')

            out.write('  <text>\n    <body>\n')

            current_page  = 0
            current_block = None
            current_line  = None

            for s_idx, sent in enumerate(sentences, start=1):
                first_bbox = next(
                    (t['_bbox'] for t in sent['tokens'] if t.get('_bbox')), None
                )

                # ── page breaks ───────────────────────────────────────────
                # FIX (page_break propagation): detect EITHER the legacy
                # sent_id == "1" signal OR the explicit page_break flag
                # stored during CoNLL-U parsing above.
                sent_page_trigger = (sent.get('id') == '1') or sent.get('page_break', False)
                if first_bbox and first_bbox.get('page_idx') and \
                        first_bbox['page_idx'] != current_page:
                    sent_page_trigger = True
                    new_page_num = first_bbox['page_idx']
                else:
                    new_page_num = current_page + 1 if sent_page_trigger else current_page

                if sent_page_trigger:
                    if current_block is not None:
                        out.write('      </div>\n')
                        current_block = None

                    current_page = new_page_num
                    sx, sy, _, _ = scale_map.get(current_page, (1.0, 1.0, None, None))

                    pb_id    = f'{doc_id_safe}.pb{current_page}'
                    facs_img = f'{doc_id_safe}-{current_page}.png'
                    out.write(
                        f'      <pb n="{current_page}" id="{pb_id}" facs="{facs_img}"/>\n'
                    )

                    for g in alto_graphics:
                        if g['page_idx'] == current_page:
                            gid = escape(g['id']) if g.get('id') else \
                                  f"{doc_id_safe}.g{abs(hash(g['bbox'])) % 10000}"
                            scaled_gbbox = _scale_bbox_tuple(g['bbox'], sx, sy)
                            out.write(
                                f'      <figure type="{escape(g["type"])}" '
                                f'id="{gid}" bbox="{scaled_gbbox}"/>\n'
                            )
                else:
                    sx, sy, _, _ = scale_map.get(current_page, (1.0, 1.0, None, None))

                # ── div / block boundaries ────────────────────────────────
                sent_block = (first_bbox.get('block_id') if first_bbox else None) \
                             or f'block_{s_idx}'

                if sent_block != current_block:
                    if current_block is not None:
                        out.write('      </div>\n')
                    current_block = sent_block
                    div_id = escape(f'{doc_id_safe}.{current_block}')

                    raw_block_bbox = alto_blocks.get(current_block, '')
                    if raw_block_bbox:
                        parts = raw_block_bbox.split()
                        if len(parts) == 4:
                            scaled_div_bbox = _scale_bbox_str(
                                int(parts[0]), int(parts[1]),
                                int(parts[2]), int(parts[3]),
                                sx, sy,
                            )
                            bbox_attr = f' bbox="{scaled_div_bbox}"'
                        else:
                            bbox_attr = ''
                    else:
                        bbox_attr = ''

                    out.write(
                        f'      <div type="MarginTextZone-P" '
                        f'id="{div_id}"{bbox_attr}>\n'
                    )

                # ── sentence ──────────────────────────────────────────────
                sid       = escape(f'{doc_id_safe}.s{s_idx}')
                text_attr = f' text="{_attr(sent["text"])}"' if sent.get('text') else ''
                out.write(f'        <s id="{sid}"{text_attr}>\n')

                id_map = {t['id']: f'{sid}.w{t["id"]}' for t in sent['tokens']}
                groups = _group_ner_spans(sent['tokens'])

                def _emit_lb_if_changed(tk, base_indent):
                    nonlocal current_line
                    b = tk.get('_bbox')
                    if b and b.get('line_id') and b['line_id'] != current_line:
                        current_line  = b['line_id']
                        lb_id         = escape(f'{doc_id_safe}.{current_line}')
                        raw_lb = b.get('line_bbox', '')
                        if raw_lb:
                            parts = raw_lb.split()
                            scaled_lb = _scale_bbox_str(
                                int(parts[0]), int(parts[1]),
                                int(parts[2]), int(parts[3]),
                                sx, sy,
                            ) if len(parts) == 4 else raw_lb
                        else:
                            scaled_lb = ''
                        out.write(
                            f'{" " * base_indent}<lb id="{lb_id}"'
                            f'{" bbox=" + chr(34) + scaled_lb + chr(34) if scaled_lb else ""}'
                            f'/>\n'
                        )

                for grp in groups:
                    if grp['kind'] == 'name':
                        code      = grp['code']
                        conll_cat = _CNEC_TO_CONLL.get(code, 'MISC')
                        out.write(
                            f'          <name type="{escape(conll_cat)}" '
                            f'cnec="{escape(code)}">\n'
                        )
                        for tok in grp['tokens']:
                            _emit_lb_if_changed(tok, 12)
                            out.write('  ' + _tok_xml(tok, id_map, sx=sx, sy=sy, indent=12))
                        out.write('          </n>\n')
                    else:
                        tok = grp['tokens'][0]
                        _emit_lb_if_changed(tok, 10)
                        out.write(_tok_xml(tok, id_map, sx=sx, sy=sy, indent=10))

                out.write('        </s>\n')

            if current_block is not None:
                out.write('      </div>\n')

            out.write('    </body>\n  </text>\n</TEI>\n')

        return True

    except Exception as exc:
        print(f'  [Error] Writing TEITOK {teitok_path}: {exc}', file=sys.stderr)
        return False