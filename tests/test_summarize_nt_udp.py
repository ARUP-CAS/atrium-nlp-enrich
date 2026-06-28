from api_util.summarize_nt_udp import get_ne_explanation


class TestNameTagExplanationMapping:

    def test_native_onto_tags(self):
        """Test that native ONTO tags are correctly resolved to their descriptions."""
        assert get_ne_explanation("B-PERSON") == "People, including fictional"
        assert get_ne_explanation("I-ORG") == "Companies, agencies, institutions, etc."
        assert get_ne_explanation("B-GPE") == "Countries, cities, states"

    def test_legacy_cnec_mapped_to_onto(self):
        """Test that legacy CNEC tags are correctly bridged to ONTO descriptions."""
        # 'p' -> 'PERSON'
        assert get_ne_explanation("B-p") == "People, including fictional"
        # 'i' -> 'ORG'
        assert get_ne_explanation("I-i") == "Companies, agencies, institutions, etc."
        # 'g' -> 'GPE'
        assert get_ne_explanation("B-g") == "Countries, cities, states"

    def test_complex_tag_strings(self):
        """Test that compound tags separated by pipes correctly read the primary tag."""
        assert get_ne_explanation("B-PERSON|I-p") == "People, including fictional"
        assert get_ne_explanation("I-g|B-GPE") == "Countries, cities, states"

    def test_empty_and_o_tags(self):
        """Test that 'O', empty strings, and None return an empty string."""
        assert get_ne_explanation("O") == ""
        assert get_ne_explanation("_") == ""
        assert get_ne_explanation("") == ""
        assert get_ne_explanation(None) == ""

    def test_unknown_tags(self):
        """Test that completely unknown tags fall back gracefully."""
        explanation = get_ne_explanation("B-unknown_xyz")
        assert "Unknown Code" in explanation
        assert "unknown_xyz" in explanation
