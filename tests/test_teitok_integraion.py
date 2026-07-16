import base64
from unittest.mock import patch

# Base64 encoded minimal 1x1 transparent PNG to act as our data sample
MINIMAL_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAACklEQVR4nGMAAQAABQABDQottAAAAABJRU5ErkJggg=="
)


def test_cli_argparse_dpi_support():
    """Ensure that the summarize_nt_udp.py pipeline natively understands dpi and alto-dpi arguments."""
    from api_util.summarize_nt_udp import build_parser

    parser = build_parser()
    help_text = parser.format_help()
    assert "--dpi" in help_text, "Missing --dpi flag in argparse configuration."
    assert "--alto-dpi" in help_text, "Missing --alto-dpi flag in argparse configuration."

    # The parser must accept the flags in-process without erroring out
    args = parser.parse_args(["--dpi", "300", "--alto-dpi", "200"])
    assert args.dpi == 300.0
    assert args.alto_dpi == 200.0


def test_process_single_document_threading(tmp_path):
    """Guarantee thread execution of process_single_document natively pushes DPI args to write_teitok_merged."""
    from api_util.summarize_nt_udp import process_single_document

    with patch("api_util.summarize_nt_udp.write_teitok_merged") as mock_write:
        teitok_out_dir = tmp_path / "TEITOK"

        # Create dummy prerequisites to prevent the pipeline from exiting early
        ne_dir = tmp_path / "dummy_ne_dir"
        ne_dir.mkdir()
        conllu_file = tmp_path / "dummy.conllu"
        conllu_file.write_text("")

        process_single_document(
            conllu_file=str(conllu_file),
            ne_dir=str(ne_dir),
            output_dir=str(tmp_path),
            save_csv=False,
            save_teitok=True,
            teitok_out=str(teitok_out_dir),
            alto_dir="dummy_alto_dir",
            dpi=300.0,
            alto_dpi=200.0,
        )

        assert mock_write.call_count >= 1, "write_teitok_merged was never called"
        _, kwargs = mock_write.call_args
        assert kwargs.get("dpi") == 300.0
        assert kwargs.get("alto_dpi") == 200.0


def test_minimal_png_generation(tmp_path):
    """Provide the minimal PNG binary to prevent CI failures when testing ALTO alignments."""
    pages_dir = tmp_path / "data_samples" / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    png_file = pages_dir / "CTX000000001-1.png"
    with open(png_file, "wb") as f:
        f.write(base64.b64decode(MINIMAL_PNG_B64))

    assert png_file.exists()
    assert png_file.stat().st_size > 0
