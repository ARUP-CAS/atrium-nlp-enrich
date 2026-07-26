import logging
from typing import Any, Dict, Optional

from atrium_document import DocumentRecord

from .teitok_alto import CNEC_TO_CONLL, group_ner_spans, parse_and_align_conllu

logger = logging.getLogger(__name__)


def run_document_hook(
        doc_id: str,
        teitok_path: str,
        conllu_path: str,
        baseline_json: Optional[str],
        out_json: str,
        run_id: str,
        paradata_ref: str,
        license_detail: Dict[str, Any],
        include_lines: bool = False
):
    """
    Integrates nlp-enrich outputs (entities, TEITOK refs) into the AtriumDocument pair.
    """
    # 1. Parse tokens & bboxes (reusing the unified teitok_alto refactor)
    tokens = parse_and_align_conllu(conllu_path)

    # 2. Extract entities and detect tagset
    is_cnec = any(t.get("misc", "").find("NE=") >= 0 for t in tokens if "misc" in t)

    entities = []
    name_counter = 1

    # FIX: group_ner_spans takes 1 argument
    spans = group_ner_spans(tokens)

    for span in spans:
        surface = " ".join(t["form"] for t in span["tokens"])
        lemma = " ".join(t["lemma"] for t in span["tokens"])

        # Bounding box union
        x_mins = [float(t["bbox"][0]) for t in span["tokens"] if t.get("bbox")]
        y_mins = [float(t["bbox"][1]) for t in span["tokens"] if t.get("bbox")]
        x_maxs = [float(t["bbox"][2]) for t in span["tokens"] if t.get("bbox")]
        y_maxs = [float(t["bbox"][3]) for t in span["tokens"] if t.get("bbox")]

        bbox = None
        if x_mins and y_mins and x_maxs and y_maxs:
            bbox = [min(x_mins), min(y_mins), max(x_maxs), max(y_maxs)]

        page = span["tokens"][0].get("page", "1")
        line = span["tokens"][0].get("line", 1)

        raw_type = span["type"]
        type_onto = raw_type if not is_cnec else None
        type_cnec = raw_type if is_cnec else None

        # Coarse TEITOK mapping
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
            "char_span": None,  # Derived later if needed by LLM
            "teitok_ref": f"{doc_id}.name{name_counter}",
            "pid": {"wikidata": None, "geonames": None, "aat": None, "amcr": None}
        }
        if bbox:
            entity_record["bbox"] = bbox

        entities.append(entity_record)
        name_counter += 1

    # 3. Document Integration via Accretion
    with DocumentRecord.open(doc_id, "nlp-enrich",
                             baseline=baseline_json,
                             run_id=run_id,
                             paradata_ref=paradata_ref) as doc:

        if license_detail:
            doc.add_license_detail(license_detail)

        if teitok_path:
            doc.add_derived_from("teitok", teitok_path)

        doc.set_block("entities", entities)

        pages_present = sorted(list(set(t.get("page", "1") for t in tokens)))
        page_updates = [
            {"page": str(p), "teitok_surface": f"{doc_id}.surface{p}"}
            for p in pages_present
        ]
        doc.merge_block("pages", page_updates)

        if include_lines:
            logger.warning(
                "DANGER: `lines` block merged from nlp-enrich. This may cause silent duplicate rows due to layout reordering mismatches with alto-postprocess.")
            # Line extraction logic omitted by default to protect the integrity of the OCR pipeline layout
            pass
