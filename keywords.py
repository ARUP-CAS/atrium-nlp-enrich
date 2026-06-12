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
             cosine similarity to the document embedding. Optimized for large
             batches and long texts via chunking.
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
from typing import List, Optional, Tuple, Union

from atrium_paradata import ParadataLogger

# ── type alias ────────────────────────────────────────────────────────────────
# A keyword list is a sequence of (phrase, score) pairs sorted best-first.
Keywords = List[Tuple[str, float]]


# ═══════════════════════════════════════════════════════════════════════════════
# Configuration loading
# ═══════════════════════════════════════════════════════════════════════════════

# ── hardcoded fallbacks ───────────────────────────────────────────────────────
DEFAULT_INPUT_DIR       = "data_samples/UDP"
DEFAULT_OUTPUT_FILE     = "data_samples/keywords_summary_{method}.csv"
DEFAULT_PER_DOC_OUT_DIR = "data_samples/KW_PER_DOC_{METHOD}"
DEFAULT_METHOD          = "yake"
DEFAULT_NUM_KEYWORDS    = 20
DEFAULT_LANG            = "cs"
DEFAULT_MAX_WORDS       = 3
DEFAULT_KEYBERT_MODEL   = "paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_NO_MMR          = False
DEFAULT_DIVERSITY       = 0.5
DEFAULT_WORKERS         = multiprocessing.cpu_count()   # one worker per logical CPU
DEFAULT_BATCH_SIZE      = 16                            # For KeyBERT GPU batching

# ── config file override ──────────────────────────────────────────────────────
_config      = configparser.ConfigParser()
_config_file = Path(__file__).parent / "kw_config.txt"

if _config_file.exists():
    _config.read(_config_file)
    if "DEFAULTS" in _config:
        _sec = _config["DEFAULTS"]
        DEFAULT_INPUT_DIR       = _sec.get("INPUT_DIR",       DEFAULT_INPUT_DIR)
        DEFAULT_OUTPUT_FILE     = _sec.get("OUTPUT_FILE",     DEFAULT_OUTPUT_FILE)
        DEFAULT_PER_DOC_OUT_DIR = _sec.get("PER_DOC_OUT_DIR", DEFAULT_PER_DOC_OUT_DIR)
        DEFAULT_METHOD          = _sec.get("METHOD",          DEFAULT_METHOD)
        DEFAULT_LANG            = _sec.get("LANG",            DEFAULT_LANG)
        DEFAULT_KEYBERT_MODEL   = _sec.get("KEYBERT_MODEL",   DEFAULT_KEYBERT_MODEL)
        DEFAULT_NUM_KEYWORDS    = _sec.getint("NUM_KEYWORDS", DEFAULT_NUM_KEYWORDS)
        DEFAULT_MAX_WORDS       = _sec.getint("MAX_WORDS",    DEFAULT_MAX_WORDS)
        DEFAULT_NO_MMR          = _sec.getboolean("NO_MMR",   DEFAULT_NO_MMR)
        DEFAULT_DIVERSITY       = _sec.getfloat("DIVERSITY",  DEFAULT_DIVERSITY)
        DEFAULT_BATCH_SIZE      = _sec.getint("BATCH_SIZE",   DEFAULT_BATCH_SIZE)
        _w = _sec.getint("WORKERS", 0)
        if _w > 0:
            DEFAULT_WORKERS = _w


# ═══════════════════════════════════════════════════════════════════════════════
# CoNLL-U reading helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_surface_text(file_path: str) -> str:
    """Reconstruct a plain-text string from a CoNLL-U file."""
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
                if "-" in tok_id or "." in tok_id:
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
    """Extract content-word lemmas from a CoNLL-U file for frequency counting."""
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
# Backend: legacy KER
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_legacy(file_path: str, num_keywords: int, **_) -> Keywords:
    lemmas = _extract_lemmas(file_path)
    counts = Counter(lemmas)
    return [(lemma, float(cnt)) for lemma, cnt in counts.most_common(num_keywords)]


# ═══════════════════════════════════════════════════════════════════════════════
# Backend: YAKE
# ═══════════════════════════════════════════════════════════════════════════════

def _load_yake():
    try:
        import yake  # type: ignore
        return yake
    except ImportError as exc:
        print(f"[Error] YAKE import failed: {exc}\nRun: pip install yake", file=sys.stderr)
        sys.exit(1)

def _extract_yake(file_path: str, num_keywords: int, lang: str = "cs", max_words: int = 3, **_) -> Keywords:
    yake = _load_yake()
    text = _extract_surface_text(file_path)
    if not text:
        return []

    extractor = yake.KeywordExtractor(
        lan=lang, n=max_words, dedupLim=0.9, dedupFunc="seqm",
        windowsSize=1, top=num_keywords, features=None,
    )
    try:
        raw_kws = extractor.extract_keywords(text)
    except Exception as exc:
        print(f"[Warning] YAKE failed on {file_path}: {exc}", file=sys.stderr)
        return []

    inverted = [(kw, 1.0 / (score + 1e-10)) for kw, score in raw_kws]
    if inverted:
        max_inv  = max(s for _, s in inverted)
        inverted = [(kw, round(s / max_inv, 6)) for kw, s in inverted]

    return inverted


# ═══════════════════════════════════════════════════════════════════════════════
# Backend: KeyBERT (GPU-accelerated, Batched, Chunked)
# ═══════════════════════════════════════════════════════════════════════════════

_keybert_model_instance: object           = None
_keybert_model_name_loaded: Optional[str] = None

def _get_keybert_model(model_name: str):
    global _keybert_model_instance, _keybert_model_name_loaded

    if _keybert_model_instance is not None and _keybert_model_name_loaded == model_name:
        return _keybert_model_instance

    try:
        import torch  # type: ignore
        device = "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError as exc:
        print(f"[Error] PyTorch import failed: {exc}", file=sys.stderr)
        sys.exit(1)

    # COMPATIBILITY PATCH: 'transformers' lazy loading hides core models when PyTorch
    # internals fail. Forcefully inject Dummy classes so sentence-transformers doesn't crash on import.
    try:
        import transformers
        _ = dir(transformers)
        class DummyPreTrained: pass
        for attr in ("PreTrainedModel", "PreTrainedTokenizer", "PretrainedConfig", "AutoModel", "AutoTokenizer"):
            if not hasattr(transformers, attr):
                setattr(transformers, attr, DummyPreTrained)
                if "transformers" in sys.modules:
                    setattr(sys.modules["transformers"], attr, DummyPreTrained)
    except Exception:
        pass

    try:
        from keybert import KeyBERT  # type: ignore
    except ImportError as exc:
        print(f"[Error] KeyBERT import failed: {exc}\nRun: pip install keybert sentence-transformers", file=sys.stderr)
        sys.exit(1)

    tag = "CUDA" if device == "cuda" else "CPU"
    print(f"[KeyBERT] Loading model '{model_name}' on {tag} …", file=sys.stderr)

    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
        st_model = SentenceTransformer(model_name, device=device)
        _keybert_model_instance    = KeyBERT(model=st_model)
        _keybert_model_name_loaded = model_name
    except Exception as exc:
        print(f"[Error] Failed to load KeyBERT model '{model_name}': {exc}", file=sys.stderr)
        sys.exit(1)

    return _keybert_model_instance


def _extract_keybert(
    file_path: Union[str, List[str]],
    num_keywords: int,
    max_words: int = 3,
    keybert_model: str = DEFAULT_KEYBERT_MODEL,
    use_mmr: bool = True,
    diversity: float = 0.5,
    **_,
) -> Union[Keywords, List[Keywords]]:
    is_batch = isinstance(file_path, list)
    paths = file_path if is_batch else [file_path]

    texts = []
    valid_indices = []
    for i, p in enumerate(paths):
        t = _extract_surface_text(p)
        if t:
            texts.append(t)
            valid_indices.append(i)

    if not texts:
        return [[] for _ in paths] if is_batch else []

    kw_model = _get_keybert_model(keybert_model)

    chunk_size = 400
    overlap = 50
    all_chunks = []
    doc_chunk_map = []

    for doc_idx, text in enumerate(texts):
        words = text.split()
        if len(words) > chunk_size:
            for i in range(0, len(words), max(1, chunk_size - overlap)):
                chunk = " ".join(words[i:i + chunk_size])
                if chunk:
                    all_chunks.append(chunk)
                    doc_chunk_map.append(doc_idx)
        else:
            all_chunks.append(text)
            doc_chunk_map.append(doc_idx)

    try:
        results = kw_model.extract_keywords(
            all_chunks,
            keyphrase_ngram_range=(1, max_words),
            stop_words=None,
            use_mmr=use_mmr,
            diversity=diversity,
            top_n=num_keywords,
        )

        if isinstance(results, list) and len(results) > 0 and isinstance(results[0], tuple):
            results = [results]

        doc_keyword_scores = [{} for _ in range(len(texts))]
        for chunk_idx, chunk_res in enumerate(results):
            doc_idx = doc_chunk_map[chunk_idx]
            target_dict = doc_keyword_scores[doc_idx]
            for kw, score in chunk_res:
                if kw not in target_dict or score > target_dict[kw]:
                    target_dict[kw] = score

        final_valid_results = []
        for kw_scores in doc_keyword_scores:
            sorted_kws = sorted(kw_scores.items(), key=lambda x: x[1], reverse=True)[:num_keywords]
            final_valid_results.append([(kw, round(float(s), 6)) for kw, s in sorted_kws])

        full_results = []
        valid_ptr = 0
        for i in range(len(paths)):
            if i in valid_indices:
                full_results.append(final_valid_results[valid_ptr])
                valid_ptr += 1
            else:
                full_results.append([])

        return full_results if is_batch else full_results[0]

    except Exception as exc:
        print(f"[Warning] KeyBERT extraction failed: {exc}", file=sys.stderr)
        return [[] for _ in paths] if is_batch else []


# ═══════════════════════════════════════════════════════════════════════════════
# Backend registry and public dispatcher
# ═══════════════════════════════════════════════════════════════════════════════

_BACKENDS: dict = {
    "legacy":  _extract_legacy,
    "yake":    _extract_yake,
    "keybert": _extract_keybert,
}

def extract_keywords(
    file_path: Union[str, List[str]],
    method: str,
    num_keywords: int,
    **kwargs,
) -> Union[Keywords, List[Keywords]]:
    fn = _BACKENDS.get(method)
    if fn is None:
        raise ValueError(f"Unknown method '{method}'. Choose from: {', '.join(_BACKENDS)}")

    if isinstance(file_path, list):
        if method == "keybert":
            return fn(file_path, num_keywords, **kwargs)
        else:
            return [fn(p, num_keywords, **kwargs) for p in file_path]
    return fn(file_path, num_keywords, **kwargs)


# ═══════════════════════════════════════════════════════════════════════════════
# Worker
# ═══════════════════════════════════════════════════════════════════════════════

def _process_document_task(task: tuple) -> List[Tuple[str, Keywords]]:
    (file_paths, method, num_keywords, indiv_out_dir,
     lang, max_words, keybert_model, use_mmr, diversity) = task

    is_batch = isinstance(file_paths, list)
    paths = file_paths if is_batch else [file_paths]

    results = extract_keywords(
        file_paths,
        method        = method,
        num_keywords  = num_keywords,
        lang          = lang,
        max_words     = max_words,
        keybert_model = keybert_model,
        use_mmr       = use_mmr,
        diversity     = diversity,
    )

    keywords_list = results if is_batch else [results]
    output = []

    for file_path, keywords in zip(paths, keywords_list):
        doc_id = Path(file_path).stem
        if keywords and indiv_out_dir:
            out_csv = Path(indiv_out_dir) / f"{doc_id}_keywords.csv"
            try:
                with open(out_csv, "w", encoding="utf-8", newline="") as fh:
                    writer = csv.writer(fh)
                    writer.writerow(["keyword", "score"])
                    writer.writerows(keywords)
            except Exception as exc:
                print(f"[Warning] Could not write {out_csv}: {exc}", file=sys.stderr)

        output.append((doc_id, keywords))

    return output


# ═══════════════════════════════════════════════════════════════════════════════
# CSV output helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _write_csv_row(output_file: str, doc_id: str, keywords: Keywords, num_keywords: int) -> None:
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
    try:
        import pandas as pd  # type: ignore
        df = pd.read_csv(file_path)
        df.sort_values(by=df.columns[0], inplace=True)
        df.to_csv(file_path, index=False)
        return
    except ImportError:
        pass

    if os.name == "posix" and shutil.which("sort"):
        tmp = file_path + ".tmp"
        try:
            with open(file_path, "r", encoding="utf-8") as fh:
                header = fh.readline()
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(header)
            subprocess.run(f"tail -n +2 '{file_path}' | sort -t ',' -k1 >> '{tmp}'", shell=True, check=True)
            shutil.move(tmp, file_path)
            return
        except Exception as exc:
            print(f"[Warning] POSIX sort failed: {exc}. Using in-memory sort.", file=sys.stderr)
            if os.path.exists(tmp):
                os.remove(tmp)

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
            "Backends: 'yake', 'keybert' (supports CUDA batch chunking), 'legacy'.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("-i", "--input_dir", default=DEFAULT_INPUT_DIR)
    parser.add_argument("-o", "--output_file", default=DEFAULT_OUTPUT_FILE)
    parser.add_argument("-d", "--per_doc_out_dir", default=DEFAULT_PER_DOC_OUT_DIR)
    parser.add_argument("-m", "--method", default=DEFAULT_METHOD, choices=list(_BACKENDS))
    parser.add_argument("-n", "--num_keywords", type=int, default=DEFAULT_NUM_KEYWORDS)
    parser.add_argument("-l", "--lang", default=DEFAULT_LANG)
    parser.add_argument("-w", "--max_words", type=int, default=DEFAULT_MAX_WORDS)

    keybert_group = parser.add_argument_group("KeyBERT options")
    keybert_group.add_argument("--keybert-model", dest="keybert_model", default=DEFAULT_KEYBERT_MODEL)
    keybert_group.add_argument("--no-mmr", action="store_true", default=DEFAULT_NO_MMR)
    keybert_group.add_argument("--diversity", type=float, default=DEFAULT_DIVERSITY)
    keybert_group.add_argument("--batch_size", type=int, default=DEFAULT_BATCH_SIZE,
                               help="Max docs per KeyBERT batch. (default: %(default)s)")

    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)

    args = parser.parse_args()

    # Dynamic substitution for output paths based on chosen method
    suffix_l = {"legacy": "l", "yake": "y", "keybert": "kb"}.get(args.method, args.method)
    suffix_u = {"legacy": "L", "yake": "Y", "keybert": "KB"}.get(args.method, args.method.upper())

    if isinstance(args.output_file, str):
        args.output_file = args.output_file.replace("{method}", suffix_l).replace("{METHOD}", suffix_u)
    if isinstance(args.per_doc_out_dir, str):
        args.per_doc_out_dir = args.per_doc_out_dir.replace("{method}", suffix_l).replace("{METHOD}", suffix_u)

    if args.method == "keybert":
        try:
            import torch  # type: ignore
            if torch.cuda.is_available() and args.workers > 1:
                print("[KeyBERT] GPU detected: forcing --workers 1 to avoid CUDA context conflicts. Relegating to Batch Mode.", file=sys.stderr)
                args.workers = 1
        except ImportError:
            pass

    input_path    = Path(args.input_dir)
    indiv_out_dir = Path(args.per_doc_out_dir)
    indiv_out_dir.mkdir(parents=True, exist_ok=True)

    if not input_path.is_dir():
        print(f"[Error] Input directory not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    header = ["document_id"]
    for i in range(1, args.num_keywords + 1):
        header.extend([f"kw-{i}", f"score-{i}"])
    with open(args.output_file, "w", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerow(header)

    all_files = sorted(input_path.glob("*.conllu"))
    BATCH_SIZE = args.batch_size if args.method == "keybert" else 1

    tasks = []
    for i in range(0, len(all_files), BATCH_SIZE):
        batch_files = [str(p) for p in all_files[i:i + BATCH_SIZE]]

        if BATCH_SIZE == 1:
            batch_files = batch_files[0]

        tasks.append((
            batch_files,
            args.method,
            args.num_keywords,
            str(indiv_out_dir),
            args.lang,
            args.max_words,
            args.keybert_model,
            not args.no_mmr,
            args.diversity,
        ))

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
                "diversity":     args.diversity,
                "batch_size":    args.batch_size}
               if args.method == "keybert" else {}),
        },
        paradata_dir="data_samples/paradata",
        output_types=["csv_per_doc", "csv_summary_row"],
    )

    _BACKEND_COMPONENTS = {
        "legacy":  ["ker"],
        "yake":    ["yake"],
        "keybert": ["keybert", "sentence_transformers"],
    }
    for _comp in _BACKEND_COMPONENTS.get(args.method, []):
        _logger.log_component(_comp)

    print(f"--- Keyword Extraction | method={args.method} | {len(all_files)} documents | workers={args.workers} ---")

    processed_count = 0
    try:
        mp_context = multiprocessing.get_context("spawn")

        with ProcessPoolExecutor(max_workers=args.workers, mp_context=mp_context) as executor:
            futures = {executor.submit(_process_document_task, t): t[0] for t in tasks}

            for future in as_completed(futures):
                batch_paths = futures[future]
                try:
                    batch_results = future.result()
                    for doc_id, keywords in batch_results:
                        _write_csv_row(args.output_file, doc_id, keywords, args.num_keywords)
                        processed_count += 1
                        _logger.log_success("csv_per_doc",     count=1)
                        _logger.log_success("csv_summary_row", count=1)

                    print(f"  Processed {processed_count}/{len(all_files)} …")
                except Exception as exc:
                    print(f"[Error] Task containing '{batch_paths}' failed: {exc}", file=sys.stderr)
                    if isinstance(batch_paths, list):
                        for p in batch_paths:
                            _logger.log_skip(str(p), str(exc))
                    else:
                        _logger.log_skip(str(batch_paths), str(exc))
    finally:
        _logger.finalize(input_total=len(all_files))

    print("--- Sorting master results … ---")
    _sort_csv_file(args.output_file)
    print(f"--- Done. {processed_count}/{len(all_files)} documents processed. Output: {args.output_file} ---")

if __name__ == "__main__":
    main()