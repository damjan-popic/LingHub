# LingHub local Core activation candidate

This isolated branch extends Part A commit
`1cb777c37eb60638d6c826719d3d5d08e1d77d57`. Canonical LingHub main, its legacy
`app.py` and router modules, and the original Part A worktree are unchanged.
Deployment uses the resulting committed SHA and a fixed snapshot; this is not a
merge or source-system parity claim.

Run `candidate_app:app` with the existing dedicated LingHub Python and exactly
one Uvicorn worker, loopback binding and no reload. Resource environment variables
are `CJVT_SYNONYMS_RELEASE`, `CJVT_COLLOCATIONS_RELEASE`, and
`CJVT_MORPHOLOGY_RELEASE`, with the exact verified Part A versioned directories.
Set `MY_LEMMATIZER_URL=http://127.0.0.1:8001` and
`LINGHUB_DEPLOYMENT_SCOPE=local_persistent` for the new persistent deployment.
Temporary tests use ports 18010/18001 and default scope `candidate`. No
`LINGHUB_CONTEXTUAL_NLP` flag exists.

All dictionary lookups continue through read-only immutable SQLite. No runtime
source XML loader is imported. Both dictionary fingerprints and the shared
morphology fingerprint are pinned; database content and evidence validation in
the compiled readers run before startup. All tracked resources receive a file
identity check before requests, failing 503 if changed or missing. The startup
scope requires a configured lemmatizer for persistent Core, and overall `/health`
actively verifies the upstream `/health` identity. Dictionary routes and
`/v1/resources/health` still work during a lemmatizer outage. Nested resource
statuses describe the retained artifact build validation; top-level
`deployment_scope`/`operational_activation` describe this process's configuration.

The pure shared morphology client imports `klikai_api.services.morphology` and
the KLIKai schema package. Deployment must pin these committed dependency files
alongside the release rather than depend on a mutable application checkout.
Use the accepted KLIKai commit `d71ac7f980b68bdf81cb1770e9ddf9ad4da50f85`;
do not alter or start its application, settings or database modules.

## Existing contracts enabled through the compiled candidate

- `/nlp/analyze`, `/nlp/analyze-full`, `/nlp/loris-check` preserve the current
  my_lemmatizer token/spans/issues shapes; `/nlp/lemmas` preserves the legacy
  detailed tokens and convenience lemma array. The complete original text is
  forwarded, including leading/trailing spaces, and all returned token/span
  coordinates are checked against it. No legacy fallback fabricates empty spans.
- `/collocations/from-text` preserves the exploratory content-lemma lookup shape.
  `/collocations/phrase` uses surface or ordered lemma-sequence matches among the
  bounded returned top records, preserving occurrence IDs, sense IDs, component
  order, source status, nullable measures and full provenance. It does not dedupe
  source occurrences or claim an absent top-result match means an invalid phrase.
- Dictionary-only routes and their original exact/normalized lookup semantics
  remain as described in the Part A guide. Sense-aware thesaurus and collocation
  source identities stay separate. No KLIKai synonym expansion is added.

Deliberate validation changes apply to the new Core entry point: NLP accepts
exact language codes `sl`/`it`, at most 10,000 characters and 4,096 response tokens.
Composition is Slovene-only because both CJVT dictionaries are Slovene. Composition
accepts at most 20 requested lemmas (default five), 500 records per lemma, and at
most 2,000 returned records in aggregate; larger products fail explicitly. Phrase
requests now expose `max_lemmas` and truncation flags. `min_freq=null` is additive
and retains source manual records with missing frequencies. Numeric logDice
must be finite; no source values are invented. Unsupported/oversized requests
return 422, blank text or an oversized result budget 400, unavailable/invalid
upstream 502, and unconfigured upstream 503.

The HTTP client accepts an explicit literal `127.0.0.1` HTTP origin with a port,
does not follow redirects or environment proxy settings, has a ten-second total
deadline and eight-MiB response cap, and sanitizes upstream error messages.
There is no network download or analyzer/model switching in this candidate.

General Sloleks/Morph-it endpoints are **API_INTEGRATION_PENDING**. The existing
`/v1/resources/lookup-inflected` joins at most four lexical candidates to Slovene
dictionaries; it is not contextual spaCy analysis or a new general morphology API.
CLASSLA Part B is NOT_STARTED and DigitalOcean parity is PENDING.

## Verification commands

From the fixed release with `PYTHONPATH` including it and its fixed KLIKai helper
snapshot, use `/data/envs/lingstack/linghub/bin/python` for all commands:

```text
python -B -m unittest discover -s tests -v
python -B tools/probe_candidate_http.py --port 8010 --pid PID --report RESOURCE_REPORT --expect-local-persistent
python -B tools/probe_core_http.py --port 8010 --report CORE_REPORT
```

The first suite has the original 28 tests and 11 activation regressions. The
resource HTTP harness has 28 functional checks and 150 measured warm calls;
the Core HTTP harness has 22 checks covering Slovene/Italian lemmas, original
offsets, five actual rule examples, no hits, lemma-sequence phrase composition,
text lookup and input limits. For the temporary candidate omit
`--expect-local-persistent`. A separate temporary instance configured to an unused
local endpoint can be checked with `tools/probe_core_http.py --port 18011 --report
REPORT --expect-missing-lemmatizer`: it expects ten checks proving bounded errors
and continued dictionary availability. Never break a persistent dependency for
that negative test.

Actual phrase fixture: `  šolskim otrokom  `, `headword=otrok`, `status=manual`,
`min_freq=null`, `limit=500`, matches source lexical-unit ID `12250092`, sense
`16268462`, internal occurrence 1 via `lemma_sequence` at rank 86. It is outside
default top 50, illustrating why a bounded miss is not evidence of invalidity.
See infrastructure `reports/linghub-core-candidate-review-20260909.json` for the
exact source/model-backed results and hashes. These are local baseline tests,
not a DigitalOcean comparison.
