import json
import time
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable
import requests


class VocabularyManager:
    """
    Manages the TEATER/AMCR vocabulary structure for prompt injection.
    Supports dynamic fetching via HTTP GET requests, external taxonomy configuration,
    and LLM-assisted classification for unknown terms.
    """

    # API Constants mapped from the original harvest script
    AMCR_OAI_BASE = "https://api.aiscr.cz/2.2/oai"
    AMCR_NS = {
        "oai": "http://www.openarchives.org/OAI/2.0/",
        "amcr": "https://api.aiscr.cz/schema/amcr/2.2/",
    }

    def __init__(
            self,
            vocab_path: str = "data_samples/teater_nested_vocab.json",
            config_path: str = "data_samples/taxonomy_config.json",
            llm_predictor: Optional[Callable[[str], str]] = None
    ):
        self.vocab_path = Path(vocab_path)
        self.config_path = Path(config_path)
        self.taxonomy: Dict[str, Any] = self._load_config()
        self.vocab_data: Dict[str, Any] = {}

        # A callable function (e.g., from llm_pipeline.py) that takes a prompt string and returns a string
        self.llm_predictor = llm_predictor

    def _load_config(self) -> Dict[str, Any]:
        """Loads the external taxonomy configuration mapping themes to multi-lingual keywords."""
        if self.config_path.exists():
            with open(self.config_path, 'r', encoding='utf-8') as f:
                return json.load(f)

        # Fallback default taxonomy if config is missing to ensure graceful degradation
        print(f"Warning: Config {self.config_path} not found. Using default internal taxonomy.")
        return {
            "Site Types": {"keywords": {
                "cs": ["hradiště", "pohřebiště", "sídliště", "hrad", "tvrz", "kostel", "mohyla", "studna", "depot",
                       "jáma", "příkop", "val", "sklep", "zaniklá", "opevnění", "areál", "objekt", "zásobní"]}},
            "Find Types": {"keywords": {
                "cs": ["keramika", "kost", "hrob", "záušnice", "nůž", "brousek", "bronz", "kámen", "sklo", "mazanice",
                       "nádoba", "střep", "oštěp", "jehlice", "mlat", "zásobnice", "kachel", "konstrukční prvek",
                       "navážka", "malta", "cihla", "glazura", "zlomek", "fragment", "dno", "okraj", "ucho", "výduť"]}},
            "Methods": {"keywords": {
                "cs": ["povrchový sběr", "plošný odkryv", "sonda", "výkop", "průzkum", "dokumentace", "geodetický",
                       "stavebně-historický", "záchranný", "badatelský", "dohled", "terénní", "revize"]}},
            "Chronology": {"keywords": {
                "cs": ["středověk", "eneolit", "paleolit", "neolit", "bronzová", "halštatská", "laténská", "novověk",
                       "pravěk", "datum", "přesné datum", "někdy v letech", "stol", "století"]}},
            "Location & Admin": {"keywords": {
                "cs": ["katastrální", "parcela", "okres", "obec", "lokalita", "poloha", "mapa", "mapový", "sekce"]}},
            "Documentation": {"keywords": {
                "cs": ["fotografie", "plán", "kresba", "zpráva", "hlášení", "nálezová", "příloha", "plánek", "negativy",
                       "diapozitiv"]}},
            "Finds Context": {"keywords": {
                "cs": ["ojedinělý nález", "náhodný nález", "nález v druhotné", "záchranný nález", "pohřeb", "kostrový",
                       "žárový"]}}
        }

    def fetch_amcr_vocab(self, delay: float = 0.3) -> Dict[str, Dict[str, str]]:
        """
        Harvests controlled-vocabulary term pairs (Czech → English) via GET requests
        from the AMCR OAI-PMH endpoint, handling XML pagination.
        """
        term_mapping = {}
        url = f"{self.AMCR_OAI_BASE}?verb=ListRecords&metadataPrefix=oai_amcr&set=heslo"
        page = 0
        MAX_PAGES = 500  # Guard against infinite loops on broken resumption tokens

        print("[AMCR] Starting OAI-PMH harvest via GET requests...")

        # Establish a persistent session for connection pooling
        session = requests.Session()
        session.headers.update({"User-Agent": "ATRIUM-vocabulary-manager/1.2"})

        while url and page < MAX_PAGES:
            page += 1
            print(f"  [AMCR] Fetching page {page}...")

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

                    # Ensure both languages exist before adding to mapping
                    if cs_text and en_text:
                        term_mapping[cs_text] = {"cs": cs_text, "en": en_text}

            # Handle Resumption Token for Pagination
            rt_elem = root.find(f".//{{{self.AMCR_NS['oai']}}}resumptionToken")
            if rt_elem is not None and rt_elem.text and rt_elem.text.strip():
                token = rt_elem.text.strip()
                url = f"{self.AMCR_OAI_BASE}?verb=ListRecords&resumptionToken={urllib.parse.quote(token)}"
                time.sleep(delay)
            else:
                url = None

        print(f"[AMCR] Harvest complete. {len(term_mapping)} terms collected.")
        return term_mapping

    def _assign_theme(self, term_pair: Dict[str, str]) -> str:
        """
        Assign a thematic group to a term pair by substring matching across configured languages.
        Falls back to 'Other' for unmatched terms.
        """
        for theme, config in self.taxonomy.items():
            for lang, keywords in config.get("keywords", {}).items():
                term_value = term_pair.get(lang, "").lower()
                if any(kw.lower() in term_value for kw in keywords):
                    return theme
        return "Other"

    def classify_with_llm(self, term_pair: Dict[str, str]) -> Optional[str]:
        """
        Uses the injected LLM predictor to categorize an unknown term into the existing taxonomy.
        """
        if not self.llm_predictor:
            return None

        categories = list(self.taxonomy.keys())
        prompt = (
            f"Categorize this archaeological term: '{term_pair.get('cs', '')}' "
            f"(English: '{term_pair.get('en', '')}') into one of the following exact categories: {categories}. "
            "Reply ONLY with the exact category name and nothing else."
        )

        try:
            response_text = self.llm_predictor(prompt).strip()
            # Validate response against known taxonomy keys to prevent hallucinated keys
            for key in categories:
                if key.lower() == response_text.lower():
                    return key
        except Exception as e:
            print(f"  [LLM] Classification error during taxonomy sync: {e}")

        return None

    def sync_and_build_nested_taxonomy(self, use_llm_fallback: bool = False):
        """
        Executes the GET requests to gather raw term pairs and encapsulates them
        into the nested dictionary structure grouped by theme required for the
        LLM system prompt.
        """
        print("Syncing remote vocabularies...")
        raw_terms = self.fetch_amcr_vocab()

        # Initialize dictionary partitions based on current taxonomy configuration
        themed: Dict[str, Dict] = {theme: {} for theme in self.taxonomy.keys()}
        themed["Other"] = {}

        for cs_key, pair in raw_terms.items():
            theme = self._assign_theme(pair)

            # Utilize the LLM fallback logic for unclassified ("Other") terms if enabled
            if theme == "Other" and use_llm_fallback and self.llm_predictor:
                llm_suggested_theme = self.classify_with_llm(pair)
                if llm_suggested_theme and llm_suggested_theme in themed:
                    theme = llm_suggested_theme
                    print(f"  [LLM] Dynamically re-classified '{cs_key}' -> {theme}")

            themed.setdefault(theme, {})[cs_key] = pair

        self.vocab_data = themed
        self.save()

    def load(self) -> Dict[str, Any]:
        """Loads the vocabulary from disk. Triggers re-sync if format is outdated."""
        if not self.vocab_path.exists():
            print(f"Warning: {self.vocab_path} not found. Triggering auto-sync.")
            self.sync_and_build_nested_taxonomy()
            return self.vocab_data

        with open(self.vocab_path, "r", encoding="utf-8") as f:
            self.vocab_data = json.load(f)

        # Detect old flat format: single broad key wrapping all terms.
        known_old_keys = {"Archaeological Terms (AMCR)"}
        if set(self.vocab_data.keys()) <= known_old_keys:
            print(
                "[vocab] WARNING: Cached vocabulary is in the old flat format. "
                "Re-syncing to build thematic grouping based on external config."
            )
            self.sync_and_build_nested_taxonomy()

        return self.vocab_data

    def save(self):
        """Commits the nested dictionary mapping to disk."""
        self.vocab_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.vocab_path, "w", encoding="utf-8") as f:
            json.dump(self.vocab_data, f, indent=4, ensure_ascii=False)
        print(f"Vocabulary successfully cached to {self.vocab_path}")

    def get_prompt_string(self) -> str:
        """Serializes the nested taxonomy dictionary for direct LLM system prompt injection."""
        if not self.vocab_data:
            self.load()
        return json.dumps(self.vocab_data, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    # Example usage:
    # def dummy_llm_call(prompt: str) -> str:
    #     return "Site Types"

    manager = VocabularyManager(
        vocab_path="data_samples/teater_nested_vocab.json",
        config_path="taxonomy_config.json",
        llm_predictor=None  # Pass your LLM prediction method here
    )

    manager.sync_and_build_nested_taxonomy(use_llm_fallback=False)

    prompt_injection_string = manager.get_prompt_string()
    print("\n[Preview of serialized LLM Prompt String]")
    print(prompt_injection_string[:500] + "\n... [truncated]")