"""keywords.py — Keyword extraction from CoNLL-U files.

Three extraction backends are supported, selected via ``--method``:

    legacy   Original KER lemma-frequency approach (no extra dependencies).
             Counts NOUN/PROPN/ADJ lemmas extracted from UDPipe CoNLL-U output;
             score = raw occurrence count.  This is the approach used in the
             original ATRIUM pipeline and requires no external packages beyond
             the standard library.

    yake     YAKE (Yet Another Keyword Extractor) — unsupervised, statistical,
             CPU-only.  Works on reconstructed surface-form text.
             YAKE raw scores are lower-is-better; they are inverted and
             normalised per-document so the output uses a consistent
             "higher = more relevant" convention.
             Requires:  pip install yake

    keybert  KeyBERT — embedding-based, GPU-accelerated when available.
             Uses a sentence-transformer model to rank candidate n-grams by
             cosine similarity to the document embedding.
             Score is cosine similarity in [0, 1].
             Requires:  pip install keybert sentence-transformers
             Optional:  pip install torch   (enables CUDA GPU acceleration)

All three backends produce identical output schemas:
    Master CSV  : document_id, kw-1, score-1, kw-2, score-2, …
    Per-doc CSV : keyword, score   (sorted descending by score)
"""

from __future__ import annotations

import argparse
import csv
import multiprocessing
import os
import shutil
import subprocess
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import List, Optional, Tuple

from atrium_paradata import ParadataLogger

# ── type alias ────────────────────────────────────────────────────────────────
Keywords = List[Tuple[str, float]]

# ── defaults ──────────────────────────────────────────────────────────────────
DEFAULT_INDIVIDUAL_OUTPUT_DIR = "data_samples/KW_PER_DOC"
DEFAULT_INPUT_CONLLU_DIR      = "data_samples/UDP"
DEFAULT_METHOD                = "yake"
DEFAULT_KEYBERT_MODEL         = "paraphrase-multilingual-MiniLM-L12-v2"


# ═══════════════════════════════════════════════════════════════════════════════
# CoNLL-U reading helpers (shared by all backends)
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_surface_text(file_path: str) -> str:
    """Reconstruct plain text from a CoNLL-U file, respecting SpaceAfter=No.

    Used by the YAKE and KeyBERT backends, which operate on surface-form text
    rather than lemmas.  The reconstructed string closely mirrors the original
    OCR output, which is important for n-gram quality.
    """
    parts: list[str] = []
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                cols = line.split("\t")
                if len(cols) < 10:
                    continue
                tok_id = cols[0]
                if "-" in tok_id or "." in tok_id:   # skip MWT / empty nodes
                    continue
                form  = cols[1]
                misc  = cols[9]
                space = "" if "SpaceAfter=No" in misc else " "
                parts.append(form + space)
    except Exception as exc:
        print(f"[Warning] Could not read surface text from {file_path}: {exc}",
              file=sys.stderr)
    return "".join(parts).strip()


def _extract_lemmas(file_path: str) -> list[str]:
    """Extract NOUN/PROPN/ADJ lemmas for the legacy frequency backend."""
    valid_pos = {"NOUN", "PROPN", "ADJ"}
    lemmas: list[str] = []
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                cols = line.split("\t")
                if len(cols) < 10:
                    continue
                tok_id = cols[0]
                if "-" in tok_id or "." in tok_id:
                    continue
                lemma = cols[2]
                upos  = cols[3]
                if upos in valid_pos and lemma != "_":
                    if len(lemma) > 1 and lemma.isalpha():
                        lemmas.append(lemma.lower())
    except Exception as exc:
        print(f"[Warning] Could not read CoNLL-U file {file_path}: {exc}",
              file=sys.stderr)
    return lemmas


# ═══════════════════════════════════════════════════════════════════════════════
# Backend: legacy KER (lemma frequency — original ATRIUM approach)
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_legacy(
    file_path: str,
    num_keywords: int,
    **_,
) -> Keywords:
    """Original KER approach: NOUN/PROPN/ADJ lemma frequency from UDPipe output.

    No external dependencies required.  Score is the raw occurrence count of
    each lemma within the document.  To reproduce the exact behaviour of the
    original pipeline, pass ``--method legacy``.
    """
    lemmas  = _extract_lemmas(file_path)
    counts  = Counter(lemmas)
    return [(lemma, float(cnt))
            for lemma, cnt in counts.most_common(num_keywords)]


# ═══════════════════════════════════════════════════════════════════════════════
# Backend: YAKE (CPU, statistical)
# ═══════════════════════════════════════════════════════════════════════════════

def _load_yake():
    try:
        import yake  # type: ignore
        return yake
    except ImportError:
        print(
            "[Error] YAKE is not installed.  Run: pip install yake",
            file=sys.stderr,
        )
        sys.exit(1)


def _extract_yake(
    file_path: str,
    num_keywords: int,
    lang: str = "cs",
    max_words: int = 3,
    **_,
) -> Keywords:
    """YAKE unsupervised keyword extraction (CPU-only, no model download needed).

    YAKE scores are lower-is-better; they are inverted and normalised to the
    [0, 1] range per document so the output column uses the "higher = more
    relevant" convention shared with the other backends.

    The language code (``--lang``) is passed directly to YAKE's stopword list
    selector.  Supported codes include ``cs``, ``en``, ``de``, ``fr``, ``pt``.
    """
    yake   = _load_yake()
    text   = _extract_surface_text(file_path)
    if not text:
        return []

    extractor = yake.KeywordExtractor(
        lan=lang,
        n=max_words,
        dedupLim=0.9,
        dedupFunc="seqm",
        windowsSize=1,
        top=num_keywords,
        features=None,
    )
    try:
        raw_kws = extractor.extract_keywords(text)
    except Exception as exc:
        print(f"[Warning] YAKE failed on {file_path}: {exc}", file=sys.stderr)
        return []

    # Invert: lower YAKE score → higher relevance.
    inverted = [(kw, 1.0 / (score + 1e-10)) for kw, score in raw_kws]
    if inverted:
        max_inv  = max(s for _, s in inverted)
        inverted = [(kw, round(s / max_inv, 6)) for kw, s in inverted]

    return inverted


# ═══════════════════════════════════════════════════════════════════════════════
# Backend: KeyBERT (GPU-accelerated, embedding-based)
# ═══════════════════════════════════════════════════════════════════════════════

# Module-level singleton — loaded once per worker process, not once per document.
_keybert_model_instance: object          = None
_keybert_model_name_loaded: Optional[str] = None


def _get_keybert_model(model_name: str):
    """Return (and cache) a KeyBERT model instance for the current process."""
    global _keybert_model_instance, _keybert_model_name_loaded

    if _keybert_model_instance is not None and _keybert_model_name_loaded == model_name:
        return _keybert_model_instance

    try:
        from keybert import KeyBERT  # type: ignore
    except ImportError:
        print(
            "[Error] KeyBERT is not installed. "
            "Run: pip install keybert sentence-transformers",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        import torch  # type: ignore
        device = "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        device = "cpu"

    tag = "CUDA" if device == "cuda" else "CPU"
    print(f"[KeyBERT] Loading model '{model_name}' on {tag} …", file=sys.stderr)

    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
        st_model = SentenceTransformer(model_name, device=device)
        _keybert_model_instance    = KeyBERT(model=st_model)
        _keybert_model_name_loaded = model_name
    except Exception as exc:
        print(f"[Error] Failed to load KeyBERT model '{model_name}': {exc}",
              file=sys.stderr)
        sys.exit(1)

    return _keybert_model_instance


def _extract_keybert(
    file_path: str,
    num_keywords: int,
    max_words: int = 3,
    keybert_model: str = DEFAULT_KEYBERT_MODEL,
    use_mmr: bool = True,
    diversity: float = 0.5,
    **_,
) -> Keywords:
    """KeyBERT embedding-based keyword extraction, GPU-accelerated when available.

    Uses Maximal Marginal Relevance (MMR, enabled by default) to balance
    relevance against phrase redundancy.  This is especially beneficial for
    long OCR documents that repeat domain vocabulary heavily.

    Score is cosine similarity between the candidate phrase embedding and the
    document centroid embedding, in [0, 1].

    The default model (``paraphrase-multilingual-MiniLM-L12-v2``) is
    multilingual and works well for Czech, Slovak, German, and English
    collections.  For English-only data, ``all-MiniLM-L6-v2`` is faster.
    """
    text = _extract_surface_text(file_path)
    if not text:
        return []

    kw_model = _get_keybert_model(keybert_model)
    try:
        results = kw_model.extract_keywords(
            text,
            keyphrase_ngram_range=(1, max_words),
            stop_words=None,   # multilingual models handle their own context weighting
            use_mmr=use_mmr,
            diversity=diversity,
            top_n=num_keywords,
        )
    except Exception as exc:
        print(f"[Warning] KeyBERT failed on {file_path}: {exc}", file=sys.stderr)
        return []

    return [(kw, round(float(score), 6)) for kw, score in results]


# ═══════════════════════════════════════════════════════════════════════════════
# Backend registry
# ═══════════════════════════════════════════════════════════════════════════════

_BACKENDS = {
    "legacy":  _extract_legacy,
    "yake":    _extract_yake,
    "keybert": _extract_keybert,
}


def extract_keywords(
    file_path: str,
    method: str,
    num_keywords: int,
    **kwargs,
) -> Keywords:
    """Dispatch to the requested extraction backend."""
    fn = _BACKENDS.get(method)
    if fn is None:
        raise ValueError(
            f"Unknown method '{method}'. "
            f"Choose from: {', '.join(_BACKENDS)}"
        )
    return fn(file_path, num_keywords, **kwargs)


# ═══════════════════════════════════════════════════════════════════════════════
# Worker (runs inside ProcessPoolExecutor subprocesses)
# ═══════════════════════════════════════════════════════════════════════════════

def _process_document_task(task: tuple) -> tuple[str, Keywords]:
    """Extract keywords for one document and write its per-document CSV."""
    (file_path, method, num_keywords, indiv_out_dir,
     lang, max_words, keybert_model, use_mmr, diversity) = task

    doc_id   = Path(file_path).stem
    keywords = extract_keywords(
        file_path,
        method       = method,
        num_keywords = num_keywords,
        lang         = lang,
        max_words    = max_words,
        keybert_model= keybert_model,
        use_mmr      = use_mmr,
        diversity    = diversity,
    )

    if keywords and indiv_out_dir:
        out_csv = Path(indiv_out_dir) / f"{doc_id}_keywords.csv"
        try:
            with open(out_csv, "w", encoding="utf-8", newline="") as fh:
                writer = csv.writer(fh)
                writer.writerow(["keyword", "score"])
                writer.writerows(keywords)
        except Exception as exc:
            print(f"[Warning] Could not write {out_csv}: {exc}", file=sys.stderr)

    return doc_id, keywords


# ═══════════════════════════════════════════════════════════════════════════════
# CSV output helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _write_csv_row(
    output_file: str,
    doc_id: str,
    keywords: Keywords,
    num_keywords: int,
) -> None:
    """Append one document's keywords as a single row to the master CSV."""
    row: list = [doc_id]
    for i in range(num_keywords):
        if i < len(keywords):
            kw, score = keywords[i]
            row.extend([kw, score])
        else:
            row.extend(["", ""])
    with open(output_file, "a", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerow(row)


def _sort_csv_file(file_path: str) -> None:
    """Sort the master CSV alphabetically by document_id (column 0).

    Tries pandas → POSIX external sort → in-memory sort, in that order, to
    avoid OOM errors on very large collections.
    """
    # 1. pandas (fastest, handles large files efficiently)
    try:
        import pandas as pd  # type: ignore
        df = pd.read_csv(file_path)
        df.sort_values(by=df.columns[0], inplace=True)
        df.to_csv(file_path, index=False)
        return
    except ImportError:
        pass

    # 2. POSIX external sort (out-of-core, no RAM limit)
    if os.name == "posix" and shutil.which("sort"):
        tmp = file_path + ".tmp"
        try:
            with open(file_path, "r", encoding="utf-8") as fh:
                header = fh.readline()
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(header)
            subprocess.run(
                f"tail -n +2 '{file_path}' | sort -t ',' -k1 >> '{tmp}'",
                shell=True, check=True,
            )
            shutil.move(tmp, file_path)
            return
        except Exception as exc:
            print(f"[Warning] POSIX sort failed: {exc}. Using in-memory sort.",
                  file=sys.stderr)
            if os.path.exists(tmp):
                os.remove(tmp)

    # 3. In-memory fallback
    try:
        with open(file_path, "r", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            header = next(reader)
            rows   = sorted(reader, key=lambda r: r[0])
        with open(file_path, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerows(rows)
    except Exception as exc:
        print(f"[Error] Could not sort {file_path}: {exc}", file=sys.stderr)


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Extract keywords from CoNLL-U files.\n"
            "Backends: 'yake' (statistical, CPU), "
            "'keybert' (embedding, GPU/CPU), "
            "'legacy' (lemma frequency, original KER approach)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # ── I/O ───────────────────────────────────────────────────────────────────
    parser.add_argument(
        "-i", "--input_dir", required=True,
        help="Directory containing .conllu files (e.g. OUTPUT_DIR/UDP/).",
    )
    parser.add_argument(
        "-o", "--output_file", default="keywords_summary.csv",
        help="Master CSV output file (default: keywords_summary.csv).",
    )
    parser.add_argument(
        "-d", "--per_doc_out_dir", default=DEFAULT_INDIVIDUAL_OUTPUT_DIR,
        help="Output directory for per-document keyword CSVs.",
    )

    # ── extraction ────────────────────────────────────────────────────────────
    parser.add_argument(
        "-m", "--method", default=DEFAULT_METHOD,
        choices=list(_BACKENDS),
        help=(
            "Extraction backend: "
            "'yake' = YAKE statistical, CPU-only (default); "
            "'keybert' = KeyBERT embedding-based, GPU when available; "
            "'legacy' = original KER lemma-frequency approach, no extra deps."
        ),
    )
    parser.add_argument(
        "-n", "--num_keywords", type=int, default=20,
        help="Number of top keywords to extract per document.",
    )
    parser.add_argument(
        "-l", "--lang", default="cs",
        help=(
            "Language code for YAKE stopword selection (e.g. 'cs', 'en', 'de'). "
            "Ignored by the 'legacy' and 'keybert' backends."
        ),
    )
    parser.add_argument(
        "-w", "--max_words", type=int, default=3,
        help="Maximum words per keyword phrase (n-gram upper bound).",
    )

    # ── KeyBERT options ───────────────────────────────────────────────────────
    keybert_group = parser.add_argument_group(
        "KeyBERT options",
        "Only used when --method keybert.",
    )
    keybert_group.add_argument(
        "--keybert-model", default=DEFAULT_KEYBERT_MODEL,
        dest="keybert_model",
        help=(
            f"Sentence-Transformer model name (default: {DEFAULT_KEYBERT_MODEL}). "
            "Works for Czech, Slovak, German, English. "
            "For English-only: 'all-MiniLM-L6-v2'."
        ),
    )
    keybert_group.add_argument(
        "--no-mmr", action="store_true",
        help="Disable Maximal Marginal Relevance diversification.",
    )
    keybert_group.add_argument(
        "--diversity", type=float, default=0.5,
        help="MMR diversity parameter: 0.0 = max relevance, 1.0 = max diversity.",
    )

    # ── runtime ───────────────────────────────────────────────────────────────
    parser.add_argument(
        "--workers", type=int, default=multiprocessing.cpu_count(),
        help=(
            "Parallel worker processes. "
            "For --method keybert with GPU, this is automatically forced to 1."
        ),
    )

    args = parser.parse_args()

    # KeyBERT + GPU: prevent competing CUDA context init across workers.
    if args.method == "keybert":
        try:
            import torch  # type: ignore
            if torch.cuda.is_available() and args.workers > 1:
                print(
                    "[KeyBERT] GPU detected: forcing --workers 1 to avoid "
                    "CUDA context conflicts across subprocesses.",
                    file=sys.stderr,
                )
                args.workers = 1
        except ImportError:
            pass

    # ── setup ─────────────────────────────────────────────────────────────────
    input_path    = Path(args.input_dir)
    indiv_out_dir = Path(args.per_doc_out_dir)
    indiv_out_dir.mkdir(parents=True, exist_ok=True)

    if not input_path.is_dir():
        print(f"[Error] Input directory not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    # Write master CSV header
    header = ["document_id"]
    for i in range(1, args.num_keywords + 1):
        header.extend([f"kw-{i}", f"score-{i}"])
    with open(args.output_file, "w", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerow(header)

    tasks = [
        (
            str(p),
            args.method,
            args.num_keywords,
            str(indiv_out_dir),
            args.lang,
            args.max_words,
            args.keybert_model,
            not args.no_mmr,
            args.diversity,
        )
        for p in sorted(input_path.glob("*.conllu"))
    ]

    # ── paradata ──────────────────────────────────────────────────────────────
    _logger = ParadataLogger(
        program="nlp-enrich",
        config={
            "script":          "keywords",
            "method":          args.method,
            "input_dir":       str(args.input_dir),
            "lang":            args.lang,
            "max_words":       args.max_words,
            "num_keywords":    args.num_keywords,
            "per_doc_out_dir": str(args.per_doc_out_dir),
            "output_file":     str(args.output_file),
            **({"keybert_model": args.keybert_model,
                "mmr":           not args.no_mmr,
                "diversity":     args.diversity}
               if args.method == "keybert" else {}),
        },
        paradata_dir="paradata",
        output_types=["csv_per_doc", "csv_summary_row"],
    )

    print(
        f"--- Keyword Extraction | method={args.method} | "
        f"{len(tasks)} documents | workers={args.workers} ---"
    )

    processed_count = 0
    try:
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            futures = {executor.submit(_process_document_task, t): t[0]
                       for t in tasks}
            for future in as_completed(futures):
                doc_path = futures[future]
                try:
                    doc_id, keywords = future.result()
                    _write_csv_row(args.output_file, doc_id, keywords,
                                   args.num_keywords)
                    processed_count += 1
                    _logger.log_success("csv_per_doc",     count=1)
                    _logger.log_success("csv_summary_row", count=1)
                    if processed_count % 100 == 0:
                        print(f"  Processed {processed_count}/{len(tasks)} …")
                except Exception as exc:
                    print(f"[Error] '{doc_path}': {exc}", file=sys.stderr)
                    _logger.log_skip(str(doc_path), str(exc))
    finally:
        _logger.finalize(input_total=len(tasks))

    print("--- Sorting master results … ---")
    _sort_csv_file(args.output_file)
    print(
        f"--- Done. {processed_count}/{len(tasks)} documents processed. "
        f"Output: {args.output_file} ---"
    )


if __name__ == "__main__":
    main()