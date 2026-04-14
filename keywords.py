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
             "higher = more relevant" convention across all backends.
             Requires:  pip install yake

    keybert  KeyBERT — embedding-based, GPU-accelerated when available.
             Uses a sentence-transformer model to rank candidate n-grams by
             cosine similarity to the document embedding.
             Score is cosine similarity in [0, 1].
             Requires:  pip install keybert sentence-transformers
             Optional:  pip install torch   (enables CUDA GPU acceleration)

Configuration priority (highest → lowest):
    1. Command-line flags  (e.g. --method yake)
    2. kw_config.txt       ([DEFAULTS] section, looked up next to this script)
    3. Hardcoded fallbacks (defined immediately below the imports)

All three backends produce identical output schemas:
    Master CSV  : document_id, kw-1, score-1, kw-2, score-2, …
    Per-doc CSV : keyword, score   (sorted descending by score)
"""

from __future__ import annotations

import argparse
import configparser
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
# A keyword list is a sequence of (phrase, score) pairs sorted best-first.
Keywords = List[Tuple[str, float]]


# ═══════════════════════════════════════════════════════════════════════════════
# Configuration loading
# ═══════════════════════════════════════════════════════════════════════════════
#
# Defaults are first set to hardcoded values, then optionally overridden by
# kw_config.txt when that file exists next to this script.  The argparse
# layer (in main()) then allows any individual setting to be further
# overridden at the command line.  This three-tier hierarchy means:
#   • Running the script with no flags uses kw_config.txt values.
#   • Explicit CLI flags always win, regardless of the config file.
#   • Removing kw_config.txt falls back to the hardcoded values below.

# ── hardcoded fallbacks ───────────────────────────────────────────────────────
DEFAULT_INPUT_DIR       = "data_samples/UDP"
DEFAULT_OUTPUT_FILE     = "keywords_summary.csv"
DEFAULT_PER_DOC_OUT_DIR = "data_samples/KW_PER_DOC"
DEFAULT_METHOD          = "yake"
DEFAULT_NUM_KEYWORDS    = 20
DEFAULT_LANG            = "cs"
DEFAULT_MAX_WORDS       = 3
DEFAULT_KEYBERT_MODEL   = "paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_NO_MMR          = False
DEFAULT_DIVERSITY       = 0.5
DEFAULT_WORKERS         = multiprocessing.cpu_count()   # one worker per logical CPU

# ── config file override ──────────────────────────────────────────────────────
_config      = configparser.ConfigParser()
_config_file = Path(__file__).parent / "kw_config.txt"   # always relative to this script

if _config_file.exists():
    _config.read(_config_file)
    if "DEFAULTS" in _config:
        _sec = _config["DEFAULTS"]
        # String settings — fall back to the hardcoded default if key is absent.
        DEFAULT_INPUT_DIR       = _sec.get("INPUT_DIR",       DEFAULT_INPUT_DIR)
        DEFAULT_OUTPUT_FILE     = _sec.get("OUTPUT_FILE",     DEFAULT_OUTPUT_FILE)
        DEFAULT_PER_DOC_OUT_DIR = _sec.get("PER_DOC_OUT_DIR", DEFAULT_PER_DOC_OUT_DIR)
        DEFAULT_METHOD          = _sec.get("METHOD",          DEFAULT_METHOD)
        DEFAULT_LANG            = _sec.get("LANG",            DEFAULT_LANG)
        DEFAULT_KEYBERT_MODEL   = _sec.get("KEYBERT_MODEL",   DEFAULT_KEYBERT_MODEL)
        # Typed settings — configparser handles the cast; fallback on bad values.
        DEFAULT_NUM_KEYWORDS    = _sec.getint("NUM_KEYWORDS", DEFAULT_NUM_KEYWORDS)
        DEFAULT_MAX_WORDS       = _sec.getint("MAX_WORDS",    DEFAULT_MAX_WORDS)
        DEFAULT_NO_MMR          = _sec.getboolean("NO_MMR",   DEFAULT_NO_MMR)
        DEFAULT_DIVERSITY       = _sec.getfloat("DIVERSITY",  DEFAULT_DIVERSITY)
        # WORKERS = 0 is a sentinel meaning "use cpu_count()" — only
        # override DEFAULT_WORKERS when a positive value is set in the file.
        _w = _sec.getint("WORKERS", 0)
        if _w > 0:
            DEFAULT_WORKERS = _w


# ═══════════════════════════════════════════════════════════════════════════════
# CoNLL-U reading helpers (shared by all backends)
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_surface_text(file_path: str) -> str:
    """Reconstruct a plain-text string from a CoNLL-U file.

    Iterates over every token line and concatenates the surface form (column 1).
    Inter-token spacing follows the ``SpaceAfter=No`` convention in the MISC
    field (column 9): when the annotation is present the next token is joined
    directly to the previous one without a space, reproducing the original
    OCR surface exactly.

    Multi-word tokens (IDs like ``1-2``) and empty nodes (IDs like ``1.1``)
    are skipped because they are either covered by their constituent tokens or
    represent syntactic nodes not present in the original text.

    Comment lines (starting with ``#``) and blank sentence-separator lines
    are ignored throughout.

    Parameters
    ----------
    file_path:
        Absolute or relative path to the CoNLL-U file.

    Returns
    -------
    str
        The reconstructed surface-form text, stripped of leading/trailing
        whitespace.  Returns an empty string if the file cannot be read or
        contains no usable tokens.

    Used by: _extract_yake, _extract_keybert
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
                if "-" in tok_id or "." in tok_id:   # MWT / empty node — skip
                    continue
                form  = cols[1]
                misc  = cols[9]
                # Append a trailing space unless the annotation says otherwise.
                space = "" if "SpaceAfter=No" in misc else " "
                parts.append(form + space)
    except Exception as exc:
        print(f"[Warning] Could not read surface text from {file_path}: {exc}",
              file=sys.stderr)
    return "".join(parts).strip()


def _extract_lemmas(file_path: str) -> list[str]:
    """Extract content-word lemmas from a CoNLL-U file for frequency counting.

    Collects the lowercased lemma (column 2) of every token whose Universal
    POS tag (column 3) is NOUN, PROPN, or ADJ.  Tokens are further filtered
    to exclude:
        • the placeholder value ``_`` (absent lemma),
        • single-character strings (typically punctuation or abbreviations
          that slipped through the POS filter),
        • strings containing non-alphabetic characters (digits, hyphens, etc.)
          which are unlikely to be informative standalone keywords.

    Multi-word tokens and empty nodes are skipped by the same ID-pattern
    check used in ``_extract_surface_text``.

    Parameters
    ----------
    file_path:
        Absolute or relative path to the CoNLL-U file.

    Returns
    -------
    list[str]
        Flat list of valid lemma strings (lower-cased), in document order,
        with repetitions preserved so that ``Counter`` can produce accurate
        frequency counts.  Returns an empty list on read error.

    Used by: _extract_legacy
    """
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

    Counts how often each content-word lemma appears in the document and
    returns the top-N most frequent ones.  This is the approach used in the
    original ATRIUM pipeline and is retained for reproducibility.

    No external dependencies are required beyond the Python standard library.

    Score semantics:
        Raw occurrence count.  Higher = more frequent in this document.
        Scores are not normalised across documents; use only for within-document
        ranking or when comparing documents of similar length.

    Extra keyword arguments (``**_``) are silently ignored so that the
    backend can be called through the unified ``extract_keywords`` dispatcher
    without special-casing.

    Parameters
    ----------
    file_path:
        Path to the CoNLL-U file.
    num_keywords:
        Maximum number of keywords to return.

    Returns
    -------
    Keywords
        List of (lemma, count) pairs, sorted by count descending.
    """
    lemmas = _extract_lemmas(file_path)
    counts = Counter(lemmas)
    return [(lemma, float(cnt))
            for lemma, cnt in counts.most_common(num_keywords)]


# ═══════════════════════════════════════════════════════════════════════════════
# Backend: YAKE (CPU, statistical)
# ═══════════════════════════════════════════════════════════════════════════════

def _load_yake():
    """Import and return the ``yake`` module, exiting with a clear message if absent.

    Deferred import keeps the module loadable even when ``yake`` is not
    installed, which lets users run the ``legacy`` backend without any
    additional packages.

    Returns
    -------
    module
        The imported ``yake`` module.

    Raises
    ------
    SystemExit
        If ``yake`` is not installed.
    """
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
    """YAKE unsupervised statistical keyword extraction (CPU-only).

    YAKE scores n-gram candidates using five statistical features — casing,
    word position, word frequency, word relatedness to context, and word
    different sentences — without any pre-trained model.  It is therefore
    fast, language-agnostic (given a matching stopword list), and usable
    offline.

    The raw YAKE score is *lower = more relevant* (analogous to a cost or
    distance).  To align with the "higher = better" convention used by the
    other backends this function:
        1. Inverts each score:  inv = 1 / (raw + ε)
        2. Normalises per-document: final = inv / max(inv)
    The resulting scores are in (0, 1] with 1.0 assigned to the best phrase.

    Score semantics (after inversion + normalisation):
        ≈ 1.0   Best candidate for this document
        0.6–0.9 Topic-representative vocabulary
        0.2–0.6 General contextual terms
        < 0.2   Low-informativeness / noisy candidates

    Extra keyword arguments (``**_``) are silently ignored.

    Parameters
    ----------
    file_path:
        Path to the CoNLL-U file.
    num_keywords:
        Maximum number of phrases to return.
    lang:
        BCP-47 language code used to select YAKE's built-in stopword list.
        Supported values include ``cs``, ``en``, ``de``, ``fr``, ``pt``.
        Passing an unsupported code silently disables stopword filtering.
    max_words:
        Upper bound on n-gram length (e.g. 3 allows up to trigrams).

    Returns
    -------
    Keywords
        List of (phrase, normalised_score) pairs, best first.
        Returns an empty list if the file is unreadable or YAKE raises.
    """
    yake = _load_yake()
    text = _extract_surface_text(file_path)
    if not text:
        return []

    extractor = yake.KeywordExtractor(
        lan=lang,           # stopword list language
        n=max_words,        # max n-gram length
        dedupLim=0.9,       # deduplication similarity threshold (Levenshtein)
        dedupFunc="seqm",   # sequence-matcher deduplication function
        windowsSize=1,      # co-occurrence window size (in sentences)
        top=num_keywords,   # candidates to return before inversion
        features=None,      # use all five default statistical features
    )
    try:
        raw_kws = extractor.extract_keywords(text)
    except Exception as exc:
        print(f"[Warning] YAKE failed on {file_path}: {exc}", file=sys.stderr)
        return []

    # Invert: lower YAKE score → higher relevance.
    # ε = 1e-10 prevents division-by-zero for near-perfect candidates.
    inverted = [(kw, 1.0 / (score + 1e-10)) for kw, score in raw_kws]
    if inverted:
        max_inv  = max(s for _, s in inverted)
        inverted = [(kw, round(s / max_inv, 6)) for kw, s in inverted]

    return inverted


# ═══════════════════════════════════════════════════════════════════════════════
# Backend: KeyBERT (GPU-accelerated, embedding-based)
# ═══════════════════════════════════════════════════════════════════════════════

# Module-level singleton — the model is expensive to initialise (download +
# load weights).  Storing it here means each worker *process* initialises it
# exactly once, regardless of how many documents that process handles.
_keybert_model_instance: object           = None
_keybert_model_name_loaded: Optional[str] = None


def _get_keybert_model(model_name: str):
    """Return a cached KeyBERT model, loading it on first call per process.

    Uses process-level module globals so that the model is loaded at most once
    per worker process across all document tasks assigned to that worker.
    If the requested model name changes mid-run (unusual but possible if
    called programmatically), the old instance is discarded and a new one is
    loaded.

    Device selection:
        • CUDA  — when ``torch`` is installed and a CUDA-capable GPU is found.
        • CPU   — otherwise (includes MPS on Apple Silicon via sentence-
                  transformers' own fallback; no special handling needed here).

    Parameters
    ----------
    model_name:
        A Sentence-Transformers model identifier, e.g.
        ``"paraphrase-multilingual-MiniLM-L12-v2"``.
        The model is downloaded from HuggingFace Hub on first use and then
        cached locally by ``sentence-transformers``.

    Returns
    -------
    KeyBERT
        Initialised KeyBERT instance wrapping the loaded SentenceTransformer.

    Raises
    ------
    SystemExit
        If ``keybert`` / ``sentence-transformers`` are not installed, or if
        the model cannot be loaded (e.g. network error on first download).
    """
    global _keybert_model_instance, _keybert_model_name_loaded

    # Fast path: return the cached instance if the model name matches.
    if _keybert_model_instance is not None and _keybert_model_name_loaded == model_name:
        return _keybert_model_instance

    # Dependency check — deferred import keeps the script usable without KeyBERT.
    try:
        from keybert import KeyBERT  # type: ignore
    except ImportError:
        print(
            "[Error] KeyBERT is not installed. "
            "Run: pip install keybert sentence-transformers",
            file=sys.stderr,
        )
        sys.exit(1)

    # Determine compute device.
    try:
        import torch  # type: ignore
        device = "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        # torch is optional; sentence-transformers handles CPU on its own.
        device = "cpu"

    tag = "CUDA" if device == "cuda" else "CPU"
    print(f"[KeyBERT] Loading model '{model_name}' on {tag} …", file=sys.stderr)

    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
        st_model = SentenceTransformer(model_name, device=device)
        _keybert_model_instance    = KeyBERT(model=st_model)
        _keybert_model_name_loaded = model_name
    except Exception as exc:
        print(
            f"[Error] Failed to load KeyBERT model '{model_name}': {exc}",
            file=sys.stderr,
        )
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

    Encodes the full document and all candidate n-gram phrases using a
    Sentence-Transformer model, then ranks phrases by cosine similarity to
    the document centroid embedding.

    When MMR (Maximal Marginal Relevance) is enabled (the default), KeyBERT
    iteratively selects phrases that are both relevant to the document *and*
    dissimilar to already-selected phrases, reducing redundancy.  This is
    especially useful for long OCR documents that heavily repeat domain
    vocabulary.

    The default model ``paraphrase-multilingual-MiniLM-L12-v2`` covers 50+
    languages including Czech, Slovak, German, and English.  For English-only
    collections, ``all-MiniLM-L6-v2`` is faster and may yield slightly
    higher English quality.

    Score semantics:
        Cosine similarity to the document centroid embedding, in [0, 1].
        Higher = more representative of the document's overall content.
        Scores are comparable across documents of different lengths.

    Extra keyword arguments (``**_``) are silently ignored.

    Parameters
    ----------
    file_path:
        Path to the CoNLL-U file.
    num_keywords:
        Maximum number of phrases to return.
    max_words:
        Upper bound on n-gram length passed to ``keyphrase_ngram_range``.
    keybert_model:
        Sentence-Transformer model identifier.  Loaded and cached via
        ``_get_keybert_model``.
    use_mmr:
        If ``True`` (default), apply Maximal Marginal Relevance to diversify
        results.  Set to ``False`` to return the top-N purely by similarity.
    diversity:
        MMR diversity parameter in [0, 1].  0.0 = maximise relevance only
        (equivalent to plain cosine ranking); 1.0 = maximise diversity only.
        The default of 0.5 balances both objectives.

    Returns
    -------
    Keywords
        List of (phrase, cosine_similarity) pairs, best first.
        Returns an empty list if the file is unreadable or KeyBERT raises.
    """
    text = _extract_surface_text(file_path)
    if not text:
        return []

    kw_model = _get_keybert_model(keybert_model)
    try:
        results = kw_model.extract_keywords(
            text,
            keyphrase_ngram_range=(1, max_words),   # unigrams up to max_words-grams
            stop_words=None,    # multilingual models provide their own context
                                # weighting; explicit stopwords are not needed
            use_mmr=use_mmr,
            diversity=diversity,
            top_n=num_keywords,
        )
    except Exception as exc:
        print(f"[Warning] KeyBERT failed on {file_path}: {exc}", file=sys.stderr)
        return []

    return [(kw, round(float(score), 6)) for kw, score in results]


# ═══════════════════════════════════════════════════════════════════════════════
# Backend registry and public dispatcher
# ═══════════════════════════════════════════════════════════════════════════════

# Maps the --method CLI flag value to the corresponding backend function.
# Adding a new backend only requires registering it here.
_BACKENDS: dict = {
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
    """Dispatch a keyword-extraction request to the specified backend.

    This is the single public entry point for extraction; all three backends
    share the same calling convention so callers do not need to import
    individual backend functions.

    Parameters
    ----------
    file_path:
        Path to the CoNLL-U file to process.
    method:
        Backend identifier: ``"legacy"``, ``"yake"``, or ``"keybert"``.
    num_keywords:
        Maximum number of keywords/phrases to return.
    **kwargs:
        Additional parameters forwarded verbatim to the backend function.
        Each backend documents which extra keys it consumes; unknown keys
        are silently ignored via ``**_`` in the backend signatures.

    Returns
    -------
    Keywords
        Sorted (phrase, score) list from the chosen backend, or an empty list
        on extraction failure.

    Raises
    ------
    ValueError
        If ``method`` is not one of the registered backend names.
    """
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
    """Extract keywords for one document and write its per-document CSV.

    This function is the unit of work submitted to each parallel worker
    process.  It intentionally receives all parameters as a plain tuple (not
    keyword arguments or a dataclass) because ``ProcessPoolExecutor`` pickles
    arguments when using the ``spawn`` start method; a flat tuple is fast to
    pickle and avoids issues with unpicklable closures or lambda functions.

    Workflow:
        1. Unpack the task tuple into named variables.
        2. Derive the document ID from the filename stem.
        3. Call ``extract_keywords`` with the requested backend.
        4. Write a two-column per-document CSV (keyword, score) if an output
           directory was provided and at least one keyword was found.
        5. Return (doc_id, keywords) so the main process can append the row
           to the master summary CSV.

    Parameters
    ----------
    task : tuple
        A 9-element tuple produced by ``main()``:
        ``(file_path, method, num_keywords, indiv_out_dir,
           lang, max_words, keybert_model, use_mmr, diversity)``

    Returns
    -------
    tuple[str, Keywords]
        ``(doc_id, keywords)`` — the document stem and the ranked keyword list.
    """
    (file_path, method, num_keywords, indiv_out_dir,
     lang, max_words, keybert_model, use_mmr, diversity) = task

    doc_id   = Path(file_path).stem
    keywords = extract_keywords(
        file_path,
        method        = method,
        num_keywords  = num_keywords,
        lang          = lang,
        max_words     = max_words,
        keybert_model = keybert_model,
        use_mmr       = use_mmr,
        diversity     = diversity,
    )

    # Write the per-document CSV only when there are results to record.
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
    """Append one document's keywords as a single wide row to the master CSV.

    The master CSV uses the schema:
        document_id, kw-1, score-1, kw-2, score-2, … kw-N, score-N

    If fewer than ``num_keywords`` phrases were found (e.g. a very short
    document), the trailing columns are filled with empty strings so that
    every row has the same width and downstream tools can parse the CSV
    reliably without raising index errors.

    The file is opened in append mode so this function is safe to call
    from the main process loop after each future completes.

    Parameters
    ----------
    output_file:
        Path to the master summary CSV (must already exist with a header row).
    doc_id:
        Document identifier written to the first column.
    keywords:
        Ranked (phrase, score) list from the extraction backend.
    num_keywords:
        Total number of kw-N / score-N column pairs in the file; used to
        pad short results to the correct row width.
    """
    row: list = [doc_id]
    for i in range(num_keywords):
        if i < len(keywords):
            kw, score = keywords[i]
            row.extend([kw, score])
        else:
            row.extend(["", ""])    # pad missing slots with empty strings
    with open(output_file, "a", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerow(row)


def _sort_csv_file(file_path: str) -> None:
    """Sort the master CSV alphabetically by document_id (column 0).

    Sorting makes the file easier to diff between pipeline runs and ensures
    a reproducible row order regardless of the order futures complete.

    Three strategies are tried in order of efficiency:

    1. **pandas** — in-memory sort using a C-backed DataFrame; fast for files
       that fit comfortably in RAM.
    2. **POSIX external sort** — invokes the system ``sort`` utility which
       performs out-of-core merge sort; handles files larger than RAM on Linux/
       macOS without any extra Python dependencies.
    3. **In-memory Python sort** — stdlib-only fallback using ``csv.reader``
       and the built-in ``sorted``; works on all platforms but may be slow or
       raise MemoryError on very large files.

    The sorted result is written back to the same path in all three cases.

    Parameters
    ----------
    file_path:
        Path to the master CSV file to sort in place.
    """
    # Strategy 1: pandas (fastest for moderate file sizes)
    try:
        import pandas as pd  # type: ignore
        df = pd.read_csv(file_path)
        df.sort_values(by=df.columns[0], inplace=True)
        df.to_csv(file_path, index=False)
        return
    except ImportError:
        pass

    # Strategy 2: POSIX external sort (out-of-core, no RAM limit)
    if os.name == "posix" and shutil.which("sort"):
        tmp = file_path + ".tmp"
        try:
            # Preserve the header separately, then sort the data rows only.
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
            print(
                f"[Warning] POSIX sort failed: {exc}. Using in-memory sort.",
                file=sys.stderr,
            )
            if os.path.exists(tmp):
                os.remove(tmp)

    # Strategy 3: in-memory stdlib fallback
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
    """Entry point: parse arguments, run extraction, write outputs.

    Execution flow:
        1. Build an ArgumentParser whose defaults come from the module-level
           DEFAULT_* variables (already overridden by kw_config.txt when
           present), so explicit CLI flags always win over the config file,
           and the config file always wins over hardcoded values.
        2. For KeyBERT with a GPU, force --workers 1 to avoid competing CUDA
           context initialisation across subprocess workers.
        3. Validate the input directory and create output directories.
        4. Write the master CSV header row (kw-1, score-1, … kw-N, score-N).
        5. Build the task list — one flat tuple per .conllu file found in the
           input directory, sorted for deterministic processing order.
        6. Initialise the paradata logger for provenance tracking.
        7. Dispatch tasks to a ProcessPoolExecutor using the ``spawn`` start
           method (required for CUDA workers; safe on all platforms including
           Windows where ``fork`` is not available).
        8. Collect results as futures complete, append each row to the master
           CSV, and log progress every 100 documents.
        9. Always call ``_logger.finalize()`` (in the ``finally`` block) so
           paradata is written even on keyboard interrupt or partial failure.
       10. Sort the master CSV and print a completion summary.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Extract keywords from CoNLL-U files.\n"
            "Backends: 'yake' (statistical, CPU, default), "
            "'keybert' (embedding, GPU/CPU), "
            "'legacy' (lemma frequency, original KER approach).\n\n"
            "Defaults are read from kw_config.txt when present next to this "
            "script; any explicit CLI flag overrides the config file value."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # ── I/O ───────────────────────────────────────────────────────────────────
    parser.add_argument(
        "-i", "--input_dir",
        default=DEFAULT_INPUT_DIR,
        help=(
            "Directory containing .conllu files to process. "
            f"(default from config: {DEFAULT_INPUT_DIR})"
        ),
    )
    parser.add_argument(
        "-o", "--output_file",
        default=DEFAULT_OUTPUT_FILE,
        help=(
            "Master CSV output file path. "
            f"(default from config: {DEFAULT_OUTPUT_FILE})"
        ),
    )
    parser.add_argument(
        "-d", "--per_doc_out_dir",
        default=DEFAULT_PER_DOC_OUT_DIR,
        help=(
            "Output directory for per-document keyword CSVs. "
            f"(default from config: {DEFAULT_PER_DOC_OUT_DIR})"
        ),
    )

    # ── extraction parameters ─────────────────────────────────────────────────
    parser.add_argument(
        "-m", "--method",
        default=DEFAULT_METHOD,
        choices=list(_BACKENDS),
        help=(
            "Extraction backend: 'yake' = YAKE statistical CPU-only; "
            "'keybert' = KeyBERT embedding-based GPU/CPU; "
            "'legacy' = original KER lemma-frequency, no extra deps. "
            f"(default from config: {DEFAULT_METHOD})"
        ),
    )
    parser.add_argument(
        "-n", "--num_keywords",
        type=int,
        default=DEFAULT_NUM_KEYWORDS,
        help=(
            "Number of top keywords to extract per document. "
            f"(default from config: {DEFAULT_NUM_KEYWORDS})"
        ),
    )
    parser.add_argument(
        "-l", "--lang",
        default=DEFAULT_LANG,
        help=(
            "Language code for YAKE stopword selection "
            "(e.g. 'cs', 'en', 'de'). Ignored by 'legacy' and 'keybert'. "
            f"(default from config: {DEFAULT_LANG})"
        ),
    )
    parser.add_argument(
        "-w", "--max_words",
        type=int,
        default=DEFAULT_MAX_WORDS,
        help=(
            "Maximum words per keyword phrase (n-gram upper bound). "
            f"(default from config: {DEFAULT_MAX_WORDS})"
        ),
    )

    # ── KeyBERT-specific options ───────────────────────────────────────────────
    keybert_group = parser.add_argument_group(
        "KeyBERT options",
        "Only used when --method keybert.",
    )
    keybert_group.add_argument(
        "--keybert-model",
        dest="keybert_model",
        default=DEFAULT_KEYBERT_MODEL,
        help=(
            "Sentence-Transformer model name. "
            "Default is multilingual (Czech, Slovak, German, English). "
            "For English-only collections 'all-MiniLM-L6-v2' is faster. "
            f"(default from config: {DEFAULT_KEYBERT_MODEL})"
        ),
    )
    keybert_group.add_argument(
        "--no-mmr",
        action="store_true",
        default=DEFAULT_NO_MMR,
        help=(
            "Disable Maximal Marginal Relevance diversification. "
            "When set, keywords are ranked purely by cosine similarity "
            "without penalising redundant phrases. "
            f"(default from config: {DEFAULT_NO_MMR})"
        ),
    )
    keybert_group.add_argument(
        "--diversity",
        type=float,
        default=DEFAULT_DIVERSITY,
        help=(
            "MMR diversity parameter: 0.0 = max relevance, 1.0 = max diversity. "
            f"(default from config: {DEFAULT_DIVERSITY})"
        ),
    )

    # ── runtime ───────────────────────────────────────────────────────────────
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=(
            "Number of parallel worker processes. "
            "For --method keybert with a CUDA GPU this is automatically "
            "forced to 1 to prevent competing context initialisation. "
            f"(default from config: {DEFAULT_WORKERS})"
        ),
    )

    args = parser.parse_args()

    # KeyBERT + GPU guard: multiple workers each loading the model into the
    # same CUDA device causes context conflicts and silent hangs.
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
            pass   # torch not installed → CPU path, any worker count is safe

    # ── directory setup ───────────────────────────────────────────────────────
    input_path    = Path(args.input_dir)
    indiv_out_dir = Path(args.per_doc_out_dir)
    indiv_out_dir.mkdir(parents=True, exist_ok=True)

    if not input_path.is_dir():
        print(f"[Error] Input directory not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    # ── master CSV header ─────────────────────────────────────────────────────
    # Written once here; all worker results are appended by _write_csv_row.
    header = ["document_id"]
    for i in range(1, args.num_keywords + 1):
        header.extend([f"kw-{i}", f"score-{i}"])
    with open(args.output_file, "w", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerow(header)

    # ── task list ─────────────────────────────────────────────────────────────
    # Each task is a flat tuple consumed by _process_document_task.
    # Sorted glob ensures a deterministic processing order.
    tasks = [
        (
            str(p),
            args.method,
            args.num_keywords,
            str(indiv_out_dir),
            args.lang,
            args.max_words,
            args.keybert_model,
            not args.no_mmr,   # use_mmr = True unless --no-mmr was passed
            args.diversity,
        )
        for p in sorted(input_path.glob("*.conllu"))
    ]

    # ── paradata logger ───────────────────────────────────────────────────────
    # Records run metadata, success/skip counts, and config for auditability.
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
            # KeyBERT-specific parameters are only included in paradata when
            # that backend is active; they are irrelevant for legacy/yake runs.
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

    # ── parallel execution ────────────────────────────────────────────────────
    processed_count = 0
    try:
        # Use the "spawn" start method so each worker process starts with a
        # clean interpreter state.  This is the only method supported on
        # Windows and is required for CUDA workers on all platforms (the
        # "fork" method duplicates GPU state inconsistently across processes).
        mp_context = multiprocessing.get_context("spawn")

        with ProcessPoolExecutor(
            max_workers=args.workers, mp_context=mp_context
        ) as executor:
            # Submit all tasks upfront; futures are keyed by the source path
            # so error messages can identify the failing document by name.
            futures = {
                executor.submit(_process_document_task, t): t[0]
                for t in tasks
            }
            for future in as_completed(futures):
                doc_path = futures[future]
                try:
                    doc_id, keywords = future.result()
                    _write_csv_row(
                        args.output_file, doc_id, keywords, args.num_keywords
                    )
                    processed_count += 1
                    _logger.log_success("csv_per_doc",     count=1)
                    _logger.log_success("csv_summary_row", count=1)
                    if processed_count % 100 == 0:
                        print(f"  Processed {processed_count}/{len(tasks)} …")
                except Exception as exc:
                    print(f"[Error] '{doc_path}': {exc}", file=sys.stderr)
                    _logger.log_skip(str(doc_path), str(exc))
    finally:
        # finalize() is always called so paradata is written even on
        # keyboard interrupt or partial failure.
        _logger.finalize(input_total=len(tasks))

    print("--- Sorting master results … ---")
    _sort_csv_file(args.output_file)
    print(
        f"--- Done. {processed_count}/{len(tasks)} documents processed. "
        f"Output: {args.output_file} ---"
    )


if __name__ == "__main__":
    main()