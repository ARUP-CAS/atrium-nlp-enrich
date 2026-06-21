"""
vocab_manager.py — TEATER/AMCR Vocabulary Manager

Handles:
  • OAI-PMH harvesting of controlled-vocabulary term pairs (Czech ↔ English)
    from the AMCR API via paginated HTTP GET requests.
  • Thematic grouping of raw terms into the nested taxonomy structure required
    for LLM system-prompt injection.
  • Thematic priority sorting: prevents administrative terms from displacing
    content-rich archaeological keywords.
  • Optional LLM-assisted fallback classification for unclassified terms.
  • Deterministic on-disk caching of the nested vocabulary.
  • Memoised, lazily-built prompt string.
"""

import json
import time
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import requests


class VocabularyManager:
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
        self.llm_predictor = llm_predictor
        self._prompt_string_cache: Optional[str] = None

    def _invalidate_cache(self) -> None:
        self._prompt_string_cache = None

    def _load_config(self) -> Dict[str, Any]:
        if self.config_path.exists():
            with open(self.config_path, "r", encoding="utf-8") as f:
                return json.load(f)

        print(f"[vocab] Warning: {self.config_path} not found. Using built-in default taxonomy.")
        return {
            "Site Types": {
                "priority": 10,
                "keywords": {
                    "cs": [
                        "hradiště",
                        "pohřebiště",
                        "sídliště",
                        "hrad",
                        "tvrz",
                        "kostel",
                        "mohyla",
                        "studna",
                        "depot",
                        "jáma",
                        "příkop",
                        "val",
                        "sklep",
                        "zaniklá",
                        "opevnění",
                        "areál",
                        "objekt",
                        "zásobní",
                    ]
                },
            },
            "Find Types": {
                "priority": 8,
                "keywords": {
                    "cs": [
                        "keramika",
                        "kost",
                        "hrob",
                        "záušnice",
                        "nůž",
                        "brousek",
                        "bronz",
                        "kámen",
                        "sklo",
                        "mazanice",
                        "nádoba",
                        "střep",
                        "oštěp",
                        "jehlice",
                        "mlat",
                        "zásobnice",
                        "kachel",
                        "konstrukční prvek",
                        "navážka",
                        "malta",
                        "cihla",
                        "glazura",
                        "zlomek",
                        "fragment",
                        "dno",
                        "okraj",
                        "ucho",
                        "výduť",
                    ]
                },
            },
            "Methods": {
                "priority": 9,
                "keywords": {
                    "cs": [
                        "povrchový sběr",
                        "plošný odkryv",
                        "sonda",
                        "výkop",
                        "průzkum",
                        "dokumentace",
                        "geodetický",
                        "stavebně-historický",
                        "záchranný",
                        "badatelský",
                        "dohled",
                        "terénní",
                        "revize",
                    ]
                },
            },
            "Chronology": {
                "priority": 11,
                "keywords": {
                    "cs": [
                        "středověk",
                        "eneolit",
                        "paleolit",
                        "neolit",
                        "bronzová",
                        "halštatská",
                        "laténská",
                        "novověk",
                        "pravěk",
                        "datum",
                        "přesné datum",
                        "někdy v letech",
                        "stol",
                        "století",
                    ]
                },
            },
            "Location & Admin": {
                "priority": 6,
                "keywords": {
                    "cs": [
                        "katastrální",
                        "parcela",
                        "okres",
                        "obec",
                        "lokalita",
                        "poloha",
                        "mapa",
                        "mapový",
                        "sekce",
                    ]
                },
            },
            "Documentation": {
                "priority": 7,
                "keywords": {
                    "cs": [
                        "fotografie",
                        "plán",
                        "kresba",
                        "zpráva",
                        "hlášení",
                        "nálezová",
                        "příloha",
                        "plánek",
                        "negativy",
                        "diapozitiv",
                    ]
                },
            },
            "Finds Context": {
                "priority": 8,
                "keywords": {
                    "cs": [
                        "ojedinělý nález",
                        "náhodný nález",
                        "nález v druhotné",
                        "záchranný nález",
                        "pohřeb",
                        "kostrový",
                        "žárový",
                    ]
                },
            },
        }

    def fetch_amcr_vocab(self, delay: float = 0.3) -> Dict[str, Dict[str, str]]:
        term_mapping: Dict[str, Dict[str, str]] = {}
        url = f"{self.AMCR_OAI_BASE}?verb=ListRecords&metadataPrefix=oai_amcr&set=heslo"
        page = 0
        MAX_PAGES = 500

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
                        if child.tag == f"{{{amcr_ns}}}heslo" and child.get(xml_lang) == "cs":
                            cs_text = (child.text or "").strip()
                        elif child.tag == f"{{{amcr_ns}}}heslo_en":
                            en_text = (child.text or "").strip()

                    if cs_text and en_text:
                        term_mapping[cs_text] = {"cs": cs_text, "en": en_text}

            rt_elem = root.find(f".//{{{self.AMCR_NS['oai']}}}resumptionToken")
            if rt_elem is not None and rt_elem.text and rt_elem.text.strip():
                token = rt_elem.text.strip()
                url = f"{self.AMCR_OAI_BASE}?verb=ListRecords&resumptionToken={urllib.parse.quote(token)}"
                time.sleep(delay)
            else:
                url = None  # type: ignore[assignment]

        print(f"[AMCR] Harvest complete. {len(term_mapping)} terms collected.")
        return term_mapping

    def _assign_theme(self, term_pair: Dict[str, str]) -> str:
        best_theme = "Other"
        best_priority = -1

        for theme, config in self.taxonomy.items():
            priority = config.get("priority", 0)
            if priority <= best_priority:
                continue
            for lang, keywords in config.get("keywords", {}).items():
                term_value = term_pair.get(lang, "").lower()
                if any(kw.lower() in term_value for kw in keywords):
                    best_priority = priority
                    best_theme = theme
                    break
        return best_theme

    def classify_with_llm(self, term_pair: Dict[str, str]) -> Optional[str]:
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

    def sync_and_build_nested_taxonomy(self, use_llm_fallback: bool = False) -> None:
        print("[vocab] Syncing remote vocabularies…")
        raw_terms = self.fetch_amcr_vocab()

        sorted_themes = sorted(
            self.taxonomy.keys(), key=lambda t: self.taxonomy[t].get("priority", 0), reverse=True
        )
        themed: Dict[str, Dict] = {theme: {} for theme in sorted_themes}
        themed["Other"] = {}

        ADMIN_STOP_WORDS = {
            "zpráva",
            "projekt",
            "číslo",
            "datum",
            "rok",
            "strana",
            "tabulka",
            "příloha",
            "text",
            "obsah",
        }

        for cs_key, pair in raw_terms.items():
            theme = self._assign_theme(pair)

            if theme == "Other" and use_llm_fallback and self.llm_predictor:
                llm_theme = self.classify_with_llm(pair)
                if llm_theme and llm_theme in themed:
                    theme = llm_theme
                    print(f"  [LLM] Re-classified '{cs_key}' → {theme}")

            themed.setdefault(theme, {})[cs_key] = pair

        for theme in list(themed.keys()):
            themed[theme] = dict(
                sorted(
                    themed[theme].items(),
                    key=lambda item: (
                        1 if any(aw in item[0].lower() for aw in ADMIN_STOP_WORDS) else 0,
                        item[0],
                    ),
                )
            )

        self.vocab_data = themed
        self._invalidate_cache()
        self.save()

    def load(self) -> Dict[str, Any]:
        if not self.vocab_path.exists():
            print(f"[vocab] {self.vocab_path} not found — triggering auto-sync.")
            self.sync_and_build_nested_taxonomy()
            return self.vocab_data

        with open(self.vocab_path, "r", encoding="utf-8") as f:
            self.vocab_data = json.load(f)

        self._invalidate_cache()

        known_old_keys = {"Archaeological Terms (AMCR)"}
        if set(self.vocab_data.keys()) <= known_old_keys:
            print(
                "[vocab] WARNING: Cached vocabulary is in the old flat format. "
                "Re-syncing to build thematic grouping based on external config."
            )
            self.sync_and_build_nested_taxonomy()

        return self.vocab_data

    def save(self) -> None:
        self.vocab_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.vocab_path, "w", encoding="utf-8") as f:
            json.dump(
                self.vocab_data,
                f,
                indent=4,
                ensure_ascii=False,
                sort_keys=False,
            )
        self._invalidate_cache()
        print(f"[vocab] Vocabulary cached to {self.vocab_path}")

    def vocab_statistics(self) -> Dict[str, int]:
        if not self.vocab_data:
            self.load()
        return {
            theme: len(terms) if isinstance(terms, dict) else 0
            for theme, terms in self.vocab_data.items()
        }

    def get_prompt_string(self) -> str:
        if self._prompt_string_cache is not None:
            return self._prompt_string_cache

        if not self.vocab_data:
            self.load()

        self._prompt_string_cache = json.dumps(
            self.vocab_data,
            indent=2,
            ensure_ascii=False,
            sort_keys=False,
        )
        return self._prompt_string_cache


if __name__ == "__main__":
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
