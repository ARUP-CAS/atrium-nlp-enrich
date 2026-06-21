import argparse
import shutil
import subprocess
import sys
from pathlib import Path

INSTALL_HINT = "Flexiconv is not installed. Please run: pip install -r requirements_flexiconv.txt"
FLEXICONV_EXTENSIONS = frozenset(
    {
        "pdf",
        "docx",
        "odt",
        "rtf",
        "html",
        "htm",
        "md",
        "txt",
        "epub",
        "tei",
        "conllu",
        "vert",
        "folia",
        "srt",
    }
)


class FlexiconvNotInstalled(RuntimeError):
    pass


def normalize_ext_list(s: str) -> frozenset:
    if not s:
        return frozenset()
    return frozenset(ext.strip().lower() for ext in s.replace(",", " ").split() if ext.strip())


def is_flexiconv_format(path: str | Path, allowed: frozenset = None) -> bool:
    if allowed is None:
        allowed = FLEXICONV_EXTENSIONS
    ext = Path(path).suffix.lower().lstrip(".")
    return ext in allowed and ext not in {"csv", "xlsx"}


def flexiconv_available() -> bool:
    """Checks if flexiconv is available via Python lib or CLI without raising."""
    try:
        import flexiconv  # noqa: F401

        return True
    except ImportError:
        return shutil.which("flexiconv") is not None


def convert_to_teitok(in_path: str | Path, out_dir: str | Path) -> str:
    """Converts input file to TEITOK XML. Raises FlexiconvNotInstalled if missing."""
    if not flexiconv_available():
        raise FlexiconvNotInstalled(INSTALL_HINT) from None

    in_path = Path(in_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{in_path.stem}.teitok.xml"

    try:
        import flexiconv  # noqa: F401

        # Utilizing Python library
        flexiconv.convert(str(in_path), str(out_path), target_format="teitok")
    except (ImportError, AttributeError):
        # Fallback to CLI
        cli_path = shutil.which("flexiconv")
        if not cli_path:
            raise FlexiconvNotInstalled(INSTALL_HINT) from None
        subprocess.run(
            [cli_path, "-t", "teitok", str(in_path), str(out_path)], check=True, timeout=300
        )

    return str(out_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("input_file", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    try:
        convert_to_teitok(args.input_file, args.out_dir)
    except FlexiconvNotInstalled as e:
        print(e, file=sys.stderr)
        sys.exit(1)
