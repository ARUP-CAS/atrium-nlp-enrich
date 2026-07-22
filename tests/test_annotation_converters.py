"""
tests/test_annotation_converters.py
===================================
Round-trip coverage for the doccano pre-annotation kit added in v0.16.2
(issue #18): CoNLL-U → doccano JSONL example (``annotation/conllu_to_doccano.py``)
and doccano export → NameTag-3 IOB2 (``annotation/doccano_to_iob2.py``).

Both modules are pure-Python (no ML deps), so these run on the fast lane.
The central invariant: because ``conllu_to_doccano`` builds ``text`` as a
deterministic space/newline join of the tokens, ``doccano_to_iob2`` re-tokenises
to exactly the same tokens, so the IOB2 tags survive the round trip.
"""

from annotation.conllu_to_doccano import document_to_example, parse_conllu
from annotation.doccano_to_iob2 import assign_tags, extract_spans, tokenize

_CONLLU = """# newdoc id = DOC1
# sent_id = 1
1\tbronzový\t_\t_\t_\t_\t_\t_\t_\tNER=B-MATERIAL
2\tnůž\t_\t_\t_\t_\t_\t_\t_\tNER=B-ARTEFACT
3\t.\t_\t_\t_\t_\t_\t_\t_\tNER=O

"""


def _conllu_to_example(text):
    import io

    docs = parse_conllu(io.StringIO(text), ner_from_misc=True)
    assert len(docs) == 1
    doc_id, doc = docs[0]
    return doc_id, document_to_example(doc, doc_id)


def test_conllu_parses_ner_from_misc():
    doc_id, example = _conllu_to_example(_CONLLU)
    assert doc_id == "DOC1"
    assert example["text"] == "bronzový nůž ."
    # Two entity spans reconstructed from the B- tags.
    assert [lbl[2] for lbl in example["label"]] == ["MATERIAL", "ARTEFACT"]
    assert example["meta"]["doc_id"] == "DOC1"


def test_spans_land_on_the_right_tokens():
    _doc_id, example = _conllu_to_example(_CONLLU)
    text = example["text"]
    for start, end, typ in example["label"]:
        substr = text[start:end]
        if typ == "MATERIAL":
            assert substr == "bronzový"
        elif typ == "ARTEFACT":
            assert substr == "nůž"


def test_full_round_trip_recovers_tags():
    """CoNLL-U → doccano example → IOB2 tags must equal the original tags."""
    _doc_id, example = _conllu_to_example(_CONLLU)
    spans = extract_spans(example)
    tokens = tokenize(example["text"])
    tags = assign_tags(tokens, spans)
    forms = [t[2] for t in tokens]
    assert forms == ["bronzový", "nůž", "."]
    assert tags == ["B-MATERIAL", "B-ARTEFACT", "O"]


def test_multi_token_entity_gets_b_then_i():
    example = {"text": "starý hrad", "label": [[0, 10, "CONTEXT"]]}
    tokens = tokenize(example["text"])
    tags = assign_tags(tokens, extract_spans(example))
    assert tags == ["B-CONTEXT", "I-CONTEXT"]


def test_overlapping_spans_pipe_join():
    """Two spans on the same token → sorted, ``|``-joined IOB2 label."""
    example = {"text": "bronz", "label": [[0, 5, "MATERIAL"], [0, 5, "ARTEFACT"]]}
    tokens = tokenize(example["text"])
    tags = assign_tags(tokens, extract_spans(example))
    assert tags == ["B-ARTEFACT|B-MATERIAL"]


def test_extract_spans_object_form():
    """doccano's object-form spans (start_offset/end_offset/label) are tolerated."""
    obj = {"label": [{"start_offset": 0, "end_offset": 5, "label": "PERIOD"}]}
    assert extract_spans(obj) == [(0, 5, "PERIOD")]


def test_extract_spans_empty():
    assert extract_spans({"text": "no labels here", "label": []}) == []
