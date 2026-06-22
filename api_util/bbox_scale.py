import re

BBOX_RE = re.compile(r'bbox="([^"]+)"')
SURFACE_RE = re.compile(r'<surface([^>]+)>')

def unit_per_inch(unit):
    if unit == 'inch1200':
        return 1200.0
    if unit == 'mm10':
        return 254.0
    return None

def dpi_scale(unit, dpi, alto_dpi=None):
    if not dpi:
        return 1.0, 1.0
    upi = unit_per_inch(unit)
    if upi:
        return float(dpi) / upi, float(dpi) / upi
    if unit == 'pixel':
        adpi = alto_dpi or dpi
        if not adpi:
            return 1.0, 1.0
        return float(dpi) / float(adpi), float(dpi) / float(adpi)
    return 1.0, 1.0

def scale_bbox_coords(value, sx, sy, dx=0, dy=0):
    parts = value.split()
    if len(parts) != 4:
        return value
    try:
        x, y, w, h = map(float, parts)
        nx = round((x - dx) * sx)
        ny = round((y - dy) * sy)
        nw = round(w * sx)
        nh = round(h * sy)
        return f"{nx} {ny} {nw} {nh}"
    except ValueError:
        return value

def fix_name_close_tags(text):
    return text.replace("</n>", "</name>")

def set_surface_extent(text, w, h):
    def repl(m):
        attrs = m.group(1)
        attrs = re.sub(r'\s*lrx="[^"]+"', '', attrs)
        attrs = re.sub(r'\s*lry="[^"]+"', '', attrs)
        return f'<surface lrx="{w}" lry="{h}"{attrs}>'
    return SURFACE_RE.sub(repl, text)

def rewrite_bboxes(text, scale_fn):
    def repl(m):
        return f'bbox="{scale_fn(m.group(1))}"'
    return BBOX_RE.sub(repl, text)

def detect_source_size(text):
    m = SURFACE_RE.search(text)
    if not m:
        return None, None
    attrs = m.group(1)
    lrx_m = re.search(r'lrx="([^"]+)"', attrs)
    lry_m = re.search(r'lry="([^"]+)"', attrs)
    if lrx_m and lry_m:
        try:
            return float(lrx_m.group(1)), float(lry_m.group(1))
        except ValueError:
            pass
    return None, None
