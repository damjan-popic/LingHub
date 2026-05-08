LingHub — Language Resource Hub

LingHub is a FastAPI-based hub service that exposes linguistic intelligence as APIs for Slovene and Italian.
It is designed to serve rule-based checking (LORIS), lexical lookup, collocations, and future conversational interfaces (chatbots) — without forcing any single frontend or workflow.

Design principle:
LingHub exposes rich linguistic data.
Frontends (UIs, chatbots, plugins) decide how much of it they want to use.

Architecture overview (mental model)
                ┌───────────────┐
                │   Frontends   │
                │  (UI / Chat)  │
                └───────┬───────┘
                        │
                ┌───────▼───────┐
                │    LingHub    │
                │  (this repo)  │
                └───────┬───────┘
                        │
     ┌──────────────────┼──────────────────┐
     ▼                  ▼                  ▼
Lemmatizer        Thesaurus           Collocations
(spaCy)        (XML → RAM)        (XML shards → RAM)


LingHub itself does not lemmatize — it delegates that to a dedicated lemmatizer service and enriches the results.

Base URL
http://<host>:8010

Health check
GET /

Simple service heartbeat.

Response

{
  "status": "ok",
  "message": "LingHub is alive."
}

NLP endpoints (proxying the lemmatizer)

These endpoints delegate work to the lemmatizer service and return normalized linguistic structure.

POST /nlp/analyze

Full token-level linguistic analysis.

Intended use

Debugging

NLP pipelines

Downstream services that need offsets, POS, lemmas

Input

{
  "lang": "sl",
  "text": "Lev je moj sinček."
}


Output

[
  {
    "text": "Lev",
    "lemma": "Lev",
    "pos": "PROPN",
    "start": 0,
    "end": 3
  },
  {
    "text": "je",
    "lemma": "biti",
    "pos": "AUX",
    "start": 4,
    "end": 6
  }
]

POST /nlp/lemmas

Returns lightweight lemma data in two shapes: `tokens` for detailed clients and `lemmas` for simple clients.

Intended use

Fast lookup

Feeding collocations / thesaurus

Chatbot reasoning layer

Input

{
  "lang": "sl",
  "text": "Lev je moj sinček."
}


Output

{
  "tokens": [
    {"text": "Lev", "lemma": "Lev"},
    {"text": "je", "lemma": "biti"},
    {"text": "moj", "lemma": "moj"},
    {"text": "sinček", "lemma": "sinček"}
  ],
  "lemmas": ["Lev", "biti", "moj", "sinček"]
}

POST /nlp/loris-check

Runs LORIS rule-based checks on the text.

Intended use

Style warnings

Prescriptive guidance

Educational feedback

Input

{
  "lang": "sl",
  "text": "To je neustrezen izraz."
}


Output

{
  "tokens": [...],
  "issues": [
    {
      "rule_id": "LORIS_123",
      "category": "style",
      "start": 6,
      "end": 16,
      "priority": 2,
      "payload": {
        "suggestion": "primernejši izraz"
      }
    }
  ],
  "spans": [
    {
      "type": "multiword_rule",
      "rule_id": "slogovni_z otroci",
      "match_on": "surface",
      "trigger": "z otroci",
      "start": 0,
      "end": 8,
      "token_indexes": [0, 1],
      "surface": "z otroci"
    }
  ]
}

Thesaurus endpoints

The thesaurus is eager-loaded into RAM at startup from XML.

GET /thesaurus/synonyms

Returns synonym senses for a lemma.

Intended use

Lexical exploration

Writing assistance

Chatbot “what else could I say?”

Query

/thesaurus/synonyms?lemma=nepretrgan


Output

{
  "lemma": "nepretrgan",
  "senses": [
    {
      "sense_id": "1",
      "core": ["nekinjen"],
      "near": ["stalni"],
      "antonyms": ["prekinjen"]
    }
  ]
}

Collocations endpoints

Collocations are precomputed, stored in XML shards, and eager-loaded at startup.

Collocations are descriptive evidence, not hard rules.

GET /collocations/by-lemma
GET /collocations

Returns collocations for a single lemma. `/collocations/by-lemma` is the clearer documented alias; `/collocations` remains backward-compatible.

Intended use

Lexicography

Evidence display

Chatbot explanations (“people usually say…”)

Query

/collocations/by-lemma?lemma=shakespearov&limit=10


Output

{
  "lemma": "shakespearov",
  "collocations": [
    {
      "phrase": "shakespearova tragedija",
      "frequency": 364
    },
    {
      "phrase": "shakespearova komedija",
      "frequency": 192
    }
  ]
}


All matching is case-insensitive.

POST /collocations/phrase

Checks a specific phrase against known collocations.

This is the most chatbot-friendly endpoint.

Intended use

“Is this a good way of saying this?”

Idiomaticity checks

Writing assistance

Input

{
  "lang": "sl",
  "text": "Shakespeareova tragedija"
}


Output

{
  "input_text": "Shakespeareova tragedija",
  "lang": "sl",
  "tokens": [...],
  "phrase_surface_norm": "shakespeareova tragedija",
  "phrase_lemma_sequence": ["shakespearov", "tragedija"],
  "phrase_lemma_norm": "shakespearov tragedija",
  "matches": [
    {
      "surface": "shakespearova tragedija",
      "frequency": 364,
      "rank": 1,
      "match_type": "lemma_sequence",
      "matched_by": ["lemma_sequence"],
      "lexical_unit_id": 12345,
      "component_lemmas": ["shakespearov", "tragedija"]
    }
  ]
}


If no strong match exists, the response still returns near evidence, not an error.

POST /collocations/from-text

Extracts candidate lemmas from text and returns collocations for them.

Important:
This is not a full-text validator.

Intended use

Exploratory UI features

Chatbot enrichment

Future experimentation

Safeguards

Limited number of lemmas

Content words only

Cheap lookups

Input

{
  "lang": "sl",
  "text": "Prebral sem Shakespeareovo tragedijo."
}


Output

{
  "input_text": "Prebral sem Shakespeareovo tragedijo.",
  "lang": "sl",
  "tokens": [...],
  "lemmas_used": ["shakespearov", "tragedija"],
  "collocations": {
    "shakespearov": [...],
    "tragedija": [...]
  }
}

Design notes (important)

Everything is lowercase-normalized internally
(users, chatbots, and LLMs are messy — LingHub is forgiving)

LORIS ≠ collocations

LORIS → prescriptive

Collocations → descriptive

The chatbot is the reasoning layer
LingHub provides evidence, not judgments.

Eager loading is intentional
Predictable latency > lazy surprises.

What’s coming next (intentionally)

Lemma override JSON (LORIS-only)

Smarter multi-word handling

Chatbot tool bindings

Usage-aware ranking

MWU / multi-word-unit note

The LORIS path now has a span layer. `POST /nlp/loris-check` still returns `tokens` and `issues`, but also returns `spans` for multi-word rules and token-aware `surface_name` matches. Frontends that highlight text can keep using `issues[*].start` and `issues[*].end`; frontends that need phrase-level UI should read `spans[*].token_indexes`, `spans[*].match_on`, and `spans[*].trigger`.

The collocation phrase endpoint now checks both normalized surface form and lemma sequence. This means an inflected input phrase can match a stored collocation when the XML component lemmas match the input lemma sequence. See `MWU_CHANGES.md` for the Slovenian and English migration notes.

---

## MWU / span-layer update

See `MWU_FRONTEND_NOTES.md` for the full bilingual Slovene/English description of the multi-word-unit changes.

Important new/changed endpoints:

- `POST /nlp/analyze-full` returns `{tokens, spans}`.
- `POST /nlp/loris-check` still returns `{tokens, issues}`, but now also includes `spans`.
- `POST /nlp/lemmas` now returns both `tokens` and a plain `lemmas` list.
- `GET /collocations/by-lemma` is now available as an alias for `GET /collocations`.
- `POST /collocations/phrase` now matches both normalized surface forms and lemma sequences.

Frontend catch point for MWUs:

```js
const spans = response.spans ?? [];
```

Use `span.start` / `span.end` for text highlighting and `span.token_indexes` or `span.token_start` / `span.token_end` for token anchoring.
