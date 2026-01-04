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

Returns only lemmas, in order.

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

Returns collocations for a single lemma.

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
  "input": "shakespeareova tragedija",
  "lemmas": ["shakespearov", "tragedija"],
  "matches": [
    {
      "phrase": "shakespearova tragedija",
      "frequency": 364,
      "rank": 1
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
  "lemmas": ["shakespearov", "tragedija"],
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