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


def _attr(value: str) -> str:
    """Escape a string for use inside an XML attribute value (double-quoted)."""
    return escape(value, {'"': '&quot;'})


def _parse_alto(alto_path):
    """
    Parse ALTO XML (v2/v3/v4/no-namespace).
    Returns strings, pages, graphics, block_bboxes, and metadata with full hierarchy.
    """
    alto_strings = []
    alto_pages = []
    alto_graphics = []
    alto_blocks = {}
    alto_meta = {
        'source_image': '',
        'ocr_software': '',
        'ocr_version': '',
        'ocr_date': ''
    }

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

        # Extract Header Metadata
        for desc in root.iter(_tag('Description')):
            for img_info in desc.iter(_tag('fileName')):
                if img_info.text: alto_meta['source_image'] = img_info.text.strip()
            for ocr in desc.iter(_tag('ocrProcessingStep')):
                for dt in ocr.iter(_tag('processingDateTime')):
                    if dt.text: alto_meta['ocr_date'] = dt.text.strip()
                for sw in ocr.iter(_tag('softwareName')):
                    if sw.text: alto_meta['ocr_software'] = sw.text.strip()
                for swv in ocr.iter(_tag('softwareVersion')):
                    if swv.text: alto_meta['ocr_version'] = swv.text.strip()

        for page_idx, page in enumerate(root.iter(_tag('Page')), start=1):
            alto_pages.append({
                'id': page.get('ID', f'Page{page_idx}'),
                'width': page.get('WIDTH', ''),
                'height': page.get('HEIGHT', ''),
                'idx': page_idx
            })

            # Capture all TextBlocks and TextLines
            for block in page.iter(_tag('TextBlock')):
                block_id = block.get('ID', '')

                # Get Block Bbox for <div> representation
                try:
                    b_hpos = float(block.get('HPOS', 0))
                    b_vpos = float(block.get('VPOS', 0))
                    b_width = float(block.get('WIDTH', 0))
                    b_height = float(block.get('HEIGHT', 0))
                    alto_blocks[
                        block_id] = f"{int(b_hpos)} {int(b_vpos)} {int(b_hpos + b_width)} {int(b_vpos + b_height)}"
                except (ValueError, TypeError):
                    pass

                for line in block.iter(_tag('TextLine')):
                    line_id = line.get('ID', '')
                    l_hpos = line.get('HPOS', '')
                    l_vpos = line.get('VPOS', '')
                    l_width = line.get('WIDTH', '')
                    l_height = line.get('HEIGHT', '')

                    try:
                        line_bbox = f"{int(float(l_hpos))} {int(float(l_vpos))} {int(float(l_hpos) + float(l_width))} {int(float(l_vpos) + float(l_height))}"
                    except:
                        line_bbox = ""

                    for string in line.iter(_tag('String')):
                        content = string.get('CONTENT', '')
                        if not content: continue
                        try:
                            hpos = float(string.get('HPOS', 0))
                            vpos = float(string.get('VPOS', 0))
                            width = float(string.get('WIDTH', 0))
                            height = float(string.get('HEIGHT', 0))
                            alto_strings.append({
                                'content': content,
                                'left': int(hpos),
                                'top': int(vpos),
                                'right': int(hpos + width),
                                'bottom': int(vpos + height),
                                'page_idx': page_idx,
                                'block_id': block_id,
                                'line_id': line_id,
                                'line_bbox': line_bbox
                            })
                        except (ValueError, TypeError):
                            pass

            # Capture Graphical Elements and Illustrations
            for graphic_tag in ['Illustration', 'GraphicalElement']:
                for graphic in page.iter(_tag(graphic_tag)):
                    try:
                        hpos = float(graphic.get('HPOS', 0))
                        vpos = float(graphic.get('VPOS', 0))
                        width = float(graphic.get('WIDTH', 0))
                        height = float(graphic.get('HEIGHT', 0))
                        alto_graphics.append({
                            'type': graphic_tag,
                            'id': graphic.get('ID', ''),
                            'bbox': f"{int(hpos)} {int(vpos)} {int(hpos + width)} {int(vpos + height)}",
                            'page_idx': page_idx
                        })
                    except (ValueError, TypeError):
                        pass

    except Exception as e:
        print(f'  [Warn] Failed to parse ALTO {alto_path}: {e}', file=sys.stderr)

    return alto_strings, alto_pages, alto_graphics, alto_blocks, alto_meta


def _align_tokens_to_alto(tokens, alto_strings):
    """
    Robust aligner leveraging difflib's sequence matcher to bridge OCR/Tokenisation
    mismatches without hard gaps. Normalizes to lowercase NFC to maximize linkage.
    """
    if not alto_strings:
        return [None] * len(tokens)

    def norm(s):
        return unicodedata.normalize('NFC', s).lower()

    alto_char_list = []
    alto_char_to_idx = []
    for idx, s in enumerate(alto_strings):
        for ch in norm(s['content']):
            if ch.strip():
                alto_char_list.append(ch)
                alto_char_to_idx.append(idx)
    alto_str = "".join(alto_char_list)

    tok_char_list = []
    tok_char_to_tok_idx = []
    for t_idx, tok in enumerate(tokens):
        for ch in norm(tok.get('form', '')):
            if ch.strip():
                tok_char_list.append(ch)
                tok_char_to_tok_idx.append(t_idx)
    tok_str = "".join(tok_char_list)

    sm = difflib.SequenceMatcher(None, tok_str, alto_str)
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

        lefts = [alto_strings[a]['left'] for a in a_indices]
        tops = [alto_strings[a]['top'] for a in a_indices]
        rights = [alto_strings[a]['right'] for a in a_indices]
        bottoms = [alto_strings[a]['bottom'] for a in a_indices]

        first_a = alto_strings[a_indices[0]]

        bboxes[t_idx] = {
            'left': min(lefts),
            'top': min(tops),
            'right': max(rights),
            'bottom': max(bottoms),
            'page_idx': first_a.get('page_idx'),
            'block_id': first_a.get('block_id'),
            'line_id': first_a.get('line_id'),
            'line_bbox': first_a.get('line_bbox')
        }

    return bboxes


def _bio_to_code(ner_tag):
    if not ner_tag or ner_tag in ('O', '_'): return ''
    primary = ner_tag.split('|')[0]
    return primary[2:] if primary.startswith(('B-', 'I-')) else ''


def _group_ner_spans(tokens):
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


def _tok_xml(tok, id_map, indent=10):
    wid = id_map.get(tok['id'], tok['id'])
    head_ref = None
    if tok.get('head') and tok['head'] != '0':
        head_ref = id_map.get(tok['head'], tok['head'])

    tok_type = 'pc' if tok.get('upos') == 'PUNCT' else 'w'
    attrs = [f'id="{wid}"', f'type="{tok_type}"']

    if tok.get('lemma') and tok['lemma'] != '_': attrs.append(f'lemma="{_attr(tok["lemma"])}"')
    if tok.get('upos') and tok['upos'] != '_': attrs.append(f'upos="{_attr(tok["upos"])}"')
    if tok.get('xpos') and tok['xpos'] != '_': attrs.append(f'xpos="{_attr(tok["xpos"])}"')
    if tok.get('feats') and tok['feats'] != '_': attrs.append(f'feats="{_attr(tok["feats"])}"')
    if head_ref is not None: attrs.append(f'head="{head_ref}"')
    if tok.get('deprel') and tok['deprel'] != '_': attrs.append(f'deprel="{_attr(tok["deprel"])}"')
    if not tok.get('space_after', True): attrs.append('join="right"')

    bbox = tok.get('_bbox')
    if bbox: attrs.append(f'bbox="{bbox["left"]} {bbox["top"]} {bbox["right"]} {bbox["bottom"]}"')

    pad = ' ' * indent
    return f'{pad}<tok {" ".join(attrs)}>{escape(tok["form"])}</tok>\n'


def _parse_misc(misc_str):
    if misc_str == '_' or not misc_str: return {}
    misc = {}
    for item in misc_str.split('|'):
        if '=' in item:
            k, v = item.split('=', 1)
            misc[k] = v
        else:
            misc[item] = "Yes"
    return misc


def write_teitok_merged(conllu_path, teitok_path, alto_path=None, doc_id=None,
                        model_udpipe=None, model_nametag=None):
    """Produce TEITOK XML from a NER-enriched CoNLL-U and structural ALTO file.

    model_udpipe  – fallback model identifier when the CoNLL-U comments don't
                    carry a '# udpipe_model' line (e.g. 'czech-pdt-ud-2.15-241121').
    model_nametag – NameTag model identifier added to encodingDesc/appInfo
                    (e.g. 'nametag3-czech-cnec2.0-240830').
    """
    alto_strings, alto_pages, alto_graphics, alto_blocks, alto_meta = _parse_alto(alto_path)

    sentences = []
    current_tok = []
    sent_id, sent_text = None, None
    conllu_meta = {}



    try:
        with open(conllu_path, 'r', encoding='utf-8') as f:
            for raw in f:
                line = raw.rstrip('\n')

                # Fetch CoNLL-U Metadata for TEI Header
                if line.startswith('# generator ='): conllu_meta['generator'] = line.split('=', 1)[1].strip()
                if line.startswith('# udpipe_model ='): conllu_meta['udpipe_model'] = line.split('=', 1)[1].strip()
                if line.startswith('# udpipe_model_licence ='): conllu_meta['udpipe_model_licence'] = \
                line.split('=', 1)[1].strip()

                if line.startswith('# sent_id'):
                    sent_id = line.split('=', 1)[1].strip() if '=' in line else None
                    continue
                if line.startswith('# text'):
                    sent_text = line.split('=', 1)[1].strip() if '=' in line else None
                    continue
                if not line.strip() or line.startswith('#'):
                    if not line.strip() and current_tok:
                        sentences.append({'id': sent_id, 'text': sent_text, 'tokens': current_tok})
                        current_tok = []
                    continue
                cols = line.split('\t')
                if len(cols) < 10 or '-' in cols[0] or '.' in cols[0]:
                    continue
                misc = _parse_misc(cols[9])
                current_tok.append({
                    'id': cols[0], 'form': cols[1], 'lemma': cols[2], 'upos': cols[3],
                    'xpos': cols[4], 'feats': cols[5], 'head': cols[6], 'deprel': cols[7],
                    'space_after': misc.get('SpaceAfter', 'Yes') != 'No', 'ner': misc.get('NER', ''),
                })
        if current_tok:
            sentences.append({'id': sent_id, 'text': sent_text, 'tokens': current_tok})
    except Exception as e:
        print(f'  [Error] Reading CoNLL-U {conllu_path}: {e}', file=sys.stderr)
        return False

    all_tokens = [tok for sent in sentences for tok in sent['tokens']]
    all_bboxes = _align_tokens_to_alto(all_tokens, alto_strings)
    tok_ptr = 0
    for sent in sentences:
        for tok in sent['tokens']:
            tok['_bbox'] = all_bboxes[tok_ptr]
            tok_ptr += 1

    matched = sum(1 for b in all_bboxes if b is not None)
    print(f'  [ALTO] matched {matched}/{len(all_tokens)} tokens to ALTO bboxes')

    doc_id_safe = escape(doc_id or Path(teitok_path).stem)
    alto_filename = Path(alto_path).name if alto_path else "Unknown"
    current_date = datetime.date.today().isoformat()

    try:
        with open(teitok_path, 'w', encoding='utf-8') as out:
            out.write('<?xml version="1.0" encoding="utf-8"?>\n')
            out.write('<TEI xmlnsoff="http://www.tei-c.org/ns/1.0" xml:lang="cs">\n')

            # --- Write TEI Header Data ---
            out.write('  <teiHeader>\n')
            out.write('    <fileDesc>\n')
            out.write(f'      <titleStmt><title>{doc_id_safe}</title></titleStmt>\n')
            out.write('      <publicationStmt><p>Unpublished</p></publicationStmt>\n')

            source_info = alto_meta.get("source_image", "")
            if source_info:
                out.write(f'      <sourceDesc><p>Source image: {escape(source_info)}</p></sourceDesc>\n')
            else:
                out.write('      <sourceDesc><p>Unknown source</p></sourceDesc>\n')
            out.write('    </fileDesc>\n')

            # AFTER
            out.write('    <encodingDesc>\n')
            out.write('      <appInfo>\n')

            # UDPipe: prefer model name from CoNLL-U comments; fall back to passed arg
            udpipe_model_name = conllu_meta.get('udpipe_model') or model_udpipe or ''
            udpipe_generator = conllu_meta.get('generator', 'UDPipe')
            if udpipe_model_name or conllu_meta.get('generator'):
                out.write(
                    f'        <application ident="udpipe" version="2">'
                    f'<label>{escape(udpipe_generator)}</label>'
                    f'<desc>Model: {escape(udpipe_model_name)}</desc>'
                    f'</application>\n'
                )

            # NameTag NER: recorded from config MODEL_NAMETAG
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
                    f'<label>{escape(alto_meta["ocr_software"])} {escape(alto_meta.get("ocr_version", ""))}</label>'
                    f'</application>\n'
                )
            out.write('      </appInfo>\n')
            out.write('    </encodingDesc>\n')

            out.write('    <revisionDesc>\n')
            out.write(
                f'      <change when="{current_date}" who="altoconvert">Converted from ALTO file {escape(alto_filename)}</change>\n')
            if alto_meta.get('ocr_date') and alto_meta.get('ocr_software'):
                out.write(
                    f'      <change when="{escape(alto_meta["ocr_date"])}" who="{escape(alto_meta["ocr_software"])}">OCR processing</change>\n')
            if conllu_meta.get('generator'):
                out.write(
                    f'      <change when="{current_date}" who="udpipe">NLP enrichment by {escape(conllu_meta.get("generator"))}</change>\n')
            out.write('    </revisionDesc>\n')
            out.write('  </teiHeader>\n')

            if alto_pages:
                out.write('  <facsimile>\n')
                for pg in alto_pages:
                    surf_id = f'{doc_id_safe}.surface{pg["idx"]}'
                    facs_img = f'{doc_id_safe}-{pg["idx"]}.png'
                    lrx_attr = f' lrx="{pg["width"]}"' if pg.get('width') else ''
                    lry_attr = f' lry="{pg["height"]}"' if pg.get('height') else ''
                    out.write(f'    <surface id="{surf_id}"{lrx_attr}{lry_attr}>\n')
                    out.write(f'      <graphic url="{facs_img}"/>\n')
                    out.write('    </surface>\n')
                out.write('  </facsimile>\n')

            out.write('  <text>\n    <body>\n')

            current_page = 0
            current_block = None
            current_line = None

            for s_idx, sent in enumerate(sentences, start=1):
                first_bbox = next((t['_bbox'] for t in sent['tokens'] if t.get('_bbox')), None)

                # Check for pagination breaks
                sent_page_trigger = (sent.get('id') == '1')
                if first_bbox and first_bbox.get('page_idx') and first_bbox.get('page_idx') != current_page:
                    sent_page_trigger = True
                    new_page_num = first_bbox.get('page_idx')
                else:
                    new_page_num = current_page + 1 if sent_page_trigger else current_page

                if sent_page_trigger:
                    if current_block is not None:
                        out.write('      </div>\n')
                        current_block = None

                    current_page = new_page_num
                    pb_id = f'{doc_id_safe}.pb{current_page}'
                    facs_img = f'{doc_id_safe}-{current_page}.png'
                    out.write(f'      <pb n="{current_page}" id="{pb_id}" facs="{facs_img}"/>\n')

                    # Print mapped structural graphics linked to this page
                    if alto_graphics:
                        for g in alto_graphics:
                            if g['page_idx'] == current_page:
                                gid = escape(g['id']) if g.get('id') else f"{doc_id_safe}.g{hash(g['bbox']) % 10000}"
                                out.write(f'      <figure type="{escape(g["type"])}" id="{gid}" bbox="{g["bbox"]}"/>\n')

                # Check for classification/structural block shifts
                sent_block = first_bbox.get('block_id') if first_bbox else current_block
                if not sent_block: sent_block = f"block_{s_idx}"

                if sent_block != current_block:
                    if current_block is not None: out.write('      </div>\n')
                    current_block = sent_block
                    div_id = escape(f"{doc_id_safe}.{current_block}")

                    # Fetch block bbox mapped from ALTO
                    block_bbox = alto_blocks.get(current_block, "")
                    bbox_attr = f' bbox="{block_bbox}"' if block_bbox else ''
                    out.write(f'      <div type="MarginTextZone-P" id="{div_id}"{bbox_attr}>\n')

                sid = escape(f'{doc_id_safe}.s{s_idx}')
                text_attr = f' text="{_attr(sent["text"])}"' if sent.get('text') else ''
                out.write(f'        <s id="{sid}"{text_attr}>\n')

                id_map = {t['id']: f'{sid}.w{t["id"]}' for t in sent['tokens']}
                groups = _group_ner_spans(sent['tokens'])

                for grp in groups:
                    # Utility to output standard elements containing <lb> mapping to ALTO lines
                    def _emit_lb_if_changed(tk, base_indent):
                        nonlocal current_line
                        b = tk.get('_bbox')
                        if b and b.get('line_id') and b.get('line_id') != current_line:
                            current_line = b.get('line_id')
                            lb_id = escape(f"{doc_id_safe}.{current_line}")
                            lb_bbox = b.get('line_bbox', '')
                            # Adding standard <lb> based on the ALTO <TextLine> bounds
                            out.write(f'{" " * base_indent}<lb id="{lb_id}" bbox="{lb_bbox}"/>\n')

                    if grp['kind'] == 'name':
                        code = grp['code']
                        conll_cat = _CNEC_TO_CONLL.get(code, 'MISC')
                        out.write(f'          <name type="{escape(conll_cat)}" cnec="{escape(code)}">\n')
                        for tok in grp['tokens']:
                            _emit_lb_if_changed(tok, 12)
                            out.write('  ' + _tok_xml(tok, id_map, indent=12))
                        out.write('          </name>\n')
                    else:
                        tok = grp['tokens'][0]
                        _emit_lb_if_changed(tok, 10)
                        out.write(_tok_xml(tok, id_map, indent=10))

                out.write('        </s>\n')

            if current_block is not None:
                out.write('      </div>\n')

            out.write('    </body>\n  </text>\n</TEI>\n')
        return True
    except Exception as e:
        print(f'  [Error] Writing TEITOK {teitok_path}: {e}', file=sys.stderr)
        return False