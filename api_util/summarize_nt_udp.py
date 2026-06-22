import argparse
import csv
import os
import re
import sys
from pathlib import Path

from teitok_alto import write_teitok_merged

# Increase CSV field size limit just in case
csv.field_size_limit(sys.maxsize)

# --- CNEC 2.0 Type Hierarchy Mapping ---
CNEC_TYPE_MAP = {
    "a": "Address/Number/Time (General)",
    "A": "Complex Address/Number/Time",
    "ah": "Street address",
    "at": "Phone/Fax number",
    "az": "Zip code",
    "g": "Geographical name (General)",
    "G": "Geographical name (General)",
    "g_": "Geographical name (General)",
    "gu": "Settlement name (City/Town)",
    "gl": "Nature/Landscape name (Mountain/River)",
    "gq": "Urban geographical name (Street/Square)",
    "gr": "Territorial name (State/Region)",
    "gs": "Super-terrestrial name (Star/Planet)",
    "gc": "States/Provinces/Regions",
    "gt": "Continents",
    "gh": "Hydronym (Bodies of water)",
    "i": "Institution name (General)",
    "i_": "Institution name (General)",
    "I": "Institution name (General)",
    "ia": "Conference/Contest",
    "if": "Company/Firm",
    "io": "Organization/Society",
    "ic": "Cult/Educational institution",
    "m": "Media name (General)",
    "mn": "Periodical name (Newspaper/Magazine)",
    "ms": "Radio/TV station",
    "mi": "Internet links",
    "o": "Artifact name (General)",
    "o_": "Artifact name (General)",
    "oa": "Cultural artifact (Book/Painting)",
    "oe": "Measure unit",
    "om": "Currency",
    "or": "Directives, norms",
    "op": "Product (General)",
    "p": "Personal name (General)",
    "p_": "Personal name (General)",
    "P": "Complex personal names",
    "pf": "First name",
    "ps": "Surname",
    "pm": "Second name",
    "ph": "Nickname/Pseudonym",
    "pc": "Inhabitant name",
    "pd": "Academic titles",
    "pp": "Relig./myth persons",
    "me": "Email address",
    "t": "Time expression (General)",
    "T": "Complex time expressions",
    "td": "Day",
    "th": "Hour",
    "tm": "Month",
    "ty": "Year",
    "tf": "Holiday/Feast",
    "tt": "Time block",
    "n": "Number expression (General)",
    "N": "Complex number expressions",
    "n_": "Number expression (General)",
    "na": "Age",
    "nb": "Volu-metric number",
    "nc": "Cardinal number",
    "ni": "Itemizer (1.)",
    "no": "Ordinal number",
    "ns": "Sport score",
    "unk": "Unknown Type",
    "O": "None",
    "C": "Complex bibliographic expression",
}

def _float_or_none(value):
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def bool_from_str(s, default=False):
    if s is None:
        return default
    if isinstance(s, bool):
        return s
    s = str(s).strip().lower()
    return s in ("1", "true", "yes", "y", "on")


def load_config(config_path="api_config.txt"):
    if not os.path.exists(config_path):
        return
    with open(config_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key not in os.environ:
                os.environ[key] = value


def sanitize_filename(name):
    return re.sub(r'[\\/*?:"<>|]', "_", name)


def get_ne_explanation(raw_tag):
    if raw_tag == "O" or not raw_tag or raw_tag == "_":
        return ""
    if raw_tag.startswith("B-") or raw_tag.startswith("I-"):
        primary = raw_tag.split("|")[0]
        short_code = primary[2:]
        return CNEC_TYPE_MAP.get(short_code, f"Unknown Code ({short_code})")
    return ""


def get_sorted_tsv_content(doc_tsv_dir):
    all_data = []
    files = list(Path(doc_tsv_dir).glob("*.tsv"))

    def sort_key(filepath):
        try:
            match = re.search(r"-(\d+)\.tsv$", filepath.name)
            if match:
                return int(match.group(1))
            return 0
        except Exception:
            return 0

    files.sort(key=sort_key)

    for fpath in files:
        with open(fpath, "r", encoding="utf-8") as f:
            next(f, None)
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split("\t")
                if len(parts) >= 2:
                    all_data.append({"token": parts[0], "tag": parts[1]})
                else:
                    all_data.append({"token": parts[0], "tag": "O"})
    return all_data


def merge_and_write(conllu_path, tsv_data, output_path):
    tsv_index = 0
    tsv_len = len(tsv_data)
    try:
        with (
            open(conllu_path, "r", encoding="utf-8") as f_conllu,
            open(output_path, "w", encoding="utf-8") as f_out,
        ):
            for line in f_conllu:
                stripped_line = line.strip()
                if not stripped_line or stripped_line.startswith("#"):
                    f_out.write(line)
                    continue

                cols = stripped_line.split("\t")
                if len(cols) >= 2 and "-" not in cols[0] and "." not in cols[0]:
                    if tsv_index < tsv_len:
                        tsv_item = tsv_data[tsv_index]
                        new_attr = f"NER={tsv_item['tag']}"
                        if len(cols) > 9:
                            if cols[9] == "_":
                                cols[9] = new_attr
                            else:
                                cols[9] += f"|{new_attr}"
                        else:
                            while len(cols) < 9:
                                cols.append("_")
                            cols.append(new_attr)
                        f_out.write("\t".join(cols) + "\n")
                        tsv_index += 1
                    else:
                        f_out.write(line)
                else:
                    f_out.write(line)
        return True
    except Exception as e:
        print(f"Error merging {conllu_path}: {e}", file=sys.stderr)
        return False


def parse_features(feat_str):
    if feat_str == "_" or not feat_str:
        return {}
    return {k: v for item in feat_str.split("|") if "=" in item for k, v in [item.split("=", 1)]}


def parse_misc(misc_str):
    if misc_str == "_" or not misc_str:
        return {}
    misc = {}
    for item in misc_str.split("|"):
        if "=" in item:
            k, v = item.split("=", 1)
            misc[k] = v
        else:
            misc[item] = "Yes"
    return misc


def write_document_csv(rows, out_path):
    if not rows:
        return
    feature_keys = set()
    misc_keys = set()
    for r in rows:
        for k in r.keys():
            if k.startswith("udpipe.feats."):
                feature_keys.add(k)
            elif k.startswith("udpipe.misc."):
                misc_keys.add(k)

    header = (
        ["page_id", "token", "lemma", "position", "nameTag", "NE"]
        + sorted(list(feature_keys))
        + sorted(list(misc_keys))
    )
    try:
        with open(out_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=header, quoting=csv.QUOTE_ALL, quotechar='"')
            writer.writeheader()
            writer.writerows(rows)
    except Exception as e:
        print(f"  [Error] writing {out_path}: {e}", file=sys.stderr)


# ── FIX #8: single-pass reader ───────────────────────────────────────────────


def _collect_merged_rows(merged_filepath):
    """Parse a merged CoNLL-U file in a single pass and return both the
    per-token rows needed for the document CSV and the per-page entity
    counts needed for the summary CSV.

    FIX #8: Previously ``process_merged_file`` and ``append_summary_row``
    each opened and iterated the merged CoNLL-U independently, causing two
    full file reads per document.  This function does the work once and
    returns both result sets so the caller can feed them to the appropriate
    writers without any additional I/O.

    Page-boundary detection understands two signals (see FIX #3 in
    call_udpipe.py):
      • ``# page_break = true``   – merged/multi-chunk files
      • ``# sent_id = 1``         – original / legacy files
    """
    all_rows = []
    entities_by_page: dict = {}
    page_counter = 0
    pending_page_break = False
    current_entity_toks: list = []
    current_entity_type = None

    def _flush_entity(page):
        nonlocal current_entity_toks, current_entity_type
        if current_entity_toks and current_entity_type:
            text = " ".join(current_entity_toks)
            entities_by_page.setdefault(page, []).append((text, current_entity_type))
        current_entity_toks, current_entity_type = [], None

    try:
        with open(merged_filepath, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()

                # Page-boundary detection — support both signals.
                if line == "# page_break = true":
                    pending_page_break = True
                    continue

                if line.startswith("# sent_id"):
                    parts = line.split("=", 1)
                    if len(parts) > 1:
                        val = parts[1].strip()
                        if val == "1" or pending_page_break:
                            _flush_entity(page_counter)
                            page_counter += 1
                            pending_page_break = False

                if line.startswith("#") or not line:
                    continue

                cols = line.split("\t")
                if len(cols) < 10 or "-" in cols[0]:
                    continue
                if page_counter == 0:
                    page_counter = 1

                misc = parse_misc(cols[9])
                feats = parse_features(cols[5])
                ner_tag = misc.get("NER", "")

                # ── token row for the document CSV ──────────────────────
                row = {
                    "page_id": page_counter,
                    "token": cols[1],
                    "lemma": cols[2],
                    "position": cols[0],
                    "nameTag": ner_tag,
                    "NE": get_ne_explanation(ner_tag),
                }
                for k, v in feats.items():
                    row[f"udpipe.feats.{k}"] = v
                for k, v in misc.items():
                    if k != "NER":
                        row[f"udpipe.misc.{k}"] = v
                all_rows.append(row)

                # ── entity tracking for the summary CSV ─────────────────
                if ner_tag.startswith("B-"):
                    _flush_entity(page_counter)
                    current_entity_toks = [cols[1]]
                    current_entity_type = get_ne_explanation(ner_tag)
                elif ner_tag.startswith("I-") and current_entity_toks:
                    current_entity_toks.append(cols[1])
                else:
                    _flush_entity(page_counter)

            _flush_entity(page_counter)

    except Exception as exc:
        print(f"  [Warn] collecting merged rows from {merged_filepath}: {exc}", file=sys.stderr)
        return [], {}

    return all_rows, entities_by_page


def process_merged_file(merged_filepath, output_csv_path):
    """Write a per-document CSV from a merged CoNLL-U file.

    Kept for backwards compatibility; internally delegates to
    _collect_merged_rows so that only one file read is performed when
    called from process_single_document (which passes pre-collected rows
    via write_document_csv directly).
    """
    rows, _ = _collect_merged_rows(merged_filepath)
    if rows:
        write_document_csv(rows, output_csv_path)


def _write_summary_rows_from_data(doc_name, entities_by_page, summary_csv_path):
    """Write per-page entity-count rows from pre-collected entity data.

    FIX #8: This replaces the file-based append_summary_row for use inside
    process_single_document, avoiding a second read of the merged CoNLL-U.
    """
    from collections import Counter

    if not summary_csv_path or not entities_by_page:
        return
    top_n = 20
    write_header = not os.path.isfile(summary_csv_path)
    try:
        with open(summary_csv_path, "a", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh, quoting=csv.QUOTE_ALL, quotechar='"')
            if write_header:
                header = ["file", "page"] + [
                    x for i in range(1, top_n + 1) for x in (f"ne{i}", f"type{i}", f"cnt-{i}")
                ]
                w.writerow(header)
            for page_num, ents in sorted(entities_by_page.items()):
                c = Counter(ents).most_common(top_n)
                row = [doc_name, page_num]
                for (ne_text, ne_type), cnt in c:
                    row.extend([ne_text, ne_type, cnt])
                missing = top_n - len(c)
                if missing > 0:
                    row.extend(["", "", 0] * missing)
                w.writerow(row)
    except Exception as exc:
        print(f"  [Warn] writing summary CSV: {exc}", file=sys.stderr)


def append_summary_row(doc_name, merged_conllu_path, summary_csv_path):
    """Append a per-page entity-count row to the global summary CSV.

    Kept as the public API; internally uses _collect_merged_rows.
    Direct callers outside process_single_document still use this entry
    point and pay the cost of one file read.
    """
    _, entities_by_page = _collect_merged_rows(merged_conllu_path)
    _write_summary_rows_from_data(doc_name, entities_by_page, summary_csv_path)


# ── per-document entry point (called from api_4_stats.sh) ────────────────────


def process_single_document(
    conllu_file,
    ne_dir,
    output_dir,
    save_conllu=True,
    save_csv=True,
    save_teitok=False,
    alto_dir=None,
    teitok_out=None,
    pages_dir=None,
    summary_csv=None,
    model_udpipe=None,
    model_nametag=None,
    dpi=None,
    alto_dpi=None
):
    """Process one document: merge NER into CoNLL-U, write CSV/TEITOK, update summary.

    FIX #8: When both a document CSV and a summary CSV are requested, the
    merged CoNLL-U is read in a single pass via _collect_merged_rows, and
    the collected data is passed directly to the two writers.  Previously
    the file was read twice (once by process_merged_file and once by
    append_summary_row).
    """
    conllu_path = Path(conllu_file)
    doc_name = conllu_path.stem
    doc_out_dir = Path(output_dir)
    doc_out_conllu = doc_out_dir / f"{doc_name}.conllu"
    doc_out_csv = doc_out_dir / f"{doc_name}.csv"

    # WITH this corrected block:
    teitok_out_path = Path(teitok_out) / f"{doc_name}.teitok.xml" if teitok_out else None

    if save_teitok and teitok_out_path:
        teitok_out_path.parent.mkdir(parents=True, exist_ok=True)

    if save_teitok and teitok_out_path and not teitok_out_path.exists():
        doc_in_alto = Path(alto_dir) / f"{doc_name}.alto.xml" if alto_dir else None
        write_teitok_merged(
            doc_out_conllu,
            teitok_out_path,
            doc_in_alto,
            doc_id=doc_name,
            model_udpipe=model_udpipe,
            model_nametag=model_nametag,
            image_dir=pages_dir or None,
            dpi=dpi,
            alto_dpi=alto_dpi,
        )

    # Merge NER tags into CoNLL-U if not already done
    merged_conllu_ready = doc_out_conllu.exists()
    if not merged_conllu_ready:
        ne_dir_path = Path(ne_dir)
        if not ne_dir_path.exists():
            print(f"  [Skip] NE dir not found: {ne_dir}", file=sys.stderr)
            return False
        tsv_data = get_sorted_tsv_content(ne_dir_path)
        if not tsv_data:
            print(f"  [Warn] No valid TSV data in {ne_dir}", file=sys.stderr)
            return False
        merged_conllu_ready = merge_and_write(conllu_path, tsv_data, doc_out_conllu)
        if not merged_conllu_ready:
            print(f"  [Error] Failed to create merged CoNLL-U for {doc_name}", file=sys.stderr)
            return False

    # FIX #8: collect token rows and entity data in a single pass when either
    # the document CSV or the summary CSV is needed.  This avoids reading the
    # merged file twice.
    need_csv = save_csv and not doc_out_csv.exists()
    need_summary = bool(summary_csv)

    if need_csv or need_summary:
        rows, entities_by_page = _collect_merged_rows(doc_out_conllu)
        if need_csv:
            write_document_csv(rows, doc_out_csv)
        if need_summary:
            _write_summary_rows_from_data(doc_name, entities_by_page, summary_csv)
    elif save_csv and not doc_out_csv.exists():
        # Fallback: CSV only, no summary (should not normally reach here,
        # but kept for safety).
        process_merged_file(doc_out_conllu, doc_out_csv)

    if save_teitok and teitok_out_path and not teitok_out_path.exists():
        doc_in_alto = Path(alto_dir) / f"{doc_name}.alto.xml" if alto_dir else None
        write_teitok_merged(
            doc_out_conllu,
            teitok_out_path,
            doc_in_alto,
            doc_id=doc_name,
            model_udpipe=model_udpipe,
            model_nametag=model_nametag,
            image_dir=pages_dir or None,
            dpi=dpi,
            alto_dpi=alto_dpi,
        )

    if not save_conllu:
        csv_done = not save_csv or doc_out_csv.exists()
        teitok_done = not save_teitok or (teitok_out_path is not None and teitok_out_path.exists())
        if csv_done and teitok_done:
            try:
                doc_out_conllu.unlink()
            except Exception as e:
                print(f"  [Warn] Could not remove intermediate CoNLL-U: {e}", file=sys.stderr)

    return True


# ── directory-level pipeline (used when running standalone) ──────────────────


def process_pipeline(
    conllu_dir,
    tsv_dir,
    output_dir,
    alto_dir,
    teitok_out,
    save_conllu=True,
    save_csv=True,
    save_teitok=False,
    pages_dir=None,
    model_udpipe=None,
    model_nametag=None,
    summary_csv=None,
    dpi=None, alto_dpi=None
):
    conllu_path_obj = Path(conllu_dir)
    if not conllu_path_obj.exists():
        print(f"Error: CoNLL-U dir not found: {conllu_dir}")
        sys.exit(1)

    conllu_files = sorted(list(conllu_path_obj.glob("*.conllu")))
    print(f"Found {len(conllu_files)} documents to process.")
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    for conllu_file in conllu_files:
        doc_name = conllu_file.stem
        doc_out_dir = Path(output_dir) / doc_name
        doc_out_conllu = doc_out_dir / f"{doc_name}.conllu"
        doc_out_csv = doc_out_dir / f"{doc_name}.csv"
        doc_out_teitok = (Path(teitok_out) / f"{doc_name}.teitok.xml") if teitok_out else None

        need_conllu = save_conllu and not doc_out_conllu.exists()
        need_csv = save_csv and not doc_out_csv.exists()
        need_teitok = save_teitok and doc_out_teitok is not None and not doc_out_teitok.exists()

        if not (need_conllu or need_csv or need_teitok):
            print(f"[Skip] {doc_name}: all requested outputs already exist.")
            continue

        print(f"[Processing] {doc_name}...")
        process_single_document(
            conllu_file=conllu_file,
            ne_dir=Path(tsv_dir) / doc_name,
            output_dir=doc_out_dir,
            save_conllu=save_conllu,
            save_csv=save_csv,
            save_teitok=save_teitok,
            alto_dir=alto_dir,
            teitok_out=teitok_out,
            pages_dir=pages_dir,
            summary_csv=summary_csv,
            model_udpipe=model_udpipe,
            model_nametag=model_nametag,
            dpi=dpi, alto_dpi=alto_dpi
        )

    print("\nPipeline Complete.")


# ── CLI ───────────────────────────────────────────────────────────────────────


def main():
    load_config("api_config.env")
    parser = argparse.ArgumentParser()

    # --- Per-document args (used by api_4_stats.sh) ---
    parser.add_argument(
        "--conllu", default=None, help="Single CoNLL-U file to process (per-document mode)."
    )
    parser.add_argument(
        "--ne-dir", default=None, help="Directory of per-page NE TSV files for this document."
    )
    parser.add_argument(
        "--output-dir", default=None, help="Output directory for this document's results."
    )
    parser.add_argument(
        "--summary-csv",
        default=os.getenv("SUMMARY_CSV"),
        help="Path to the global summary CSV to append entity counts to.",
    )

    # Add to the argparse.ArgumentParser definition:
    parser.add_argument("--dpi", type=_float_or_none, default=os.environ.get("IMAGE_DPI"),
                        help="Output image DPI for TEITOK scaling")
    parser.add_argument("--alto-dpi", type=_float_or_none, default=os.environ.get("ALTO_DPI"), help="Source ALTO DPI")

    # --- Directory-mode args (used when running standalone) ---
    parser.add_argument("--conllu-dir", default=os.getenv("CONLLU_INPUT_DIR"))
    parser.add_argument("--tsv-dir", default=os.getenv("TSV_INPUT_DIR"))
    parser.add_argument("--out-dir", default=os.getenv("SUMMARY_OUTPUT_DIR"))
    parser.add_argument("--tt-dir", default=os.getenv("TEITOK_OUTPUT_DIR"))
    parser.add_argument("--alto-dir", default=os.getenv("ALTO_DIR"))
    parser.add_argument(
        "--pages-dir",
        default=os.getenv("INPUT_PAGES_DIR"),
        help="Directory containing per-page images (doc-N.png) used "
        "to scale ALTO bboxes to the actual PNG resolution.",
    )

    # --- Format flags ---
    parser.add_argument(
        "--save-conllu-ne",
        default=os.getenv("SAVE_CONLLU_NE", "1"),
        help="1/0 whether to keep the merged CoNLL-U per document.",
    )
    parser.add_argument(
        "--save-csv",
        default=os.getenv("SAVE_CSV", "1"),
        help="1/0 whether to write the summary CSV per document.",
    )
    parser.add_argument(
        "--save-teitok",
        default=os.getenv("SAVE_TEITOK", "0"),
        help="1/0 whether to write TEITOK-XML per document.",
    )

    args = parser.parse_args()

    save_conllu = bool_from_str(args.save_conllu_ne, default=True)
    save_csv = bool_from_str(args.save_csv, default=True)
    save_teitok = bool_from_str(args.save_teitok, default=False)

    # ── per-document mode (invoked by api_4_stats.sh with --conllu) ──
    if args.conllu:
        if not args.ne_dir or not args.output_dir:
            print(
                "[Error] --ne-dir and --output-dir are required in per-document mode.",
                file=sys.stderr,
            )
            sys.exit(1)
        ok = process_single_document(
            conllu_file=args.conllu,
            ne_dir=args.ne_dir,
            output_dir=args.output_dir,
            save_conllu=save_conllu,
            save_csv=save_csv,
            save_teitok=save_teitok,
            alto_dir=args.alto_dir,
            teitok_out=args.tt_dir,
            pages_dir=args.pages_dir,
            summary_csv=args.summary_csv,
            model_udpipe=os.getenv("MODEL_UDPIPE"),
            model_nametag=os.getenv("MODEL_NAMETAG"),
        )
        sys.exit(0 if ok else 1)

    # ── directory mode (standalone / legacy) ──
    if not all([args.conllu_dir, args.tsv_dir, args.out_dir]):
        print(
            "[Error] Provide either --conllu/--ne-dir/--output-dir (per-document) "
            "or --conllu-dir/--tsv-dir/--out-dir (directory mode).",
            file=sys.stderr,
        )
        sys.exit(1)

    if save_teitok:
        # FIX #5: when alto_dir is absent, emit a warning rather than a hard
        # error — the pipeline can still produce TEITOK output without bboxes.
        # Only exit if the directory is explicitly set but does not exist on disk,
        # which is most likely a configuration mistake worth surfacing loudly.
        if not args.alto_dir:
            print("[Warn] --alto-dir not set; TEITOK output will have no bboxes.", file=sys.stderr)
        elif not Path(args.alto_dir).exists():
            print(
                f"[Error] A valid --alto-dir is required when save-teitok=true "
                f"('{args.alto_dir}' not found).",
                file=sys.stderr,
            )
            sys.exit(1)

        if args.tt_dir:
            Path(args.tt_dir).mkdir(parents=True, exist_ok=True)

    process_pipeline(
        conllu_dir=args.conllu_dir,
        tsv_dir=args.tsv_dir,
        output_dir=args.out_dir,
        alto_dir=args.alto_dir,
        teitok_out=args.tt_dir,
        save_conllu=save_conllu,
        save_csv=save_csv,
        save_teitok=save_teitok,
        pages_dir=args.pages_dir,
        model_udpipe=os.getenv("MODEL_UDPIPE"),
        model_nametag=os.getenv("MODEL_NAMETAG"),
        summary_csv=args.summary_csv,
        dpi=args.dpi, alto_dpi=args.alto_dpi
    )


if __name__ == "__main__":
    main()
