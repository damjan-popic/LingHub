# Source-bound frequency position

This optional presentation feature compares **Sloleks 3.1 source lexeme
occurrences with a recorded Gigafida 2.0 head frequency**. It does not compare
all Slovene words or the complete vocabulary of Gigafida. Sloleks is a lexicon
assembled from several sources; its 7,001-entry spoken-language extension does
not supply this head-frequency measurement.

The retained official README defines head frequency as the lemma together with
its inherent lexeme features, such as grammatical type and gender. The current
lexical card displays that measurement. A form/readings count, a collocation
count, logDice and a different corpus are separate units and populations.

## Provenance and one-time build

The local official source and its README are retained at:

```text
/data/datasets/raw/lingstack/morphology/sloleks/3.1/e53727a06b7b3a19b53637475bb111476a6746fe31b6bc8378a497d933c2398c/Sloleks.3.1.zip
Sloleks.3.1/00README.txt
```

The source catalogue is <https://hdl.handle.net/11356/2080>. The extracted
README is also beside the rich profile index as `SOURCE-README.txt`. See its
“LEMMA FREQUENCY” and “FORM REPRESENTATIONS” sections. The retained acquisition
manifest records the archive digest and verification. No corpus token total is
assumed, so this feature makes no per-million or percentage-of-tokens claim.

The build reads every row in the existing complete rich-profile inventory once,
after validating its pinned database digest. It never modifies that index:

```sh
/data/envs/lingstack/pilot/bin/python -B tools/build_frequency_position.py \
  --source /data/datasets/processed/lingstack/morphology/profiles/sloleks-3.1-20261017-v2 \
  --output /data/services/lingstack/resources/frequency-position/sloleks-3.1-gigafida-2.0-v1
```

The output path must be new. Its manifest pins the source archive, profile
database, profile manifest, build recipe, derived SQLite checksum, population
counts and source-shard counts. Files are published read-only only after the
complete input count and derived index checks pass. Runtime data, manifests,
credentials and backups stay outside Git; Git contains only implementation,
tests and these instructions.

The measured v1 population is 372,341 source records: 365,340 eligible,
including 7,875 genuine zeroes; 7,001 missing; zero invalid or conflicting
measurements. Both MANUAL and AUTOMATIC source records remain eligible. These
are source preparation labels, not normative recommendations.

## Identity and statistics

An inventory member is identified by the existing source occurrence key:
resource, edition, archive digest, source shard and entry ordinal. Publisher IDs
and lemma strings alone are insufficient: homographs can reuse an ID. They are
neither summed nor collapsed. Multiple identical observations within one
source occurrence give one value; contradictory values remain unranked.

For a frequency `f`, let `L`, `E` and `G` be eligible records with lower, equal
and greater values, and let `N=L+E+G`.

- Descending rank is the tied interval `G+1` through `G+E`; rank 1 is highest.
- The empirical percentile block is `(100*L/N, 100*(L+E)/N]`.
- Its displayed midpoint is `100*(L+E/2)/N`; higher means more frequent.
- Quartiles cover `(0,25]`, `(25,50]`, `(50,75]` and `(75,100]`. A tied block
  retains every quartile it touches, rather than assigning equal frequencies
  to arbitrary exclusive bins. Small histogram bins are stored in the manifest.

Zero is numeric and ranked. An absent, invalid, conflicting or incompatible
measurement has no position. The original count or missing-frequency message
is retained. Frequency is not a correctness, register or preference judgment.

## Runtime configuration

The optional host config key is the derived **manifest path**, not a source
database or model selection:

```json
{
  "frequency_position_reference": "/data/services/lingstack/resources/frequency-position/sloleks-3.1-gigafida-2.0-v1/manifest.json"
}
```

`Reference(Path(manifest_path))` checks the derived SQLite digest once at app
startup. It then uses indexed read-only queries with a bounded row cache.
`annotate(view, bundle, reference)` returns an additive copy of the view. It
requires the exact source occurrence, archive/index identity, corpus, lexeme
scope, occurrence unit and source value to agree with the inventory. Missing
reference or incompatible data leaves the existing presentation intact.

The app constructs the optional reference once; source/profile imports,
inventory scans, corpus queries and model calls are absent from request-time
annotation. The bundle, evidence, canonical answer and citation bytes are not
changed. This presentation resource does not select or change the writer,
embedding, reranker, tokenizer, context or source retrieval policy. Its host
path is recorded separately from the portable statistical definition.
