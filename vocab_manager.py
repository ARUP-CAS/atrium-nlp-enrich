"""
vocab_manager.py — TEATER/AMCR Vocabulary Manager

Handles:
  • OAI-PMH harvesting of controlled-vocabulary term pairs (Czech ↔ English)
    from the AMCR API via paginated HTTP GET requests.
  • Thematic grouping of raw terms into the nested taxonomy structure required
    for LLM system-prompt injection.
  • Optional LLM-assisted fallback classification for unclassified terms.
  • Deterministic on-disk caching of the nested vocabulary (sorted JSON).
  • Memoised, lazily-built prompt string — `get_prompt_string()` is called
    once per pipeline run and its result is cached for the lifetime of the
    VocabularyManager instance, avoiding redundant serialisation across the
    potentially thousands of per-document calls that read the same string.

Changes vs. previous version
  • `save()` now writes JSON with `sort_keys=True` for deterministic diffs.
  • `get_prompt_string()` result is memoised in `_prompt_string_cache`.
  • Cache is invalidated automatically on `load()`, `save()`, and
    `sync_and_build_nested_taxonomy()` so stale strings are never returned.
  • `_assign_theme()` priority logic is unchanged.
"""

import json
import time
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests


class VocabularyManager:
    """
    Manages the TEATER/AMCR vocabulary structure for LLM system-prompt injection.

    Supports:
      • Dynamic fetching via paginated OAI-PMH GET requests.
      • External taxonomy configuration (taxonomy_config.json).
      • LLM-assisted classification for terms that do not match any theme.
      • Deterministic JSON caching with sorted keys.
      • Memoised `get_prompt_string()` for efficient repeated access.
    """

    # API constants
    AMCR_OAI_BASE = "https://api.aiscr.cz/2.2/oai"
    AMCR_NS = {
        "oai": "http://www.openarchives.org/OAI/2.0/",
        "amcr": "https://api.aiscr.cz/schema/amcr/2.2/",
    }

    def __init__(
        self,
        vocab_path: str = "data_samples/teater_nested_vocab.json",
        config_path: str = "data_samples/taxonomy_config.json",
        llm_predictor: Optional[Callable[[str], str]] = None,
    ) -> None:
        self.vocab_path = Path(vocab_path)
        self.config_path = Path(config_path)
        self.taxonomy: Dict[str, Any] = self._load_config()
        self.vocab_data: Dict[str, Any] = {}

        # A callable (e.g. from llm_pipeline.py) that takes a prompt string and
        # returns a string — used for LLM-assisted taxonomy classification.
        self.llm_predictor = llm_predictor

        # Memoisation cache for get_prompt_string().
        # Set to None whenever vocab_data changes so the next call rebuilds it.
        self._prompt_string_cache: Optional[str] = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _invalidate_cache(self) -> None:
        """Discard the memoised prompt string whenever vocab_data changes."""
        self._prompt_string_cache = None

    def _load_config(self) -> Dict[str, Any]:
        """Load external taxonomy configuration, falling back to built-in defaults."""
        if self.config_path.exists():
            with open(self.config_path, "r", encoding="utf-8") as f:
                return json.load(f)

        print(
            f"[vocab] Warning: {self.config_path} not found. "
            "Using built-in default taxonomy."
        )
        # Fallback taxonomy — ensures graceful degradation when the config file
        # is missing (e.g. first run on a new machine, or the config was deleted).
        return {
            "Site Types": {
                "priority": 10,
                "keywords": {
                    "cs": [
                        "hradiště", "pohřebiště", "sídliště", "hrad", "tvrz",
                        "kostel", "mohyla", "studna", "depot", "jáma", "příkop",
                        "val", "sklep", "zaniklá", "opevnění", "areál", "objekt",
                        "zásobní",
                    ]
                },
            },
            "Find Types": {
                "priority": 8,
                "keywords": {
                    "cs": [
                        "keramika", "kost", "hrob", "záušnice", "nůž", "brousek",
                        "bronz", "kámen", "sklo", "mazanice", "nádoba", "střep",
                        "oštěp", "jehlice", "mlat", "zásobnice", "kachel",
                        "konstrukční prvek", "navážka", "malta", "cihla", "glazura",
                        "zlomek", "fragment", "dno", "okraj", "ucho", "výduť",
                    ]
                },
            },
            "Methods": {
                "priority": 9,
                "keywords": {
                    "cs": [
                        "povrchový sběr", "plošný odkryv", "sonda", "výkop",
                        "průzkum", "dokumentace", "geodetický",
                        "stavebně-historický", "záchranný", "badatelský",
                        "dohled", "terénní", "revize",
                    ]
                },
            },
            "Chronology": {
                "priority": 11,
                "keywords": {
                    "cs": [
                        "středověk", "eneolit", "paleolit", "neolit", "bronzová",
                        "halštatská", "laténská", "novověk", "pravěk", "datum",
                        "přesné datum", "někdy v letech", "stol", "století",
                    ]
                },
            },
            "Location & Admin": {
                "priority": 6,
                "keywords": {
                    "cs": [
                        "katastrální", "parcela", "okres", "obec", "lokalita",
                        "poloha", "mapa", "mapový", "sekce",
                    ]
                },
            },
            "Documentation": {
                "priority": 7,
                "keywords": {
                    "cs": [
                        "fotografie", "plán", "kresba", "zpráva", "hlášení",
                        "nálezová", "příloha", "plánek", "negativy", "diapozitiv",
                    ]
                },
            },
            "Finds Context": {
                "priority": 8,
                "keywords": {
                    "cs": [
                        "ojedinělý nález", "náhodný nález", "nález v druhotné",
                        "záchranný nález", "pohřeb", "kostrový", "žárový",
                    ]
                },
            },
        }

    # ------------------------------------------------------------------
    # Remote vocabulary harvest
    # ------------------------------------------------------------------

    def fetch_amcr_vocab(self, delay: float = 0.3) -> Dict[str, Dict[str, str]]:
        """
        Harvest controlled-vocabulary term pairs (Czech → English) via paginated
        GET requests from the AMCR OAI-PMH endpoint.

        Returns a flat mapping: ``{cs_term: {"cs": cs_term, "en": en_term}}``.
        """
        term_mapping: Dict[str, Dict[str, str]] = {}
        url = f"{self.AMCR_OAI_BASE}?verb=ListRecords&metadataPrefix=oai_amcr&set=heslo"
        page = 0
        MAX_PAGES = 500  # Guard against infinite loops on broken resumption tokens

        print("[AMCR] Starting OAI-PMH harvest via GET requests…")

        session = requests.Session()
        session.headers.update({"User-Agent": "ATRIUM-vocabulary-manager/1.3"})

        while url and page < MAX_PAGES:
            page += 1
            print(f"  [AMCR] Fetching page {page}…")

            try:
                resp = session.get(url, timeout=60)
                resp.raise_for_status()
            except requests.RequestException as exc:
                print(f"  [AMCR] Network error on page {page}: {exc}")
                break

            try:
                root = ET.fromstring(resp.content)
            except ET.ParseError as exc:
                print(f"  [AMCR] XML parse error on page {page}: {exc}")
                break

            amcr_ns = self.AMCR_NS["amcr"]
            xml_lang = "{http://www.w3.org/XML/1998/namespace}lang"

            for record in root.iter(f"{{{self.AMCR_NS['oai']}}}record"):
                for heslo_block in record.iter(f"{{{amcr_ns}}}heslo"):
                    cs_text = en_text = ""
                    for child in heslo_block:
                        if (
                            child.tag == f"{{{amcr_ns}}}heslo"
                            and child.get(xml_lang) == "cs"
                        ):
                            cs_text = (child.text or "").strip()
                        elif child.tag == f"{{{amcr_ns}}}heslo_en":
                            en_text = (child.text or "").strip()

                    if cs_text and en_text:
                        term_mapping[cs_text] = {"cs": cs_text, "en": en_text}

            # Handle OAI-PMH resumption token
            rt_elem = root.find(f".//{{{self.AMCR_NS['oai']}}}resumptionToken")
            if rt_elem is not None and rt_elem.text and rt_elem.text.strip():
                token = rt_elem.text.strip()
                url = (
                    f"{self.AMCR_OAI_BASE}"
                    f"?verb=ListRecords&resumptionToken={urllib.parse.quote(token)}"
                )
                time.sleep(delay)
            else:
                url = None  # type: ignore[assignment]

        print(f"[AMCR] Harvest complete. {len(term_mapping)} terms collected.")
        return term_mapping

    # ------------------------------------------------------------------
    # Taxonomy assignment
    # ------------------------------------------------------------------

    def _assign_theme(self, term_pair: Dict[str, str]) -> str:
        """
        Assign a thematic group to a term pair via substring matching.

        Uses priority-based best-match: all themes are evaluated and the one
        with the highest ``priority`` value (from taxonomy_config.json) wins
        on conflict. This prevents low-priority categories whose keywords are
        short or ambiguous (e.g. "sklo", "bronz") from pre-empting
        higher-priority categories (e.g. Chronology, Documentation) when both
        match the same term string.

        Returns ``"Other"`` if no theme matches.
        """
        best_theme = "Other"
        best_priority = -1

        for theme, config in self.taxonomy.items():
            priority = config.get("priority", 0)
            if priority <= best_priority:
                continue  # Cannot improve on current best — skip early
            for lang, keywords in config.get("keywords", {}).items():
                term_value = term_pair.get(lang, "").lower()
                if any(kw.lower() in term_value for kw in keywords):
                    best_priority = priority
                    best_theme = theme
                    break  # No need to check other languages for this theme

        return best_theme

    def classify_with_llm(self, term_pair: Dict[str, str]) -> Optional[str]:
        """
        Use the injected LLM predictor to categorise an unknown term into the
        existing taxonomy. Returns ``None`` if no predictor is available or the
        model's response does not match any known category.
        """
        if not self.llm_predictor:
            return None

        categories = list(self.taxonomy.keys())
        prompt = (
            f"Categorize this archaeological term: '{term_pair.get('cs', '')}' "
            f"(English: '{term_pair.get('en', '')}') "
            f"into one of the following exact categories: {categories}. "
            "Reply ONLY with the exact category name and nothing else."
        )

        try:
            response_text = self.llm_predictor(prompt).strip()
            for key in categories:
                if key.lower() == response_text.lower():
                    return key
        except Exception as exc:
            print(f"  [LLM] Classification error during taxonomy sync: {exc}")

        return None

    # ------------------------------------------------------------------
    # Sync + build
    # ------------------------------------------------------------------

    def sync_and_build_nested_taxonomy(self, use_llm_fallback: bool = False) -> None:
        """
        Harvest raw term pairs from AMCR and organise them into the nested
        thematic dictionary required for LLM system-prompt injection.

        Sets ``self.vocab_data`` and writes it to disk. Invalidates the
        memoised prompt string.
        """
        print("[vocab] Syncing remote vocabularies…")
        raw_terms = self.fetch_amcr_vocab()

        themed: Dict[str, Dict] = {theme: {} for theme in self.taxonomy.keys()}
        themed["Other"] = {}

        for cs_key, pair in raw_terms.items():
            theme = self._assign_theme(pair)

            if theme == "Other" and use_llm_fallback and self.llm_predictor:
                llm_theme = self.classify_with_llm(pair)
                if llm_theme and llm_theme in themed:
                    theme = llm_theme
                    print(f"  [LLM] Re-classified '{cs_key}' → {theme}")

            themed.setdefault(theme, {})[cs_key] = pair

        self.vocab_data = themed
        self._invalidate_cache()
        self.save()

    # ------------------------------------------------------------------
    # Load / save
    # ------------------------------------------------------------------

    def load(self) -> Dict[str, Any]:
        """
        Load the vocabulary from disk.

        Triggers a remote re-sync if:
          • The cached file does not exist.
          • The cached file is in the old flat format (pre-thematic-grouping).

        Invalidates the memoised prompt string after loading.
        """
        if not self.vocab_path.exists():
            print(f"[vocab] {self.vocab_path} not found — triggering auto-sync.")
            self.sync_and_build_nested_taxonomy()
            return self.vocab_data

        with open(self.vocab_path, "r", encoding="utf-8") as f:
            self.vocab_data = json.load(f)

        self._invalidate_cache()

        # Detect old flat format (single broad key wrapping all terms)
        known_old_keys = {"Archaeological Terms (AMCR)"}
        if set(self.vocab_data.keys()) <= known_old_keys:
            print(
                "[vocab] WARNING: Cached vocabulary is in the old flat format. "
                "Re-syncing to build thematic grouping based on external config."
            )
            self.sync_and_build_nested_taxonomy()

        return self.vocab_data

    def save(self) -> None:
        """
        Write the nested vocabulary dictionary to disk.

        Uses ``sort_keys=True`` for deterministic output and meaningful diffs.
        Invalidates the memoised prompt string after writing.
        """
        self.vocab_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.vocab_path, "w", encoding="utf-8") as f:
            json.dump(
                self.vocab_data,
                f,
                indent=4,
                ensure_ascii=False,
                sort_keys=True,  # deterministic diffs
            )
        self._invalidate_cache()
        print(f"[vocab] Vocabulary cached to {self.vocab_path}")

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def vocab_statistics(self) -> Dict[str, int]:
        """Return per-theme term counts for diagnostics and sanity checks."""
        if not self.vocab_data:
            self.load()
        return {
            theme: len(terms) if isinstance(terms, dict) else 0
            for theme, terms in self.vocab_data.items()
        }

    # ------------------------------------------------------------------
    # Prompt serialisation (memoised)
    # ------------------------------------------------------------------

    def get_prompt_string(self) -> str:
        """
        Return the nested taxonomy serialised as a JSON string for direct
        injection into the LLM system prompt.

        The result is memoised: repeated calls within the same pipeline run
        return the cached string without re-serialising ``vocab_data``.
        The cache is invalidated automatically whenever ``vocab_data`` changes
        (via ``load()``, ``save()``, or ``sync_and_build_nested_taxonomy()``).
        """
        if self._prompt_string_cache is not None:
            return self._prompt_string_cache

        if not self.vocab_data:
            self.load()

        self._prompt_string_cache = json.dumps(
            self.vocab_data,
            indent=2,
            ensure_ascii=False,
            sort_keys=True,  # deterministic string across runs
        )
        return self._prompt_string_cache


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Example usage:
    #
    # def dummy_llm(prompt: str) -> str:
    #     return "Site Types"
    #
    manager = VocabularyManager(
        vocab_path="data_samples/teater_nested_vocab.json",
        config_path="data_samples/taxonomy_config.json",
        llm_predictor=None,
    )

    manager.sync_and_build_nested_taxonomy(use_llm_fallback=False)

    prompt_str = manager.get_prompt_string()
    print("\n[Preview of serialised LLM prompt string]")
    print(prompt_str[:500] + "\n… [truncated]")

    print("\n[Vocabulary statistics]")
    for theme, count in manager.vocab_statistics().items():
        print(f"  {theme}: {count} terms")