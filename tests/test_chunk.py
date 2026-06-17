"""
tests/test_chunk.py
===================
Unit tests for the chunking logic used before UDPipe.
"""

import sys
import os
import pytest
from api_util import chunk


def test_chunk_empty_file(tmp_path, monkeypatch):
    """Test that an empty file exits cleanly without creating chunks."""
    infile = tmp_path / "empty.txt"
    infile.write_text("")
    outdir = tmp_path / "out"

    monkeypatch.setattr(sys, 'argv', ["chunk.py", str(infile), str(outdir), "10"])

    with pytest.raises(SystemExit) as e:
        chunk.main()

    assert e.value.code == 0
    assert not os.path.exists(outdir / "chunk_0.txt")


def test_chunk_under_limit(tmp_path, monkeypatch):
    """Test that a file smaller than the word limit is kept as a single chunk."""
    infile = tmp_path / "input.txt"
    text_content = "This is a short line.\nAnother line here."
    infile.write_text(text_content, encoding='utf-8')
    outdir = tmp_path / "out"

    monkeypatch.setattr(sys, 'argv', ["chunk.py", str(infile), str(outdir), "20"])
    chunk.main()

    chunk_file = outdir / "chunk_0.txt"
    assert chunk_file.exists()
    assert chunk_file.read_text(encoding='utf-8') == text_content
    assert not (outdir / "chunk_1.txt").exists()


def test_chunk_over_limit_with_punctuation(tmp_path, monkeypatch):
    """Test the intelligent sentence boundary lookback."""
    infile = tmp_path / "input.txt"
    # Total 10 words. Limit is 6.
    # Line 1 has 5 words and ends with '.'. Line 2 has 5 words.
    infile.write_text("This is the first sentence.\nAnd this is the second.", encoding='utf-8')
    outdir = tmp_path / "out"

    monkeypatch.setattr(sys, 'argv', ["chunk.py", str(infile), str(outdir), "6"])
    chunk.main()

    assert (outdir / "chunk_0.txt").exists()
    assert (outdir / "chunk_1.txt").exists()

    # It should split intelligently at the sentence boundary rather than blindly at the limit.
    assert (outdir / "chunk_0.txt").read_text(encoding='utf-8') == "This is the first sentence."
    assert (outdir / "chunk_1.txt").read_text(encoding='utf-8') == "And this is the second."