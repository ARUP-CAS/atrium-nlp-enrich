# `archaeo` NER annotation guidelines

Six flat entity types for the domain-specific NameTag 3 model (issue
[#7](https://github.com/ufal/atrium-nlp-enrich/issues/7)). Adapted from the
ArchaeoBERT guidelines (`ART/PER/LOC/CON/MAT/SPE`). Annotate spans over the
displayed text; the exporter turns them into token-level IOB2.

| Type       | Hotkey | Meaning                                                                              |
|------------|--------|--------------------------------------------------------------------------------------|
| `ARTEFACT` | a      | Objects found in the ground (pottery, beams, waste, iron ore, sherds…)               |
| `PERIOD`   | p      | Dates and time periods                                                               |
| `LOCATION` | l      | Municipalities, provinces, countries, full addresses, named institutions             |
| `CONTEXT`  | c      | Human-made features that can contain artefacts (cesspit, moat, ditch, burial mound…) |
| `MATERIAL` | m      | What an artefact is made of (flint, bronze, brick, wood…)                            |
| `SPECIES`  | s      | Animal / plant / human species                                                       |

## Boundary rules (summary)

**ARTEFACT** — Include descriptive adjectives (*"rough-walled sherd"*, *"burnt
wattle and daub"*) **unless** the adjective is a material (*"metal household
goods"* → annotate only *household goods*). Include natural finds (*"iron ore"*,
*"index fossils"*). Do **not** annotate over-general categories (*"artefact"*,
*"finds"*, *"find material"*). Objects made from a material are ARTEFACT (*"the
glass from this excavation"*, *"worked pieces of flint"*).

**PERIOD** — For a full date annotate only the year (*"12 December 2012"* →
*2012*). Include spanning words (*"between approx. 1700 and 1850"*, *"from 300 to
100 BC"*) and complete periods (*"Late Medieval"*, *"last quarter of the 15th
century"*). Strip leading *after / before / circa / around / approximately*
(annotate only *"12th century"* from *"before the 12th century"*). Include *time*
/ *period* where part of the name (*"Carolingian period"*). Include years in
references (*"Van As 2010"*). Do **not** annotate years in codes (*"Rapport
2009-61 ARC"*) or relative periods (*"later periods"*).

**LOCATION** — For municipalities annotate only the place name (*"Municipality
Zutphen"* → *Zutphen*). Annotate full addresses. Do **not** annotate directions
(*"Northern France"*) unless part of the name (*"North Holland"*), place
adjectives (*"Zutphen castle"*, *"German monastery"*), rivers/seas/lakes,
coordinates, or province abbreviations (*"NH"*). If *at/in* links two places,
annotate the whole (*"Kastanjestraat in Hoorn"*). Named institutions are
LOCATION (*"University of Amsterdam"*).

**CONTEXT** — Human-made features (*"rampart"*, *"postholes"*, *"ditch"*), even
when empty. Do **not** annotate natural contexts (*"peat layer"*, *"dune"*),
ground types (*"sand"*, *"clay"*), modern contexts (*"office basement"*), or
over-general ones (*"buildings"*, *"traces"*). Drop adjectives (*"shallow
postholes"* → *postholes*).

**MATERIAL** — Only when it is the material *of an artefact* (*"flint axe"*,
*"arrowhead made of bone"*). Architectural (*"brick wall"*) and modern
(*"concrete garage"*) materials count. Drop modifying adjectives (*"molten
bronze"* → *bronze*, *"oak wood"* → *wood*).

**SPECIES** — Include humans as a species. *"human bones"* → *bones* = ARTEFACT,
*human* = SPECIES. Do **not** annotate *"the human"* / *"human influence"*. A
Latin binomial followed by a colloquial name is annotated as two separate spans.

## General rules

- Do not annotate articles unless they fall inside a span (*"de bijl"* → *bijl*;
  but *"Eerste helft van de 19e eeuw"* is annotated whole).
- Parenthetical parts belonging to the entity are included (*"wal(len)"*).
- With *or* listing alternatives, annotate each separately.
- Do not annotate quantity words (*"some graves"*, *"a few scrapers"*).

## Nesting (v1 = flat)

v1 is flat: give each token one type. Where a genuine overlap exists (e.g.
*bronze* as both MATERIAL and ARTEFACT), record it only if the doccano project has
"Allow overlapping" enabled — the exporter will emit it as `B-MATERIAL|B-ARTEFACT`
for a future seq2seq v2. Otherwise pick the most specific single type.
