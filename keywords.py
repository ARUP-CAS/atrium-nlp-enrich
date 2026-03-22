import os
import csv
import argparse
import multiprocessing
import subprocess
import shutil
from pathlib import Path
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from atrium_paradata import ParadataLogger

# --- Configuration ---
DEFAULT_INDIVIDUAL_OUTPUT_DIR = "data_samples/KW_PER_DOC"
DEFAULT_INPUT_CONLLU_DIR = "data_samples/UDP"


def extract_keywords_from_conllu(file_path: str, num_keywords: int) -> list[tuple[str, float]]:
    """
    Parses a CoNLL-U file, extracts lemmas of specific Parts of Speech (Nouns, Proper Nouns, Adjectives),
    and calculates their frequency as the 'score'.
    """
    valid_pos = {'NOUN', 'PROPN', 'ADJ'}
    lemmas = []

    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue

                parts = line.split('\t')

                if len(parts) == 10:
                    lemma = parts[2]
                    upos = parts[3]

                    if upos in valid_pos and lemma != '_':
                        if len(lemma) > 1 and lemma.isalpha():
                            lemmas.append(lemma.lower())
    except Exception as e:
        print(f"[Warning] Could not read CoNLL-U file {file_path}: {e}")
        return []

    lemma_counts = Counter(lemmas)
    top_keywords = [(lemma, float(count)) for lemma, count in lemma_counts.most_common(num_keywords)]

    return top_keywords


def process_document_task(task):
    """Worker function for multiprocessing."""
    file_path, num_keywords, indiv_out_dir = task
    doc_id = Path(file_path).stem

    keywords = extract_keywords_from_conllu(file_path, num_keywords)

    if keywords and indiv_out_dir:
        out_csv = Path(indiv_out_dir) / f"{doc_id}_keywords.csv"
        try:
            with open(out_csv, 'w', encoding='utf-8', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['keyword', 'score'])
                writer.writerows(keywords)
        except Exception as e:
            print(f"[Warning] Could not write {out_csv}: {e}")

    return doc_id, keywords


def write_csv_row(output_file: str, doc_id: str, keywords: list[tuple[str, float]], num_keywords: int):
    """Appends a single document's keywords to the master CSV."""
    row = [doc_id]
    for i in range(num_keywords):
        if i < len(keywords):
            kw, score = keywords[i]
            row.extend([kw, score])
        else:
            row.extend(['', ''])

    with open(output_file, 'a', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(row)


def sort_csv_file(file_path: str):
    """
    Sorts the master CSV alphabetically by document_id.
    Implements safe out-of-core chunking (POSIX sort / pandas) to avoid OOM
    errors on massive datasets before falling back to in-memory sorting.
    """
    # 1. Try Pandas if available (highly optimized C-backend sort)
    try:
        import pandas as pd
        df = pd.read_csv(file_path)
        df.sort_values(by=df.columns[0], inplace=True)
        df.to_csv(file_path, index=False)
        return
    except ImportError:
        pass

    # 2. Try POSIX External Sort (highly efficient for files larger than RAM)
    if os.name == 'posix' and shutil.which('sort'):
        temp_path = file_path + '.tmp'
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                header = f.readline()
            with open(temp_path, 'w', encoding='utf-8') as f:
                f.write(header)

            # Use external UNIX sort, skipping the header, and append to temp file
            subprocess.run(
                f"tail -n +2 '{file_path}' | sort -t ',' -k1 >> '{temp_path}'",
                shell=True, check=True
            )
            shutil.move(temp_path, file_path)
            return
        except Exception as e:
            print(f"[Warning] Posix sort failed: {e}. Falling back to in-memory sort.")
            if os.path.exists(temp_path):
                os.remove(temp_path)

    # 3. Fallback to in-memory sort (Original Implementation)
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            header = next(reader)
            rows = sorted(list(reader), key=lambda x: x[0])

        with open(file_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows(rows)
    except Exception as e:
        print(f"[Error] Could not sort file {file_path}: {e}")


def main():
    parser = argparse.ArgumentParser(description="Extract keywords from CoNLL-U files.")
    parser.add_argument('-i', '--input_dir', required=True, default=DEFAULT_INPUT_CONLLU_DIR,
                        help="Directory containing .conllu files")
    parser.add_argument('-o', '--output_file', default="keywords_master.csv",
                        help="Master CSV output file path")
    parser.add_argument('-n', '--num_keywords', type=int, default=20,
                        help="Number of keywords to extract per document")
    parser.add_argument('-l', '--lang', default='cs',
                        help="Language code for keyword extraction (e.g. 'cs', 'en')")
    parser.add_argument('-w', '--max_words', type=int, default=3,
                        help="Maximum number of words per keyword entry")
    parser.add_argument('-d', '--per_doc_out_dir', default=DEFAULT_INDIVIDUAL_OUTPUT_DIR,
                        help="Output directory for per-document keyword CSV files")
    parser.add_argument('--workers', type=int, default=multiprocessing.cpu_count(),
                        help="Maximum number of parallel worker processes")
    args = parser.parse_args()

    input_path = Path(args.input_dir)
    indiv_out_path = Path(args.per_doc_out_dir)
    indiv_out_path.mkdir(parents=True, exist_ok=True)

    if not input_path.exists() or not input_path.is_dir():
        print(f"Input directory not found: {input_path}")
        return

    header = ['document_id']
    for i in range(1, args.num_keywords + 1):
        header.extend([f'keyword{i}', f'score{i}'])

    with open(args.output_file, 'w', encoding='utf-8', newline='') as f:
        csv.writer(f).writerow(header)

    tasks = [
        (str(p), args.num_keywords, str(indiv_out_path))
        for p in input_path.glob("*.conllu")
    ]

    processed_count = 0
    futures_map = {}

    _logger = ParadataLogger(
        program="nlp-enrich",
        config={
            "script": "keywords",
            "input_dir": str(args.input_dir),
            "lang": str(args.lang),
            "max_words": int(args.max_words),
            "num_keywords": int(args.num_keywords),
            "per_doc_out_dir": str(args.per_doc_out_dir),
            "output_file": str(args.output_file),
        },
        paradata_dir="paradata",
        output_types=["csv_per_doc", "csv_summary_row"],
    )

    print(f"--- Starting Keyword Lemmatization Process on {len(tasks)} documents ---")
    _total_inputs = len(tasks)

    try:
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            for task in tasks:
                future = executor.submit(process_document_task, task)
                futures_map[future] = task[0]

            for future in as_completed(futures_map):
                doc_path = futures_map[future]
                try:
                    result = future.result()
                    if result:
                        res_doc_id, keywords = result
                        write_csv_row(args.output_file, res_doc_id, keywords, args.num_keywords)
                        processed_count += 1
                        _logger.log_success("csv_per_doc", count=1)
                        _logger.log_success("csv_summary_row", count=1)
                        if processed_count % 100 == 0:
                            print(f"Processed {processed_count} documents...")
                except Exception as e:
                    print(f"[Error] Failed processing document '{doc_path}': {e}")
                    _logger.log_skip(str(doc_path), str(e))
    finally:
        _logger.finalize(input_total=_total_inputs)

    print("\n--- Processing Complete. Sorting Master Results... ---")
    sort_csv_file(args.output_file)

    print("--- Done! ---")
    print(f"Total documents processed: {processed_count}")


if __name__ == "__main__":
    main()