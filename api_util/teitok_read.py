import xml.etree.ElementTree as ET
from pathlib import Path


def doc_id_from_path(path: str | Path) -> str:
    """Strips .conllu or .teitok.xml to produce a clean document ID."""
    name = Path(path).name
    if name.lower().endswith(".teitok.xml"):
        return name[:-11]
    if name.lower().endswith(".conllu"):
        return name[:-7]
    return Path(path).stem


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
