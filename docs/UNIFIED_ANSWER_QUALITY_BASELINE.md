# Unified answer-quality baseline v1

The JSON fixture `tests/fixtures/unified_answer_quality_baseline.v1.json` preserves **12 cases: 2 historical observations and 10 NOT_RUN**. No new model call or application change was made. It contains both complete saved generated answers verbatim, their hashes, citation identities, pinned source records and six separate review dimensions. Mechanical acceptance is not a linguistic-quality PASS.

| Case | Query | Scope | Observation |
|---|---|---|---|
| U01 | agriturismo | cross-source editorial | HISTORICAL_OBSERVATION |
| U02 | consiglio d'amministrazione | cross-source institutional terminology | HISTORICAL_OBSERVATION |
| U03 | učiteljskemu zboru | inflected Šolska terminology | NOT_RUN |
| U04 | Belopeška kotlina | exact geographic terminology | NOT_RUN |
| U05 | presoja krajinske skladnosti | domain-specific CrossTerm terminology | NOT_RUN |
| U06 | Kako naj prevedemo rifiuti da demolizione? | full natural-language editorial question | NOT_RUN |
| U07 | Kako pišemo imena dogodkov, prireditev in srečanj? | connected non-Luka answer bank / draft | NOT_RUN |
| U08 | Ali za referent v pomenu nanosnik velja kategorija živosti? | connected ZRC reference / uncertainty | NOT_RUN |
| U09 | Kako sklanjamo ime 24 ur Le Mansa? | ePravopis / proposal status | NOT_RUN |
| U10 | assemblea dei soci | institutional ambiguity / qualifications | NOT_RUN |
| U11 | Kako pišemo pitagorov izrek in parkinsonova bolezen? | historical source-reported discrepancy | NOT_RUN |
| U12 | zzqv_baseline_neizpričani_izraz_20260915 | weak/missing evidence limitation control | NOT_RUN |

## Historical observations

| Query | Retrieval | Generation | Browser total | Returned / uncited |
|---|---:|---:|---:|---:|
| agriturismo | 9.438 s | 194.054 s | 204.141 s | 2 / 0 |
| consiglio d'amministrazione | 9.733 s | 110.130 s | 120.802 s | 38 / 36 |

The two complete outputs come directly from `editorial-live-delivery-unified-20260914-r3.json`, not from reconstructed handback excerpts. The saved provider was **Qwen/Qwen3.8-27B**, revision `1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0`, transformers, CPU, bfloat16. These are two historical observations, not a performance distribution. Content was non-streaming; first-generated-content time was not observed.

For **agriturismo**, both retained editorial originals support the practical translation. The answer quotes a Pošta sentence about the preceding *azienda agricola* question next to the *agriturismo* recommendation, risking scope transfer. It omits the tourism-type/metaphorical-establishment distinction, repeats the recommendation and does not clearly attribute historical/current-law wording. These are review concerns, not a claim that the core translation is false.

For **consiglio d’amministrazione**, both cited originals support *upravni odbor*. “Raba termina upravni svet ni ustrezna” is defensible within the query’s scope but should not become a universal prohibition: Urgenca allows a separate advisory body and ZKB distinguishes one-/two-tier governance systems. The short answer omits these qualifications. Its Slovene is clear but terse.

The fixture separates retrieval relevance, correctness/completeness, citation support, qualifications, Slovene quality and latency. Comments are preliminary source-based review, not reviewer sign-off or a fabricated numerical score.

## Independent expectations

Luka identities were re-read from the retained normalized release and pinned by exact line, file, canonical record and payload hash. The extra cited agriturismo Urgenca record was independently checked against that retained source. The baseline does not claim all original XLSX/DOCX bytes exist. Retained source dates are not automatically publication dates, and capture hashes are not database-edition identities.

The answer-bank case pins the connected `answers.v1.jsonl` record **ODG-A1C4**, whose original status requires partner/editorial review. Do not confuse this active 133-record adapter with the wider document archive or treat a draft as approved advice.

ZRC source **13** requires preserving uncertainty about a dictionary omission. Source **10** explicitly discusses a historical SP2001 rule/dictionary divergence; this is not an invented disagreement between Luka sources or a fresh norm check in 2026. The ePravopis entry **24 ur Le Mansa** is explicitly **Predlog**, annual 2025 additions, not the cumulative dictionary or Pravopis 8 rules. Selected source revisions match both the retained and current configured fulltext manifest `8c4348c6…`, containing 3,578 passages. This was checked against existing configuration/manifests without a database query or live call; the checkpoint records the full active path.

U03–U05 keep prior source-specific lookup evidence separate from unexecuted default-route generated results. U12 is an artificial limitation control: no search was run to assert exhaustive absence, and irrelevant incidental hits must not become invented support.

## Caps and interpretation

Current LH caps are **20** per terminology source, **3** per Pošta/Urgenca source and **10** per answer-bank/other documentary source. Saved r3 reference coverage reports an upper bound of **38** per reference family. The owned matches API accepts 1–50 items; the UI loads 20. These pages enumerate **returned frozen matches**, not every matching record in the collections. `corpus_total` remains unknown, so the observed 38 results do not establish exhaustive recall.

The short signed evidence budget defaults to 10 passages (policy range 6–16) and 14,000 characters. The current CPU compact profile selects at most two complete source-revision groups and 4,096 prompt tokens; long uses three groups/6,144 tokens. All returned matches remain browsable, including those outside the model context. Short output targets 40–100 words, allows 120 visible words/four claims and 768 output tokens with reasoning disabled. The configured common context limit is 32,768 tokens; this does not expand the CPU compact-input limits. Long output targets 400–900 words with a 1,100-word visible cap and 4,096 output tokens. Configuration observations and code pins are included in the fixture.

## Later CPU/GPU comparison

After the next explicit generation authorization, reuse the existing normal signed-in UI or pilot harness. Submit unchanged queries through reference/default provider; capture actual provider/artifact, complete answer, citations, returned matches and separate timings. Review against the pinned originals and save observations in a new file. Preserve this baseline. The existing `unified-acceptance` phase is a fixed two-case operational smoke, not a twelve-case quality runner. Do not replay a deployment/update request to evaluate answers.

No credentials, accounts, family IDs, raw operational receipts or full source-dataset copies are embedded. The two selected generated outputs are retained in full, including their quotation, as authorized evaluation evidence. Source datasets and original operational evidence belong in the private checkpoint; this baseline does not change source-use policy.
