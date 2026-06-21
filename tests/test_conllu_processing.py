"""
tests/test_conllu_processing.py
================================
Unit tests for CoNLL-U processing helpers in ``api_util/``.

Modules under test:
    call_udpipe.merge_conllu_chunks   — pure string → string transform
    call_nametag._get_ne_suffix       — pure tag string helper
    call_nametag.build_sent_page_map  — CoNLL-U file → page-number list

No ML models, no network, no GPU required.
"""

from api_util.call_nametag import _get_ne_suffix, build_sent_page_map
from api_util.call_udpipe import merge_conllu_chunks

# ─────────────────────────────────────────────────────────────────────────────
# Inline chunk content helpers
# ─────────────────────────────────────────────────────────────────────────────


def _chunk(sent_ids: list[int], suffix: str = "") -> str:
    """Build a minimal CoNLL-U chunk with the given sentence IDs."""
    lines = []
    for sid in sent_ids:
        lines.append(f"# sent_id = {sid}\n")
        lines.append(f"# text = Sentence {sid}.{suffix}\n")
        lines.append(f"1\tWord{sid}\tword\tNOUN\t_\t_\t0\troot\t_\t_\n")
        lines.append("2\t.\t.\tPUNCT\t_\t_\t1\tpunct\t_\tSpaceAfter=No\n")
        lines.append("\n")
    return "".join(lines)


# ════════════════════════════════════════════════════════════════════════════
# merge_conllu_chunks
# ════════════════════════════════════════════════════════════════════════════
class TestMergeConlluChunks:
    def test_empty_list_returns_empty_string(self):
        assert merge_conllu_chunks([]) == ""

    def test_single_chunk_sent_ids_unchanged(self):
        chunk = _chunk([1, 2, 3])
        merged = merge_conllu_chunks([chunk])
        # All three sent_id lines must appear with their original values
        for i in (1, 2, 3):
            assert f"# sent_id = {i}\n" in merged

    def test_single_chunk_no_page_break_injected(self):
        chunk = _chunk([1, 2])
        merged = merge_conllu_chunks([chunk])
        assert "# page_break = true" not in merged

    def test_two_chunks_second_offset(self):
        """
        Chunk 1 has sent_ids [1, 2]; chunk 2 has [1, 2].
        After merge the second chunk must be renumbered [3, 4].
        """
        merged = merge_conllu_chunks([_chunk([1, 2]), _chunk([1, 2])])
        for i in (1, 2, 3, 4):
            assert f"# sent_id = {i}\n" in merged
        # Original sent_id = 1 from chunk 2 should no longer appear unshifted
        # (it was sent_id=1 but offset by 2 → now 3)
        lines = merged.splitlines()
        sent_id_lines = [ln for ln in lines if ln.startswith("# sent_id")]
        values = [int(val.split("=")[1].strip()) for val in sent_id_lines]
        assert values == [1, 2, 3, 4]

    def test_page_break_injected_before_first_sentence_of_second_chunk(self):
        """
        A ``# page_break = true`` comment must appear immediately before the
        renumbered ``# sent_id`` of the first sentence of every non-first chunk.
        """
        merged = merge_conllu_chunks([_chunk([1, 2]), _chunk([1])])
        lines = merged.splitlines()
        pb_positions = [i for i, pp in enumerate(lines) if pp == "# page_break = true"]
        sid3_positions = [i for i, sp in enumerate(lines) if sp == "# sent_id = 3"]
        assert len(pb_positions) == 1
        assert len(sid3_positions) == 1
        # page_break must be the line immediately before sent_id = 3
        assert pb_positions[0] == sid3_positions[0] - 1

    def test_no_page_break_before_first_chunk_first_sentence(self):
        """
        The very first sentence of the whole document (chunk 0, sent_id = 1)
        must never be preceded by a page_break marker.
        """
        merged = merge_conllu_chunks([_chunk([1, 2]), _chunk([1])])
        lines = merged.splitlines()
        first_sid_pos = next(i for i, ln in enumerate(lines) if ln.startswith("# sent_id"))
        if first_sid_pos > 0:
            assert lines[first_sid_pos - 1] != "# page_break = true"

    def test_three_chunks_two_page_breaks(self):
        merged = merge_conllu_chunks([_chunk([1]), _chunk([1]), _chunk([1])])
        pb_count = merged.count("# page_break = true")
        assert pb_count == 2

    def test_non_sent_id_lines_pass_through_unchanged(self):
        """Comment lines that are not ``# sent_id`` must be left verbatim."""
        chunk = "# newdoc\n# sent_id = 1\n# text = Hello.\n1\tHello\thello\tNOUN\t_\t_\t0\troot\t_\t_\n\n"
        merged = merge_conllu_chunks([chunk])
        assert "# newdoc\n" in merged
        assert "# text = Hello.\n" in merged

    def test_chunk_with_no_sent_ids_passes_through(self):
        """A chunk containing only comments and token lines must not crash."""
        chunk = "# newdoc\n1\tWord\tword\tNOUN\t_\t_\t0\troot\t_\t_\n\n"
        merged = merge_conllu_chunks([chunk])
        assert "Word" in merged

    def test_merged_output_ends_with_final_chunk_content(self):
        c1 = _chunk([1, 2])
        c2 = _chunk([1], suffix="_final")
        merged = merge_conllu_chunks([c1, c2])
        assert "_final" in merged


# ════════════════════════════════════════════════════════════════════════════
# _get_ne_suffix
# ════════════════════════════════════════════════════════════════════════════
class TestGetNeSuffix:
    def test_empty_string_returns_empty(self):
        assert _get_ne_suffix("") == ""

    def test_none_equivalent_empty_returns_empty(self):
        # Function only receives strings — test falsy empty string
        assert _get_ne_suffix("") == ""

    def test_bio_tag_b_prefix(self):
        assert _get_ne_suffix("B-gu") == "gu"

    def test_bio_tag_i_prefix(self):
        assert _get_ne_suffix("I-ps") == "ps"

    def test_o_tag_returns_empty_string_suffix(self):
        """'O' doesn't start with B-/I- so suffix appended is ''."""
        result = _get_ne_suffix("O")
        # A single part "O" → no B-/I- → appends "" → joins to ""
        assert result == ""

    def test_multi_tag_pipe_separated(self):
        """'B-P|B-pf' → two parts → 'P|pf'."""
        assert _get_ne_suffix("B-P|B-pf") == "P|pf"

    def test_mixed_bio_tags(self):
        """'I-gu|B-gh' → 'gu|gh'."""
        assert _get_ne_suffix("I-gu|B-gh") == "gu|gh"

    def test_plain_tag_no_dash_prefix(self):
        """Tag with no B-/I- prefix produces empty suffix for that part."""
        result = _get_ne_suffix("plain")
        assert result == ""

    def test_three_part_tag(self):
        """'B-A|B-ic|B-gu' → 'A|ic|gu'."""
        assert _get_ne_suffix("B-A|B-ic|B-gu") == "A|ic|gu"


# ════════════════════════════════════════════════════════════════════════════
# build_sent_page_map
# ════════════════════════════════════════════════════════════════════════════
class TestBuildSentPageMap:
    def test_single_page_all_mapped_to_page_1(self, sample_conllu):
        """
        ``sample.conllu`` has 3 sentences (sent_id 1,2,3) all on one page.
        The first ``# sent_id = 1`` triggers page 1; subsequent ones stay on 1.
        """
        page_map = build_sent_page_map(sample_conllu)
        assert page_map == [1, 1, 1]

    def test_sent_id_reset_starts_new_page(self, two_page_conllu):
        """
        Legacy two-page file: sent_ids 1,2 then reset to 1,2.
        Expected page map: [1, 1, 2, 2].
        """
        page_map = build_sent_page_map(two_page_conllu)
        assert page_map == [1, 1, 2, 2]

    def test_page_break_comment_starts_new_page(self, page_break_conllu):
        """
        Merged file using explicit ``# page_break = true`` markers.
        sent_ids 1,2,3,4 where page_break precedes sent_id 3.
        Expected page map: [1, 1, 2, 2].
        """
        page_map = build_sent_page_map(page_break_conllu)
        assert page_map == [1, 1, 2, 2]

    def test_empty_file_returns_sentinel_page_1(self, empty_conllu):
        """No sent_id markers → returns [1] as the fallback sentinel."""
        page_map = build_sent_page_map(empty_conllu)
        assert page_map == [1]

    def test_nonexistent_file_returns_sentinel(self, tmp_path):
        page_map = build_sent_page_map(str(tmp_path / "missing.conllu"))
        assert page_map == [1]

    def test_page_numbers_are_monotonically_non_decreasing(self, two_page_conllu):
        """Page numbers must never decrease across the sentence list."""
        page_map = build_sent_page_map(two_page_conllu)
        for a, b in zip(page_map, page_map[1:], strict=True):
            assert b >= a, f"Page number decreased: {a} → {b}"

    def test_result_length_equals_sentence_count(self, sample_conllu):
        """The map must have exactly one entry per sentence in the file."""
        page_map = build_sent_page_map(sample_conllu)
        # sample.conllu has 3 sentences
        assert len(page_map) == 3

    def test_inline_page_break_increments_exactly_once(self, tmp_path):
        """
        A single ``# page_break = true`` should increment the page counter
        exactly once, regardless of how many sentences follow on that page.
        """
        content = (
            "# sent_id = 1\n1\tA\ta\tNOUN\t_\t_\t0\troot\t_\t_\n\n"
            "# page_break = true\n"
            "# sent_id = 2\n1\tB\tb\tNOUN\t_\t_\t0\troot\t_\t_\n\n"
            "# sent_id = 3\n1\tC\tc\tNOUN\t_\t_\t0\troot\t_\t_\n\n"
        )
        conllu_path = tmp_path / "inline.conllu"
        conllu_path.write_text(content, encoding="utf-8")
        page_map = build_sent_page_map(str(conllu_path))
        assert page_map == [1, 2, 2]
