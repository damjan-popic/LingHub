# CPU pilot capability and clarification contract

The authenticated site and its saved records remain usable when model endpoints are absent. Deployment readiness and model readiness are distinct.

`GET /api/pilot/capabilities` requires a session. Existing `tasks`, `models` and `comparisons` fields describe configured choices. `generation.models.qwen.available` and `generation.models.gemma.available` report the bounded private gateway observation. `dense.available` describes dense retrieval readiness. `tools.tasks` reports separate terminology, morphology and text-check health observations. A configured URL alone does not establish availability. Public capability responses exclude model revisions, private URLs and credentials.

For a new generated reference request, `POST /api/pilot/families` returns HTTP 503 with error code `generation_waiting_capacity` before creating a family or queue entry when its selected model is unavailable. Expansion and comparison apply the same admission check. Workers recheck immediately before beginning work. Existing idempotent responses, saved answers, source openings and ownership checks remain usable when capacity changes. This is an observation and admission guard, not a GPU reservation.

Missing reference-tool focus or input language returns HTTP 200 with `status: clarification_required` and `clarification: {code, fields}`. It creates no family, run, job, delivery or feedback. `focus` is an optional explicit request field; contributors need not rewrite a question with special quote syntax. A supplied focus is recorded in the immutable `family_focus` child table and cascades with its parent on authorized deletion. Existing family rows and frozen bundles are unchanged. For backward compatibility, a text-check request without input language retains HTTP 400 `input_language_required`; the browser presents it as the same inline continuation.

The composer preserves the question, explicit focus and independent input/output languages during continuation. Editing the question clears the old focus so it cannot silently apply to a different request. Clarification and capacity states have no answer rating, expansion or comparison controls. Previously saved clarification records remain readable and are presented inline; new linguistic ratings or comparisons against those records are rejected. Feedback targets receive server-derived `capability` and `answer_method` fields. New analysis separates methods and excludes retained non-answer feedback from adaptation eligibility without modifying historical feedback or cycle reports.

CPU modes are `terminology`, `source_search`, `text_check` and `morphology`, with additive development prose presets `jezikovna_urgenca` and `jezikovna_posta`. Each prose preset requires explicit inclusion in the configured `tasks` and a `klikai_url`; adding code does not enable it in the deployed pilot. These source choices use the existing selector and family flow. Morphology uses the existing `/v1/morphology/lemmatize` API, captures its resource fingerprint and exact bounded lexical candidates, and renders them without contextual disambiguation or generated prose. Its input requires an explicit `input_language` (`sl` or `it`) and at most 32 whitespace-separated forms. Its original source capture can be reopened while models are absent.

The five Luka terminology collections remain separate from the two prose/Q&A collections. A prose preset requests only its own source from accepted KLIKai `/api/search`, passing `q`, the fixed source slug in `sources`, and `limit=3`. It displays attributed search excerpts, invokes no model and preserves the normal owned frozen capture/drawer. No-hit and upstream failure remain explicit rather than falling back to generation or unrelated sources. Existing ZRC source search keeps its current route.

Prose availability means bounded search is available; it does not mean a full editorial Q&A reader exists. The current accepted API lacks full question/answer context, source date and a detail route. A Pošta title must not be treated as its original question, and excerpts must not be presented as complete advice. The two new prose excerpt cases passed on 2026-09-14 with two accepted searches, zero detail requests and zero model calls; the five-source terminology matrix also passed once as a regression. The earlier terminology acceptance checkpoint remains separate. Full Q&A acceptance remains blocked by the upstream contract. Neither this development change nor its tests deploys or changes the running pilot.

Example morphology family payload:

```json
{"question":"delibere","task":"morphology","input_language":"it","locale":"sl","model":"qwen","idempotency_key":"external-morphology-example-1"}
```

The configured model field is retained for compatibility; a morphology job always runs in the CPU pool and has answer method `deterministic`.

The existing adaptation replay returns `DEFERRED_CAPACITY`, zero model requests and `baseline_kept: true` when a required model is unavailable. Analysis and backup remain separate CPU operations. Eligibility thresholds, independent review and canary gates are unchanged.
