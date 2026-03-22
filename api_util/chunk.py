import sys
import os


def write_chunk(output_dir, chunk_index, lines_list):
    """Write a list of text lines to a numbered chunk file.

    FIX #4: Lines are joined with newlines rather than spaces so that OCR
    line boundaries are preserved.  UDPipe's tokeniser treats newlines as
    sentence-boundary hints, which avoids merging OCR text lines (and thus
    separate sentences) into a single run-on paragraph.
    """
    filename = os.path.join(output_dir, f"chunk_{chunk_index}.txt")
    with open(filename, 'w', encoding='utf-8') as out:
        out.write("\n".join(lines_list))


def main():
    # Basic argument validation
    if len(sys.argv) < 4:
        print("Usage: chunk.py <infile> <outdir> <word_limit>")
        sys.exit(1)

    infile = sys.argv[1]
    outdir = sys.argv[2]
    limit = int(sys.argv[3])

    # Ensure output directory exists
    if not os.path.exists(outdir):
        os.makedirs(outdir)

    # Read the full text extracted from CSV (newline-separated OCR lines)
    with open(infile, 'r', encoding='utf-8') as f:
        text = f.read().strip()

    if not text:
        sys.exit(0)

    # FIX #4: Split by lines rather than all whitespace to preserve OCR line
    # structure.  Empty lines are discarded; each non-empty line is treated as
    # one logical unit (typically one OCR text line / sentence fragment).
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    current_chunk_lines = []
    current_word_count = 0
    chunk_count = 0

    for line in lines:
        words_in_line = len(line.split())

        # If adding this line would reach or exceed the word limit and we
        # already have content, look back for a good sentence boundary.
        if current_word_count + words_in_line >= limit and current_chunk_lines:
            # Intelligent splitting: scan back for the last line ending with
            # sentence-terminal punctuation.
            cut_index = len(current_chunk_lines)  # default: flush everything
            lookback_limit = max(0, len(current_chunk_lines) - 100)

            for j in range(len(current_chunk_lines) - 1, lookback_limit, -1):
                last_word = current_chunk_lines[j].split()[-1] if current_chunk_lines[j].split() else ''
                if last_word and last_word[-1] in ('.', '?', '!'):
                    cut_index = j + 1
                    break

            write_chunk(outdir, chunk_count, current_chunk_lines[:cut_index])
            chunk_count += 1

            # Carry over any lines that were after the cut point.
            leftover = current_chunk_lines[cut_index:]
            current_chunk_lines = leftover
            current_word_count = sum(len(l.split()) for l in leftover)

        current_chunk_lines.append(line)
        current_word_count += words_in_line

    # Write the final chunk
    if current_chunk_lines:
        write_chunk(outdir, chunk_count, current_chunk_lines)


if __name__ == "__main__":
    main()