# LingHub lexical profiles

The lexical implementation lives in `core/lexical_profile.py`, with target planning in
`core/lexical_planner.py`, analysis policy in `core/analysis_profiles.py`, and the
source-faithful Sloleks reader/compiler in `core/sloleks_profiles.py` and
`tools/build_sloleks_profiles.py`. The existing `candidate_app:app` entry point enables
it only when `LINGHUB_SLOLEKS_PROFILE_RELEASE` is explicitly configured.

## Profiles and isolation

`klikai_experimental_v1` and `linghub_lexical_stable_v1` initially resolve the same
2026-10-17 implementation. Every response identifies the actual immutable code
release, resource checksums and analysis policy revision. The stable external URL
is separately configurable as `linghub_stable_url`; future experimental releases
must change only `linghub_url` until a stable promotion is authorized.

Short Slovene inputs use CLASSLA 2.2.3 / standard Slovene 2.0 as contextual primary,
and spaCy 3.8.16 / sl_core_news_lg 3.8.0 independently. Both raw analyses and
character-span disagreement are retained. Agreement is not linguistic proof.
Above 512 characters, the declared policy uses CLASSLA alone; Italian uses spaCy.
Unavailability is explicit and does not stop independent indexed lexical results.
The existing production LingHub, spaCy default, CLASSLA worker and LORIS rule files
are not replaced by this opt-in deployment. No model selector is added to KLIKai.

## Sources and meanings

- Sloleks 3.1: original lexeme IDs, grammatical slots, every slot variant, pattern
  memberships, original measurements and source status. Lexeme frequencies and
  surface/readings remain separate; zero is different from missing. No sums across
  homographs or repeated readings. Runtime reads compiled JSON, not XML.
- CJVT Thesaurus 2.2: separate entries/senses, directed core/near/antonym relations,
  original scores and status. An association score is not corpus frequency.
- KSSS 2.2: occurrence identity, original source identity, structural components,
  occurrence frequency and logDice remain separate. Phrase matching uses whole
  surface or an explicitly recorded analyzer lemma sequence; components are marked.
- LORIS: established original-context matcher and retained exact surface aliases.
  Its curated recommendation is never substituted for a statistical lemma.
- Existing terminology and documentary search are reused. Direct documentary
  results are discovery records, with rights/completeness intact; the KLIKai
  client additionally captures its authorized full original records.

The interface freezes deterministic facets and complete source units alongside
ordinary questions. Expanding paradigms or original-source drawers does not generate
another answer. Exact-case source candidates receive priority without deleting
alternative homographs. Normative claims still require appropriate source support.
A rich overview uses the bounded `lexical_overview` writer profile; simple
translations retain the short profile. All admitted evidence is fitted with native
tokenizers and the actual model context/output limits.

## Authenticated API

Public base: `https://klikai.jeziknaklik.it/api/linghub/v1`.
Local opt-in service after installation: `http://127.0.0.1:8011`.
The external route is available only after the application release is published.

Application keys are separate from CU/SLORI/TEHNO passwords. The installed registry
`/data/services/lingstack/config/linghub-clients.json` contains only secret hashes,
scopes, profile grants and limits. Revocation is read on every request. The initial
review credential is delivered once in the operator receipt's private file; never
commit it or paste it into browser JavaScript. Existing browser sessions are unchanged.

```sh
# Set LINGHUB_API_KEY privately from the delivered credentials file.
curl --fail --silent --show-error \
  -H "Authorization: Bearer $LINGHUB_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"text":"brez Luke ali brez Luka","lang":"sl","profile":"stable"}' \
  https://klikai.jeziknaklik.it/api/linghub/v1/profile
```

```python
import os, requests
base = "https://klikai.jeziknaklik.it/api/linghub/v1"
headers = {"Authorization": "Bearer " + os.environ["LINGHUB_API_KEY"]}
result = requests.post(base + "/profile", headers=headers,
    json={"text": "lepem dnevu", "profile": "stable"}, timeout=35)
result.raise_for_status()
profile = result.json()
```

`POST /analyze` accepts `engine: default`, `spacy`, `classla` or `compare`;
explicit comparison is bounded to 512 characters. `default` resolves the selected
client profile, while explicit engines retain their actual identity.

Authenticated `GET /openapi.json` documents the complete contract. Operations:
`GET /capabilities`, `/resources`, `/paradigm?surface=Luka`, `/synonyms?lemma=lep`,
`/collocations?lemma=lep`; `POST /analyze`, `/profile`, `/morphology/lemmatize`,
`/morphology/inflect`. Select `experimental` explicitly when granted. No arbitrary
URL, file or internal-model proxy exists. There is no unbounded batch endpoint;
callers must respect per-client request/concurrency limits and retry 429 responses.

External logs contain request ID, client, profile, operation, status and duration,
never bodies or keys. Exact-input analysis caching is scoped to the authenticated
client and expires after 60 seconds; unscoped internal analysis is not shared-cached.
Public lexical-entry cache keys are isolated by the immutable reader edition.
KLIKai retains its existing disclosed project testing diagnostics and ownership.

## Update and recovery

The initial prepared operator command installs `classla-lexical.service` and
`linghub-lexical.service`, enables both for reboot, qualifies the API/gateway successor
and checks four private ordinary-query answers. CPU analyzer setup is distinct from
the normal beta website publication. Existing GPU services are not restarted.
The existing tested beta workflow publishes the website after successful activation.

The NAS collector includes both new units, their pinned code, configuration,
application-key registry and compiled profile index. Existing raw source editions
are already in the versioned inventory. Environment locks are retained for rebuilding;
model weights are not added to each release archive. A successful installation is
not a claim that the later publication or NAS checkpoint has completed: inspect
its explicit receipts.
