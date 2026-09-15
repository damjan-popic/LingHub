# Ordinary question and retained matches

The normal composer contains one question field and submit action. It uses the
existing `reference` task and Qwen provider selection. Model, source-specific
lookup tools and language overrides remain under **More options**. The deployed
runtime enables unified retrieval; users do not select an extra generation mode.

The browser uses the existing family/job routes and displays their public
`progress.stage` as retrieval, generation or final validation. This is status
polling, not streaming; no provisional answer or internal reasoning is shown.
An owned frozen capture exposes `matches_available` and the existing family’s
`matches?offset=…&limit=20` reader. Results are grouped by source; filtering applies
to the loaded results. **Load more returned matches** follows `next_offset` within
that capture. Displayed counts describe returned matches, not corpus totals.
The first page and its original drawers are available while generation runs.
Already loaded pages and the source filter remain in place when the answer
finishes. The list is not retrieved again simply to update citation usage.
A failed initial list request gets one further attempt when generation settles;
already loaded pages are preserved.

During an unfinished run, match usage is labelled undecided (`data-used=pending`);
the matches API reports `usage_final: false`. Only a finalized answer's evidence
marks a match as used. Generated claim citations use
the existing resolved evidence registry; unknown markers remain ordinary text.
Both cited evidence and other matches open the existing owned source drawer.
An uncited match is not given an answer-source feedback control or presented as
support for a generated claim. The browser renders text through DOM text nodes.

Coverage timeouts, unavailable sources and retrieval limits remain visible.
Provider failure retains matches and explicitly labels any excerpt fallback as
not generated. No supporting evidence has a separate notice. Provider names and
device labels come from the actual public generation/execution metadata.

Cancellation uses the existing owned job route. A queued job is removed; a
claimed job observes cancellation before the next stage, so cancellation during
retrieval prevents generation. An already running synchronous provider call
cannot be interrupted individually: its bounded job slot stays occupied until
the call returns, and its answer is discarded. The UI reports cancellation as
requested during that interval instead of claiming the backend has stopped.
It never terminates the shared model process. Job and run finish as cancelled,
including when the interrupted operation returns an error. Any completed frozen
capture remains available to its owner, including original drawers.
The same holds after a generation or answer-validation failure; it does not
grant access to an unknown handle or another account's capture.

Private result timings distinguish `first_returned_matches_ready_ms` (from job
creation to the server's frozen matches) from the final validated response and
browser delivery. This is not first-token or screen-paint latency. The latter
requires the browser acceptance observation. Existing per-account/model queue
limits continue to apply to cancelled-but-still-running provider work.

`test_pilot_ui_unified.mjs` covers the presentation contracts.
`test_pilot_ui_unified_browser.cjs` loads the real UI assets under Playwright
request interception and exercises generated, partial and failed-provider
fixtures, early original drawers and pagination, cancellation, citations, filtering, safe rendering,
optional tools, a narrow viewport and logout clearing. It opens no listener and
contacts no service or model. These isolated fixtures do not establish live
generated-answer or backend ownership acceptance.
`test_pilot_job_progress.py` exercises the real authenticated API, owned store and
job lifecycle with blocked synthetic operations, including cross-user denial,
pre-generation cancellation and another user's queued job continuing safely.
