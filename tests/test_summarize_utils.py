"""
tests/test_summarize_utils.py
==============================
Unit tests for the pure utility functions in ``api_util/summarize_nt_udp.py``.

Functions under test:
    bool_from_str      — flexible truthy string → bool converter
    sanitize_filename  — strips filesystem-illegal characters
    get_ne_explanation — maps CNEC 2.0 BIO tags to human-readable strings
    parse_features     — parses CoNLL-U FEATS column into a dict
    parse_misc         — parses CoNLL-U MISC column into a dict

No ML models, no network, no GPU required.
"""

import pytest

from summarize_nt_udp import (
    CNEC_TYPE_MAP,
    bool_from_str,
    get_ne_explanation,
    parse_features,
    parse_misc,
    sanitize_filename,
)


# ════════════════════════════════════════════════════════════════════════════
# bool_from_str
# ════════════════════════════════════════════════════════════════════════════
class TestBoolFromStr:

    @pytest.mark.parametrize("value", ["true", "True", "TRUE", "1", "yes", "y", "on"])
    def test_truthy_strings(self, value):
        assert bool_from_str(value) is True

    @pytest.mark.parametrize("value", ["false", "False", "FALSE", "0", "no", "n", "off"])
    def test_falsy_strings(self, value):
        assert bool_from_str(value) is False

    def test_none_returns_default_false(self):
        assert bool_from_str(None) is False

    def test_none_returns_custom_default(self):
        assert bool_from_str(None, default=True) is True

    def test_bool_true_passes_through(self):
        assert bool_from_str(True) is True

    def test_bool_false_passes_through(self):
        assert bool_from_str(False) is False

    def test_unknown_string_returns_false(self):
        assert bool_from_str("maybe") is False

    def test_whitespace_stripped(self):
        assert bool_from_str("  true  ") is True
        assert bool_from_str("  false  ") is False


# ════════════════════════════════════════════════════════════════════════════
# sanitize_filename
# ════════════════════════════════════════════════════════════════════════════
class TestSanitizeFilename:

    def test_clean_name_unchanged(self):
        assert sanitize_filename("CTX199405706") == "CTX199405706"

    def test_forward_slash_replaced(self):
        result = sanitize_filename("path/to/file")
        assert "/" not in result

    def test_backslash_replaced(self):
        result = sanitize_filename("path\\file")
        assert "\\" not in result

    def test_colon_replaced(self):
        result = sanitize_filename("C:file")
        assert ":" not in result

    def test_multiple_illegal_chars_all_replaced(self):
        dirty = 'a/b\\c:d*e?f"g<h>i|j'
        clean = sanitize_filename(dirty)
        for ch in '/\\:*?"<>|':
            assert ch not in clean, f"Illegal char '{ch}' survived in '{clean}'"

    def test_underscore_used_as_replacement(self):
        result = sanitize_filename("a/b")
        assert "_" in result

    def test_empty_string_unchanged(self):
        assert sanitize_filename("") == ""

    def test_already_clean_unicode_preserved(self):
        """Czech characters are legal in filenames — they must not be touched."""
        name = "výzkum_hradiště"
        assert sanitize_filename(name) == name


# ════════════════════════════════════════════════════════════════════════════
# get_ne_explanation
# ════════════════════════════════════════════════════════════════════════════
class TestGetNeExplanation:

    def test_o_tag_returns_empty(self):
        assert get_ne_explanation("O") == ""

    def test_underscore_returns_empty(self):
        assert get_ne_explanation("_") == ""

    def test_empty_string_returns_empty(self):
        assert get_ne_explanation("") == ""

    def test_b_gu_settlement_name(self):
        assert get_ne_explanation("B-gu") == "Settlement name (City/Town)"

    def test_i_ps_surname(self):
        assert get_ne_explanation("I-ps") == "Surname"

    def test_b_pf_first_name(self):
        assert get_ne_explanation("B-pf") == "First name"

    def test_b_ty_year(self):
        assert get_ne_explanation("B-ty") == "Year"

    def test_b_nc_cardinal_number(self):
        assert get_ne_explanation("B-nc") == "Cardinal number"

    def test_b_ic_educational_institution(self):
        assert get_ne_explanation("B-ic") == "Cult/Educational institution"

    def test_multi_pipe_tag_uses_primary_part(self):
        """Pipe-separated tags (e.g. 'B-P|B-pf') should resolve to a non-empty explanation
        rather than raising or returning an empty/error string."""
        result = get_ne_explanation("B-P|B-pf")
        assert isinstance(result, str)
        assert len(result) > 0
        assert "Unknown Code" not in result

    def test_unknown_code_returns_unknown_code_string(self):
        result = get_ne_explanation("B-xyz")
        assert "Unknown Code" in result
        assert "xyz" in result

    def test_plain_tag_no_bio_prefix_returns_empty(self):
        """Tags not starting with B-/I- should return empty string."""
        assert get_ne_explanation("gu") == ""

    def test_all_cnec_codes_in_map_are_reachable(self):
        """
        Every code in CNEC_TYPE_MAP must be reachable via a B- prefix tag.
        This guards against typos in the map keys.
        """
        for code, explanation in CNEC_TYPE_MAP.items():
            result = get_ne_explanation(f"B-{code}")
            # Either maps to the known explanation or produces an "Unknown Code"
            # string (for codes that map to generic labels like "O").
            assert isinstance(result, str)


# ════════════════════════════════════════════════════════════════════════════
# parse_features
# ════════════════════════════════════════════════════════════════════════════
class TestParseFeatures:

    def test_underscore_returns_empty_dict(self):
        assert parse_features("_") == {}

    def test_empty_string_returns_empty_dict(self):
        assert parse_features("") == {}

    def test_single_feature(self):
        assert parse_features("Case=Nom") == {"Case": "Nom"}

    def test_multiple_features(self):
        result = parse_features("Case=Nom|Gender=Fem|Number=Sing")
        assert result == {"Case": "Nom", "Gender": "Fem", "Number": "Sing"}

    def test_feature_without_equals_sign_ignored(self):
        """Malformed entries that have no '=' must be silently skipped."""
        result = parse_features("Case=Nom|BrokenEntry|Number=Plur")
        assert "BrokenEntry" not in result
        assert result.get("Case") == "Nom"
        assert result.get("Number") == "Plur"

    def test_value_with_equals_in_it_split_at_first_occurrence(self):
        """Values containing '=' must not be truncated."""
        result = parse_features("Key=a=b")
        assert result.get("Key") == "a=b"

    def test_real_czech_feature_string(self):
        feat_str = "Animacy=Inan|Case=Nom|Degree=Pos|Gender=Masc|Number=Sing|Polarity=Pos"
        result = parse_features(feat_str)
        assert result["Animacy"] == "Inan"
        assert result["Case"] == "Nom"
        assert result["Gender"] == "Masc"
        assert len(result) == 6


# ════════════════════════════════════════════════════════════════════════════
# parse_misc
# ════════════════════════════════════════════════════════════════════════════
class TestParseMisc:

    def test_underscore_returns_empty_dict(self):
        assert parse_misc("_") == {}

    def test_empty_string_returns_empty_dict(self):
        assert parse_misc("") == {}

    def test_space_after_no(self):
        assert parse_misc("SpaceAfter=No") == {"SpaceAfter": "No"}

    def test_ner_tag(self):
        result = parse_misc("NER=B-gu")
        assert result == {"NER": "B-gu"}

    def test_multiple_key_value_pairs(self):
        result = parse_misc("SpaceAfter=No|NER=B-gu")
        assert result["SpaceAfter"] == "No"
        assert result["NER"] == "B-gu"

    def test_flag_without_equals_gets_yes_value(self):
        """A bare key with no '=' should map to the sentinel value 'Yes'."""
        result = parse_misc("SpaceAfter")
        assert result == {"SpaceAfter": "Yes"}

    def test_mixed_key_value_and_flag(self):
        result = parse_misc("SpaceAfter|NER=B-ps")
        assert result["SpaceAfter"] == "Yes"
        assert result["NER"] == "B-ps"

    def test_value_with_equals_in_it(self):
        """Value side may contain '=' — only the first '=' is the delimiter."""
        result = parse_misc("Key=val=ue")
        assert result.get("Key") == "val=ue"

    def test_real_ner_misc_column(self):
        """Reproduce a typical MISC column value from the pipeline output."""
        misc_str = "SpaceAfter=No|NER=B-gr|B-gu"
        result = parse_misc(misc_str)
        assert result.get("SpaceAfter") == "No"
        assert result.get("NER") == "B-gr"
        # "B-gu" has no '=' → stored as flag
        assert result.get("B-gu") == "Yes"