import json
import time
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Any, List
import requests


class VocabularyManager:
    """
    Manages the TEATER/AMCR vocabulary structure for prompt injection.
    Supports dynamic fetching via HTTP GET requests and local JSON caching.
    """

    # API Constants mapped from the original harvest script
    AMCR_OAI_BASE = "https://api.aiscr.cz/2.2/oai"
    AMCR_NS = {
        "oai": "http://www.openarchives.org/OAI/2.0/",
        "amcr": "https://api.aiscr.cz/schema/amcr/2.2/",
    }

    THEMATIC_PREFIXES: Dict[str, List[str]] = {
        "Site Types": [
            "hradiště", "pohřebiště", "sídliště", "hrad", "tvrz", "kostel",
            "mohyla", "studna", "depot", "jáma", "příkop", "val", "sklep",
            "zaniklá", "opevnění", "areál", "objekt", "zásobní",
        ],
        "Find Types": [
            "keramika", "kost", "hrob", "záušnice", "nůž", "brousek",
            "bronz", "kámen", "sklo", "mazanice", "nádoba", "střep",
            "oštěp", "jehlice", "mlat", "zásobnice", "mazanice", "kachel",
            "konstrukční prvek", "navážka", "malta", "cihla", "glazura",
            "zlomek", "fragment", "dno", "okraj", "ucho", "výduť",
        ],
        "Methods": [
            "povrchový sběr", "plošný odkryv", "sonda", "výkop", "průzkum",
            "dokumentace", "geodetický", "stavebně-historický", "záchranný",
            "badatelský", "dohled", "terénní", "revize",
        ],
        "Chronology": [
            "středověk", "eneolit", "paleolit", "neolit", "bronzová",
            "halštatská", "laténská", "novověk", "pravěk", "datum",
            "přesné datum", "někdy v letech", "stol", "století",
        ],
        "Location & Admin": [
            "katastrální", "parcela", "okres", "obec", "lokalita",
            "poloha", "mapa", "mapový", "sekce",
        ],
        "Documentation": [
            "fotografie", "plán", "kresba", "zpráva", "hlášení", "nálezová",
            "příloha", "plánek", "negativy", "diapozitiv",
        ],
        "Finds Context": [
            "ojedinělý nález", "náhodný nález", "nález v druhotné",
            "záchranný nález", "pohřeb", "kostrový", "žárový",
        ],
    }

    def __init__(self, vocab_path: str = "data_samples/teater_nested_vocab.json"):
        self.vocab_path = Path(vocab_path)
        self.vocab_data: Dict[str, Any] = {}

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
                        # We use the Czech term as the primary key for the LLM
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

    def _assign_theme(self, cs_term: str) -> str:
        """
        Assign a thematic group to a Czech term by substring matching.
        Falls back to 'Other' for unmatched terms.
        """
        cs_lower = cs_term.lower()
        for theme, keywords in self.THEMATIC_PREFIXES.items():
            if any(kw in cs_lower for kw in keywords):
                return theme
        return "Other"

    def sync_and_build_nested_taxonomy(self):
        """
        Executes the GET requests to gather raw term pairs and encapsulates them
        into the nested dictionary structure grouped by theme required for the
        LLM system prompt.
        """
        print("Syncing remote vocabularies...")
        amcr_terms = self.fetch_amcr_vocab()

        # Partition flat term list into thematic groups
        themed: Dict[str, Dict] = {}
        for cs_key, pair in amcr_terms.items():
            theme = self._assign_theme(cs_key)
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
        # The new format partitions into multiple thematic keys including "Other".
        known_old_keys = {"Archaeological Terms (AMCR)"}
        if set(self.vocab_data.keys()) <= known_old_keys:
            print(
                "[vocab] WARNING: Cached vocabulary is in the old flat format. "
                "Re-syncing to build thematic grouping. "
                "Delete the cache file and re-run vocab_manager.py if this repeats."
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
    manager = VocabularyManager(vocab_path="data_samples/teater_nested_vocab.json")
    manager.sync_and_build_nested_taxonomy()
    prompt_injection_string = manager.get_prompt_string()
    print("\n[Preview of serialized LLM Prompt String]")
    print(prompt_injection_string[:500] + "\n... [truncated]")