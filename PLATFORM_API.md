# Reusable local LingHub platform

The versioned API uses the adopted CJVT and morphology SQLite releases. It does
not rebuild lexicons, import KLIKai database/settings modules, perform synonym
expansion in terminology retrieval, or select CLASSLA as the default. Source
IDs and licences remain separate for each resource. DigitalOcean parity and
analyzer default-selection review remain pending.

All requests/responses use UTF-8 JSON. Offsets are **Python Unicode code points,
half-open, into the unchanged original text**. JavaScript callers must convert
UTF-16 positions explicitly. Contextual lemma joins identify lexical candidates,
not resolved dictionary senses. Empty dictionary results do not mean invalid
language or a failed analyzer. No automatic rewrite or best-synonym decision is
performed.

| Route | Input | Output |
| --- | --- | --- |
| `GET /v1/resources` | None | Sources, versions, licences, opaque fingerprints, presence/validation/usability, model catalogue and privacy limits |
| `GET /v1/capabilities` | None | Same catalogue plus engine/default status and stable capability `identity` for client resume |
| `POST /v1/analyze` | `lang`, `text`, `engine` (`default`, `spacy`, `classla`), optional `include_loris` | Rich native sentence/token/word analysis, legacy token/span/issue projection, model/rule provenance |
| `POST /v1/morphology/lemmatize` | `lang`, `tokens`, optional `limit` | Ordered per-input status/candidates, exact-before-folded matching, ambiguity/count/truncation and native MSD/POS evidence |
| `POST /v1/morphology/inflect` | `lang`, `lemma`, optional `limit`, exact native `msd` | Forms with native MSD/POS evidence; no contextual form selection |
| `POST /v1/morphology/signatures` | `lang`, `text`, optional `limit` | Accepted deterministic lexical signatures, protected-token details and original normalization identity |
| `POST /v1/explore` | `lang`, `text`, optional `selection:{start,end}`, `engine`, `include`, `limit` | Independent `results.analysis/loris/morphology/synonyms/collocations` with `status`, `data`, `provenance`, `truncated` |

Exploration runs one analysis for the whole original input and shares it among
requested tools. A selected surface still supports lexical/dictionary lookup
when an analyzer is unavailable. Missing analyzers return `unavailable`, Italian
CJVT/CLASSLA requests report `unsupported`, and absent dictionary hits report
`no_hit`. The response is `partial` when requested components are unavailable.
No explicit engine silently falls back to another engine.

If selection is omitted, contextual analysis covers the full text while lexical
tools use at most its first 512 code points, with the literal selection and
truncation explicitly reported. Explicit selections are bounded to 512 code
points. Up to eight deduplicated lemma candidates are queried, preserving sense
groups and source relation grouping/direction. Returned thesaurus senses are
namespaced `cjvt-thesaurus:2.2:...`; KSSS senses use `cjvt-ksss:2.2:...`. These are
different namespaces and are not a cross-dictionary sense map.

The existing dictionary contracts remain available. The versioned thesaurus GET
adds optional `limit=1..20`, retains exact case-sensitive lookup, and reports
truncation separately from source totals. The versioned collocation GET adds
`kind=collocation|grammaticalCombination|all` while its default remains collocation.
The compiled source contains 4,425,942 collocations and 2,109 separate grammatical
combinations; they are never relabelled or discarded. Every occurrence retains
source status, IDs, nullable measures, component order and provenance. Source
status does not establish universal manual approval.

Bounds: 10,000 text characters; 64 morphology tokens of 128 characters; four lemma
candidates; 100 forms; 32 lexical signatures over at most 32 tokens/512 characters;
eight exploration dictionary lemmas; 20 requested dictionary records per lemma;
20 thesaurus senses and 100 relations per sense in bounded composition; 128 KiB
request bodies; two MiB composed responses. Analyzer calls have a 30-second total
deadline. New routes are capability-specific during dependency failure; legacy
required-resource failure tests remain unchanged. Deep checks occur at startup,
with lightweight file-identity checks on requests. Host metadata/private paths
are not exposed by normal catalogue responses.

The functioning pure morphology code is retained byte-for-byte under
`_dependencies/apps/api`; its accepted origin and hashes are recorded in
`_dependencies/morphology-code-lock.json`. There is no explicit upstream repository
code licence in the accepted KLIKai checkout: the notice records authorized
local reuse, not an invented public redistribution licence. Resource data licences
are independent. The Italian spaCy model's CC BY-NC-SA 3.0 remains visible in
the live model catalogue; this is not an unrestricted public/commercial release.

## Python and CLI

The client is the standard-library module `linghub_client.py`: no model or SQLite
imports occur. From this project or with this directory on `PYTHONPATH`:

```python
from linghub_client import LingHubClient
client = LingHubClient("http://127.0.0.1:8010")
analysis = client.analyze("sl", "Ana bere knjige.", engine="classla")
evidence = client.explore("sl", "Na občnem zboru.", {"start": 3, "end": 15})
forms = client.inflect("sl", "zbor", limit=20, msd="Somer")
```

Executable examples from the fixed release directory using the dedicated Python:

```text
/data/envs/lingstack/linghub/bin/python -B linghub_client.py analyze 'Ana bere knjige.' --lang sl --engine classla
/data/envs/lingstack/linghub/bin/python -B linghub_client.py explore 'Na občnem zboru.' --lang sl --start 3 --end 15
/data/envs/lingstack/linghub/bin/python -B linghub_client.py lemmatize 'občnega zbora' --lang sl
/data/envs/lingstack/linghub/bin/python -B linghub_client.py inflect zbor --lang sl --limit 20 --msd Somer
/data/envs/lingstack/linghub/bin/python -B linghub_client.py synonyms otrok
/data/envs/lingstack/linghub/bin/python -B linghub_client.py collocations otrok --limit 5
```

Set the global `--base-url http://127.0.0.1:18010` before the task for a temporary
candidate. Only local HTTP origins are allowed; redirects and environment proxies
are disabled. Read-only calls retry 429/502/503/504 and transport failures at most
twice, with 0.2/0.4-second backoff and a 35-second request timeout.

JSONL input records require unique string `id`, `lang`, `text`, explicit `task` and
`engine`; they may add `selection`, `limit`, `tokens`, `msd` or `include`:

```json
{"id":"public-001","lang":"sl","text":"Na občnem zboru.","task":"explore","engine":"default","selection":{"start":3,"end":15}}
```

Choose a new output directory inside your private scratch area; the parent must
already exist. The client creates the output and item directories as 0700 and
files as 0600. Existing shared/symlink output directories are rejected.

```text
/data/envs/lingstack/linghub/bin/python -B linghub_client.py batch /data/scratch/damjan/my-input.jsonl /data/scratch/damjan/my-private-export
/data/envs/lingstack/linghub/bin/python -B linghub_client.py batch /data/scratch/damjan/my-input.jsonl /data/scratch/damjan/my-private-export --resume
```

This emits `results.jsonl`, `progress.json`, and private per-item recovery files.
Each result is flushed and atomically published. SIGINT preserves completed items;
resuming unchanged input reuses results without duplicated IDs. Keys include the
complete input, task, engine and capability identity. Changed input/engine/resource
identities cause recomputation, not stale reuse. Resource identity is rechecked
after newly executed items; errors are not cached as successful completions.
Failed document IDs are explicit and the CLI exits nonzero. The progress manifest
contains hashes/IDs/status, not raw text; result exports contain only the
operator-selected input and outputs. No telemetry or automatic corpus collection
occurs. Batch bounds are 500 documents, eight MiB input, 64 KiB lines, two MiB per
result and 64 MiB aggregate output. Over-limit text is rejected, never silently
chunked with fabricated offsets.

## Verification and temporary deployment

Keep existing `CJVT_SYNONYMS_RELEASE`, `CJVT_COLLOCATIONS_RELEASE`,
`CJVT_MORPHOLOGY_RELEASE` exact paths; configure `MY_LEMMATIZER_URL` to the new
versioned local analyzer and `LINGHUB_DEPLOYMENT_SCOPE=local_persistent`. The
workbench additionally uses root-owned `KLIKAI_URL` configuration. No environment
installations or resource moves are required by these API/client changes.

Run `python -B -m unittest discover -s tests -v`, then
`python -B tools/probe_platform_http.py --port PORT --report PRIVATE_REPORT`.
The latter tests live Slovene/Italian morphology, native filters, namespaces,
grammatical combinations, original offsets and both engines. Its
`--missing-analyzer` variant targets a separate temporary candidate configured to
an unused loopback upstream; never break a persistent service for that test.
Existing `tools/probe_core_http.py` and the corrected infrastructure resource
probe remain the legacy regression gates.
