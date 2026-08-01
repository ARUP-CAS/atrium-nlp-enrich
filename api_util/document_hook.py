import logging
from typing import Any, Dict, List, Optional

from atrium_document import DocumentRecord

from .teitok_alto import CNEC_TO_CONLL, group_ner_spans, parse_and_align_conllu

logger = logging.getLogger(__name__)


def _augment_tokens_with_position(tokens: List[dict]) -> None:
    """
    Attach ``_page`` (str), ``_line`` (int, 1-based, sequential per page) and
    ``_char_start``/``_char_end`` (offset within that line's reconstructed text) to
    each token IN PLACE, walking ``tokens`` in document order.

    ALTO's own ``line_id`` (carried on ``tok["_bbox"]["line_id"]`` by
    ``parse_and_align_conllu``/``_align_tokens_to_alto``) is an arbitrary XML ID, not
    a sequential number — it is only used here to detect "still the same line as the
    previous token" while assigning a stable per-page sequential line number as new
    line_ids are first encountered in token order. This is internally consistent
    across re-runs of nlp-enrich against the same CoNLL-U/ALTO pair (which is what
    the entities[] merge key needs — it does not need to match alto-postprocess's own
    line numbering, since nlp-enrich is entities[]'s sole owner).

    Tokens with no matched ALTO bbox (alignment miss, or no ALTO supplied at all) are
    grouped onto one synthetic per-page line rather than dropped, so entities from an
    unaligned document still get a valid (page, line) pair instead of crashing later.
    """
    page_line_seq: Dict[str, Dict[Any, int]] = {}
    page_next_line: Dict[str, int] = {}
    last_key = None
    cursor = 0

    for tok in tokens:
        bbox = tok.get("_bbox") or {}
        page_idx = bbox.get("page_idx")
        line_id = bbox.get("line_id")
        page = str(page_idx) if page_idx is not None else "1"
        line_marker = line_id if line_id is not None else f"__noalign_{page}"

        page_line_seq.setdefault(page, {})
        page_next_line.setdefault(page, 1)
        if line_marker not in page_line_seq[page]:
            page_line_seq[page][line_marker] = page_next_line[page]
            page_next_line[page] += 1
        line_num = page_line_seq[page][line_marker]

        key = (page, line_marker)
        if key != last_key:
            cursor = 0

        form = tok.get("form", "")
        tok["_page"] = page
        tok["_line"] = line_num
        tok["_char_start"] = cursor
        tok["_char_end"] = cursor + len(form)

        cursor = tok["_char_end"] + (1 if tok.get("space_after", True) else 0)
        last_key = key


def run_document_hook(
        doc_id: str,
        teitok_path: str,
        conllu_path: str,
        baseline_json: Optional[str],
        out_json: str,
        run_id: str,
        paradata_ref: str,
        license_detail: Dict[str, Any],
        alto_path: Optional[str] = None,
        include_lines: bool = False,
):
    """
    Integrates nlp-enrich outputs (entities, TEITOK refs) into the AtriumDocument pair.

    ``alto_path`` is required to recover page/line/bbox for each token — without it
    ``parse_and_align_conllu`` returns no ALTO strings to align against, and every
    token falls back to page "1" with a synthetic, unaligned line.
    """
    # 1. Parse tokens & bboxes (reusing the unified teitok_alto refactor). This
    # returns {"sentences": [...], ...} — NOT a flat token list — so it must be
    # flattened the same way parse_and_align_conllu itself does internally before
    # bbox alignment.
    parsed = parse_and_align_conllu(conllu_path, alto_path=alto_path, doc_id=doc_id)
    tokens: List[dict] = (
        [tok for sent in parsed["sentences"] for tok in sent["tokens"]] if parsed else []
    )
    _augment_tokens_with_position(tokens)

    entities = []
    name_counter = 1

    spans = group_ner_spans(tokens)

    for span in spans:
        if span.get("kind") != "name":
            continue  # non-entity tokens are grouped too; only "name" spans are entities

        span_tokens = span["tokens"]
        surface = " ".join(t["form"] for t in span_tokens)
        lemma = " ".join(t["lemma"] for t in span_tokens)

        x_mins = [float(t["_bbox"]["left"]) for t in span_tokens if t.get("_bbox")]
        y_mins = [float(t["_bbox"]["top"]) for t in span_tokens if t.get("_bbox")]
        x_maxs = [float(t["_bbox"]["right"]) for t in span_tokens if t.get("_bbox")]
        y_maxs = [float(t["_bbox"]["bottom"]) for t in span_tokens if t.get("_bbox")]

        bbox = None
        if x_mins and y_mins and x_maxs and y_maxs:
            bbox = [min(x_mins), min(y_mins), max(x_maxs), max(y_maxs)]

        first, last = span_tokens[0], span_tokens[-1]
        page = first.get("_page", "1")
        line = first.get("_line", 0)
        char_span = [first.get("_char_start", 0), last.get("_char_end", 0)]

        # span["code"] is the BIO-stripped NE code (_bio_to_code), valid for either
        # tagset. Membership in CNEC_TO_CONLL is the precise tagset test — CNEC codes
        # (p, pf, gc, i, ia, ...) never collide with OntoNotes labels (PERSON, ORG, ...).
        raw_type = span.get("code", "")
        is_cnec = raw_type in CNEC_TO_CONLL
        type_onto = raw_type if not is_cnec else None
        type_cnec = raw_type if is_cnec else None

        type_teitok = "MISC"
        if is_cnec and raw_type in CNEC_TO_CONLL:
            type_teitok = CNEC_TO_CONLL[raw_type]
        elif not is_cnec:
            if raw_type == "PERSON":
                type_teitok = "PER"
            elif raw_type in ["ORG", "LOC"]:
                type_teitok = raw_type
            elif raw_type in ["GPE", "FAC"]:
                type_teitok = "LOC"

        entity_record = {
            "surface": surface,
            "lemma": lemma,
            "type_onto": type_onto,
            "type_cnec": type_cnec,
            "type_teitok": type_teitok,
            "page": str(page),
            "line": int(line),
            "char_span": char_span,
            "teitok_ref": f"{doc_id}.name{name_counter}",
            "pid": {"wikidata": None, "geonames": None, "aat": None, "amcr": None},
        }
        if bbox:
            entity_record["bbox"] = bbox

        entities.append(entity_record)
        name_counter += 1

    # 2. Document Integration via Accretion
    with DocumentRecord.open(doc_id, "nlp-enrich",
                             baseline=baseline_json,
                             run_id=run_id,
                             paradata_ref=paradata_ref) as doc:

        if license_detail:
            doc.add_license_detail(license_detail)

        if teitok_path:
            doc.add_derived_from("teitok", teitok_path)

        # entities[] is field-split (translator writes translation_en, llm-enrich
        # writes pid) — merge_block(), never set_block(), or a re-run of nlp-enrich
        # alone erases every downstream contribution on the same rows.
        doc.merge_block("entities", entities)

        pages_present = sorted({t.get("_page", "1") for t in tokens})
        page_updates = [
            {"page": p, "teitok_surface": f"{doc_id}.surface{p}"}
            for p in pages_present
        ]
        doc.merge_block("pages", page_updates)

        if include_lines:
            logger.warning(
                "DANGER: `lines` block merged from nlp-enrich. This may cause silent duplicate rows due to layout reordering mismatches with alto-postprocess.")
            # Line extraction logic omitted by default to protect the integrity of the OCR pipeline layout
            pass

        # Rule: the caller decides where the record lands (in-place accretion or a
        # fresh path) — write there explicitly rather than falling through to
        # __exit__'s implicit out_dir="." default, which silently drops the record
        # in the process CWD instead of the shared document_json_dir.
        doc.finalize(out_json)
