import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# Inject project root into sys.path so the vendored, hub-canonical `atrium_document`
# resolves when this module is reached with only api_util/ on the path (keywords.py and
# llm_utils.py both insert api_util/ before importing it). Same idiom as
# api_util/summarize_nt_udp.py.
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from atrium_document import canonical_doc_id  # noqa: E402


def doc_id_from_path(path: str | Path) -> str:
    """Strips .conllu or .teitok.xml to produce a clean document ID.

    Delegates to `atrium_document.canonical_doc_id()` — the one derivation the whole
    ecosystem shares (issue atrium-project#10, D3). Kept as a named wrapper because
    keywords.py, llm_utils.py and tests/test_teitok_read.py all call it, so one change
    point moves every caller.

    The literal-length slices this used to do (`name[:-7]` for ".conllu") stripped only
    the LAST suffix, so `X.udpipe.conllu` — the working currency of this repo's own
    UDPipe stage — became doc_id "X.udpipe" while every other stage called the same
    document "X", forking the accretion record in two. canonical_doc_id() matches the
    longest known pipeline suffix first (KNOWN_PIPELINE_SUFFIXES), which is exactly the
    ordering that case needs.
    """
    return canonical_doc_id(path)


def read_teitok_rows(path: str | Path) -> list[dict]:
    """
    Parses TEITOK XML.
    Returns: list of dicts [{"page_num": int, "line_num": int, "text": str}]
    """
    tree = ET.parse(path)
    root = tree.getroot()
    rows = []

    page_num = 1
    line_num = 1

    for elem in root.iter():
        tag = elem.tag.split("}")[-1]  # Namespace agnostic

        if tag == "pb":
            page_num = int(elem.get("n", page_num + 1))
        elif tag == "lb":
            line_num += 1
        elif tag == "s":
            text = elem.get("text")

            # If @text is missing, fallback to joining <tok> elements
            if not text:
                toks = []
                for tok in elem.iter():
                    tok_tag = tok.tag.split("}")[-1]
                    if tok_tag == "tok":
                        toks.append(tok.text or "")
                        if tok.get("join") != "right" and tok.get("spaceAfter") != "No":
                            toks.append(" ")
                text = "".join(toks).strip()

            if text:
                rows.append({"page_num": page_num, "line_num": line_num, "text": text})

    return rows


def read_teitok_text(path: str | Path) -> str:
    """Returns the surface text as a single string."""
    rows = read_teitok_rows(path)
    return "\n".join(r["text"] for r in rows)


def read_teitok_tokens(path: str | Path) -> list[dict]:
    """
    Returns token-level annotations.
    Returns: list of dicts [{"form", "lemma", "upos", "space_after"}]
    """
    tree = ET.parse(path)
    root = tree.getroot()
    tokens = []

    for tok in root.iter():
        tag = tok.tag.split("}")[-1]
        if tag == "tok":
            tokens.append(
                {
                    "form": tok.text or "",
                    "lemma": tok.get("lemma", ""),
                    "upos": tok.get("pos", tok.get("type", "")),
                    "space_after": tok.get("join") != "right" and tok.get("spaceAfter") != "No",
                }
            )

    return tokens
