# 🤝 Contributing to the NLP Enrichment Pipeline of the ATRIUM project

Welcome! This repository provides a professional-grade workflow for taking ordered text 
(derived from ALTO XML [^2]) and enriching it with high-level linguistic features like syntax,
morphology, and named entity recognition tags.

The previous step: [atrium-alto-postprocess](https://github.com/ARUP-CAS/atrium-alto-postprocess)

## 🏗️ Project Contributions & Capabilities

This pipeline bridges the gap between raw OCR text and structured digital humanities 
research data through three major contributions, as detailed in 
the [Workflow Stages](README.md#workflow-stages) of the main README [^1].

### 1. Advanced Linguistic Annotation

The pipeline utilizes a couple of Lindat/CLARIAH-CZ APIs to perform deep analysis on archival text:

* **UDPipe [^5] Processing:** Generates Universal Dependencies (CoNLL-U files) containing 
word lemmas and part-of-sentence (POS) tags.
* **NameTag [^6] Recognition:** Annotates Named Entities (NER) using the CNEC 2.0 standard to 
identify people, locations, and organizations.

for both:
* **Scale Management:** Automatically handles large documents by splitting them into 
~900-word chunks to respect API limits, ensuring no data is lost during remote processing.

### 2. Multi-Layered Archival Outputs

Archive managers can generate multiple data formats simultaneously based on their preservation and access needs:

| Format               | Content Detail                                             | Primary Use Case                                                  |
|----------------------|------------------------------------------------------------|-------------------------------------------------------------------|
| **Summary CSV**      | Textline-level table with lemmas and NE explanations [^3]. | General spreadsheet-based research and data cleaning.             |
| **Enriched CoNLL-U** | Syntax trees with NER tags in the `MISC` column.           | Computational linguistics and NLP model training.                 |
| **TEITOK XML**       | TEI-style XML merging text with ALTO coordinates.          | Web-based digital editions with facsimile (side-by-side) viewing. |
| **Global Stats**     | Aggregated counts of entities across all documents.        | High-level archive inventory and trend analysis.                  |

---

## 📞 Contacts & Acknowledgements

For technical questions contact **lutsai.k@gmail.com** 

* **Developed by:** UFAL [^7]
* **Funded by:** ATRIUM [^4]
* **Models:** 
  * NameTag 3 [^6] 
  * UDPipe 2 [^5]
  
**©️ 2026 UFAL & ATRIUM**


[^2]: https://github.com/ARUP-CAS/atrium-alto-postprocess
[^3]: https://ufal.mff.cuni.cz/~strakova/cnec2.0/ne-type-hierarchy.pdf
[^4]: https://atrium-research.eu/
[^5]: https://lindat.mff.cuni.cz/services/udpipe/api-reference.php
[^6]: https://lindat.mff.cuni.cz/services/nametag/api-reference.php
[^1]: https://github.com/ARUP-CAS/atrium-nlp-enrich
[^7]: https://ufal.mff.cuni.cz/home-page