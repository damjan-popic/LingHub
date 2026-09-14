# Ordinary question and retained matches

The normal composer contains one question field and submit action. It uses the
existing `reference` task and Qwen provider selection. Model, source-specific
lookup tools and language overrides remain under **More options**. The deployed
runtime enables unified retrieval; users do not select an extra generation mode.

The browser uses the existing family/job routes and displays their public
`progress.stage` as retrieval or generation. This is status polling, not streaming.
An owned frozen capture exposes `matches_available` and the existing family’s
`matches?offset=…&limit=20` reader. Results are grouped by source; filtering applies
to the loaded results. **Load more returned matches** follows `next_offset` within
that capture. Displayed counts describe returned matches, not corpus totals.

Only `used_in_answer: true` marks a match as used. Generated claim citations use
the existing resolved evidence registry; unknown markers remain ordinary text.
Both cited evidence and other matches open the existing owned source drawer.
An uncited match is not given an answer-source feedback control or presented as
support for a generated claim. The browser renders text through DOM text nodes.

Coverage timeouts, unavailable sources and retrieval limits remain visible.
Provider failure retains matches and explicitly labels any excerpt fallback as
not generated. No supporting evidence has a separate notice. Provider names and
device labels come from the actual public generation/execution metadata.

`test_pilot_ui_unified.mjs` covers the presentation contracts.
`test_pilot_ui_unified_browser.cjs` loads the real UI assets under Playwright
request interception and exercises generated, partial and failed-provider
fixtures, citations, original drawers, pagination, filtering, safe rendering,
optional tools, a narrow viewport and logout clearing. It opens no listener and
contacts no service or model. These isolated fixtures do not establish live
generated-answer or backend ownership acceptance.
