# CJVT resource candidate on ALDHI

This branch adds streaming source converters, read-only SQLite adapters and an
opt-in dictionary API. Canonical `app.py` and contextual NLP are unchanged.
The authoritative acquisition/build/runtime evidence is linked from
`/data/projects/lingstack-infra/inventory/CJVT_RESOURCE_LOCK_20260909.json`.
No merge, service activation, resource pointer switch or CLASSLA adapter is included.

The candidate was tested using Python 3.12.13 in the existing dedicated LingHub
environment. Its only cross-project import is the pure read-only KLIKai morphology
service and schemas; it does not import KLIKai settings, secrets or database code.
The existing morphology fingerprint is pinned in `candidate_app.py`.

## Explicit local launch

Check that `127.0.0.1:18010` is free first. From this worktree:

```bash
env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH=/data/scratch/damjan/linghub-cjvt-candidate-20260909:/data/projects/KLIKai/apps/api \
  CJVT_SYNONYMS_RELEASE=/data/datasets/processed/lingstack/synonyms/releases/cjvt-synonyms-v1-de2195bde050500bce48de06 \
  CJVT_COLLOCATIONS_RELEASE=/data/datasets/processed/lingstack/collocations/releases/25fd5882cde3b326208050bb6bfbd3901895a83ac6633b209e081fde0bf8f63c \
  CJVT_MORPHOLOGY_RELEASE=/data/datasets/processed/lingstack/morphology/releases/lexicons-nfkc-lower-ws-v1-fa2fe5c58891375b9953ed86 \
  /data/envs/lingstack/linghub/bin/python -B -m uvicorn candidate_app:app \
  --host 127.0.0.1 --port 18010 --workers 1 --no-access-log
```

Use the PID of that owned process with `tools/probe_candidate_http.py --port 18010
--pid PID --report NEW_REPORT_PATH`. Stop it with Ctrl-C or SIGTERM when finished.
The completion pass did this and confirmed the port closed. The existing
`linghub.service` is a separately gated legacy deployment and is not this launcher.

## Contracts and source semantics

- `/thesaurus/synonyms` preserves the legacy sense/core/near/antonym projection and
  exact case-sensitive lemma lookup. Legacy absent synonym grouping projects to
  `core`; this compatibility behavior is not a claim about the source grouping.
- `/v1/resources/thesaurus` returns actual lexical-unit/sense identities, directed
  ordered relations, raw score strings, status/definitions and unresolved references.
  Missing source grouping remains null. Normalized lookup is internal to the
  explicitly versioned inflected route; no transitive synonym expansion occurs.
- `/collocations` and `/collocations/by-lemma` preserve the legacy filters and
  collocation fields, adding source evidence. Their default `min_freq=1` excludes
  missing frequencies. `/v1/resources/collocations` defaults to no frequency filter
  and retains those manual records. Numeric filters must be finite; limits are bounded.
- Collocations preserve each source occurrence, including repeated lexical-unit IDs.
  No source collocation `@id` exists here: `collocation_id` is null and the local
  `occurrence_id` is documented as internal, with shard/entry/sense/example positions.
  Components follow XML order; `@num` is retained separately. Inferred component
  roles carry `role_origin=lemma_comparison`. Structure IDs are not UD labels.
- `/v1/resources/lookup-inflected` accepts one Slovene/Italian token, uses the existing
  Sloleks/Morph-it release, and returns at most four lexical lemma candidates.
  Slovene lemmas retrieve dictionary evidence; Italian candidates stay separate.
  This endpoint does not perform contextual model analysis or join incompatible
  sense IDs across dictionaries.
- `/health` and `/v1/resources/health` report verified resource identities and
  explicitly inactive contextual NLP. Every resource endpoint returns 503 if a
  tracked release file changes/disappears after startup. Database hashes are checked
  at startup, with per-request file identity checks and read-only SQLite connections.

Legacy phrase/from-text composition and LORIS/contextual NLP remain outside this
entry point. No claims are made for richer Sloleks metadata beyond the existing
runtime projection. Query readiness does not establish DigitalOcean parity.

## Reproducibility and validation

Raw sources retain original archive/XML/XSD names under versioned raw releases.
`tools/compile_synonyms.py` takes `--xml`, `--provenance` (acquisition manifest), and
`--output`. `tools/build_cjvt_collocations.py` takes `--raw-release`, `--output`, and
an explicit `--apply`; default is a plan. Outputs must be new private build locations.
Already validated releases do not need rebuilding. Do not run either builder on
the operational morphology artifacts.

The shared XML reader rejects DTD/entity declarations, NUL/UTF-16 and invalid UTF-8.
The optional `tools/validate_cjvt_xml.py` uses existing lxml in a separate process,
original XSDs and an explicitly pinned local copy of the official W3C XML namespace
schema; it disables network resolution. No shared environment installation is needed.

Each compiled release has a manifest, SQLite file, QA summary, notices and checksums.
Schema, row accounting, IDs, integrity, query plans, provenance and source-derived
fixtures were checked. Different resources retain explicitly named normalization
policies: synonyms `nfkc-casefold-whitespace-v1`; collocations `strip-casefold-v1`;
existing morphology `nfkc-lower-ws-v1`.

Run the branch's 28 fixture/API/concurrency tests with:

```bash
/data/envs/lingstack/linghub/bin/python -B -m unittest discover -s tests -v
```

Real HTTP evidence also covers source-derived ambiguity/senses/core/near/antonyms,
manual/automatic collocations, preserved missing values and IDs, `otrokom`/`Jemnu`
inflected joins, Italian separation, no-hit and malformed request behavior. The
private candidate server loads SQLite, not the complete raw XML in each worker.

All source/catalogue discrepancies, model inventory, performance measurements and
remaining deployment gates are in the infrastructure Part A handback. Source data
and SQLite databases are external artifacts and must not be committed to this repo.
