import os
import csv
import argparse
import sys
import multiprocessing
import shutil
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from multi_rake import Rake

# --- Configuration ---
# Default directory for individual CSV files
DEFAULT_INDIVIDUAL_OUTPUT_DIR = "data_samples/KW_PER_DOC"


def get_text_from_csv(file_path: str) -> list[str]:
    """Reads a CSV file, ordering the text column by page_num and line_num."""
    rows = []
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Safely parse page and line numbers for sorting
                try:
                    page_num = int(row.get('page_num', 0) or 0)
                except ValueError:
                    page_num = 0

                try:
                    line_num = int(row.get('line_num', 0) or 0)
                except ValueError:
                    line_num = 0

                text = row.get('text', '').strip()
                if text:
                    rows.append((page_num, line_num, text))
    except Exception as e:
        print(f"[Warning] Could not read CSV {file_path}: {e}")
        return []

    # Sort primarily by page_num, then secondarily by line_num
    rows.sort(key=lambda x: (x[0], x[1]))

    # Return just the ordered text strings
    return [row[2] for row in rows]


def save_individual_csv(doc_id: str, keywords: list, output_dir: str):
    """
    Saves the keywords for a single document to its own CSV file.
    Format: keyword, score
    """
    # Sanitize doc_id for filename usage
    safe_name = "".join([c for c in doc_id if c.isalpha() or c.isdigit() or c in (' ', '-', '_')]).strip()
    file_path = os.path.join(output_dir, f"{safe_name}.csv")

    try:
        with open(file_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["keyword", "score"])
            for kw, score in keywords:
                writer.writerow([kw, f"{score:.2f}"])
    except Exception as e:
        print(f"[Warning] Could not write individual CSV for {doc_id}: {e}")


def process_document_task(task_data):
    """
    Worker function to process a single CSV document.
    Args: task_data (tuple): (doc_id, file_path, lang, max_w_count, chunk_size, individual_out_dir)
    """
    doc_id, file_path, lang, max_words, chunk_size, individual_out_dir = task_data

    try:
        # Initialize RAKE locally per process
        rake = Rake(language_code=lang, max_words=max_words)
    except Exception:
        return None

    aggregated_scores = defaultdict(float)
    current_lines = []

    # Get ordered text lines from the CSV
    lines = get_text_from_csv(str(file_path))
    if not lines:
        return None

    for line in lines:
        current_lines.append(line)

        # Process in chunks to manage memory and RAKE performance
        if len(current_lines) >= chunk_size:
            text_chunk = " ".join(current_lines)
            kw_scores = rake.apply(text_chunk)
            for kw, score in kw_scores:
                aggregated_scores[kw] += score
            current_lines = []

    # Process remaining lines
    if current_lines:
        text_chunk = " ".join(current_lines)
        kw_scores = rake.apply(text_chunk)
        for kw, score in kw_scores:
            aggregated_scores[kw] += score

    if not aggregated_scores:
        return None

    # Sort keywords by highest score
    sorted_keywords = sorted(aggregated_scores.items(), key=lambda item: item[1], reverse=True)

    # Write individual CSV file immediately
    if individual_out_dir:
        save_individual_csv(doc_id, sorted_keywords, individual_out_dir)

    return doc_id, sorted_keywords


def create_csv_header(output_file: str, num_keywords: int):
    """Creates the master output CSV file with the correct header."""
    header = ["document_id"]
    for i in range(1, num_keywords + 1):
        header.extend([f"keyword{i}", f"score{i}"])

    with open(output_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(header)


def write_csv_row(output_file: str, doc_id: str, keywords: list, num_keywords: int):
    """Appends a result row to the MASTER CSV."""
    row = [doc_id]
    top_kws = keywords[:num_keywords]

    for kw, score in top_kws:
        row.extend([kw, f"{score:.2f}"])

    # Pad with empty strings if document has fewer keywords than requested
    missing = num_keywords - len(top_kws)
    row.extend(["", ""] * missing)

    with open(output_file, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(row)


def sort_csv_file(csv_file: str):
    """Sorts the CSV file alphabetically by the first column (document_id)."""
    print("--- Sorting Master CSV ---")
    try:
        import pandas as pd
        df = pd.read_csv(csv_file)
        df.sort_values(by=df.columns[0], inplace=True)
        df.to_csv(csv_file, index=False)
        return
    except ImportError:
        pass

    temp_file = csv_file + ".tmp"
    try:
        with open(csv_file, 'r', newline='', encoding='utf-8') as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if not header:
                return
            rows = list(reader)

        rows.sort(key=lambda x: x[0])

        with open(temp_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows(rows)

        shutil.move(temp_file, csv_file)
    except Exception as e:
        print(f"Error during sorting: {e}")
        if os.path.exists(temp_file):
            os.remove(temp_file)


def yield_document_tasks(input_dir: Path, lang: str, max_words: int, chunk_size: int, individual_out_dir: str):
    """Generator that identifies CSV documents in the root directory and yields task data."""
    with os.scandir(input_dir) as entries:
        for entry in entries:
            # Look directly for CSV files
            if entry.is_file() and entry.name.lower().endswith(".csv"):
                # Use the filename (without extension) as the document ID
                doc_id = Path(entry.name).stem
                yield (doc_id, entry.path, lang, max_words, chunk_size, individual_out_dir)


def main():
    parser = argparse.ArgumentParser(
        description="Extract keywords from a directory of CSV documents and save individually and in a master file.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    # Default changed to suggest a directory of CSVs
    parser.add_argument("--input_dir", "-i", default="../INPUT_CSVS", help="Directory containing the input CSV files.")
    parser.add_argument("--workers", "-j", type=int, default=max(1, multiprocessing.cpu_count() - 1),
                        help="Number of parallel worker processes.")
    parser.add_argument("--chunk_size", "-c", type=int, default=10000,
                        help="Number of text lines to process in one memory batch per document.")
    parser.add_argument("--num_keywords", "-n", type=int, default=10,
                        help="Number of top keywords to save per document in the MASTER file.")
    parser.add_argument("--lang", "-l", default="cs", help="Language code (e.g., 'cs', 'en').")
    parser.add_argument("--max_words", "-w", type=int, default=2, help="Maximum length (in words) of a keyword phrase.")
    parser.add_argument("--output_file", "-o", default="keywords_master.csv", help="Master summary CSV file path.")

    # Argument for individual output directory
    parser.add_argument("--per_doc_out_dir", "-d", default=DEFAULT_INDIVIDUAL_OUTPUT_DIR,
                        help="Directory to save individual CSV files for each document.")

    args = parser.parse_args()
    input_path = Path(args.input_dir)
    indiv_out_path = Path(args.per_doc_out_dir)

    if not input_path.exists():
        print(f"Error: Directory '{input_path}' not found.")
        sys.exit(1)

    # Create individual output directory if it doesn't exist
    if not indiv_out_path.exists():
        try:
            os.makedirs(indiv_out_path, exist_ok=True)
            print(f"[Info] Created individual output directory: {indiv_out_path.resolve()}")
        except OSError as e:
            print(f"[Error] Could not create directory {indiv_out_path}: {e}")
            sys.exit(1)

    # Initialize Master CSV
    create_csv_header(args.output_file, args.num_keywords)

    print(f"--- Starting Processing ---")
    print(f"Input: {input_path.resolve()}")
    print(f"Individual Output: {indiv_out_path.resolve()}")
    print(f"Workers: {args.workers}")

    processed_count = 0

    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {}

        task_generator = yield_document_tasks(
            input_path, args.lang, args.max_words, args.chunk_size, str(indiv_out_path)
        )

        for task in task_generator:
            # We map the future to the original file path instead of doc_id
            # so we can track exactly which task finished or failed.
            future = executor.submit(process_document_task, task)
            futures[future] = task[0]  # task[0] is the doc_id

        print(f"--- Processing submitted tasks... ---")

        for future in as_completed(futures):
            doc_id = futures[future]
            try:
                result = future.result()
                if result:
                    res_doc_id, keywords = result
                    # Write to master file (summary)
                    write_csv_row(args.output_file, res_doc_id, keywords, args.num_keywords)
                    processed_count += 1

                    if processed_count % 100 == 0:
                        print(f"Processed {processed_count} documents...")
            except Exception as e:
                print(f"[Error] Failed processing document '{doc_id}': {e}")

    print(f"\n--- Processing Complete. Sorting Master Results... ---")
    sort_csv_file(args.output_file)

    print(f"--- Done! ---")
    print(f"Total documents processed: {processed_count}")
    print(f"Master results: {args.output_file}")
    print(f"Individual files: {indiv_out_path.resolve()}")


if __name__ == "__main__":
    main()