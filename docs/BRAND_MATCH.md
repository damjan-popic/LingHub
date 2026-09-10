# klikai pilot brand and interface evidence

The Slovene and Italian public [Jezik na klik](https://www.jeziknaklik.it/) portal pages were read on ALDHI on 10 September 2026 at 14:43 UTC using a fresh, ephemeral Chrome context. Both returned HTTP 200 with their real portal content; neither displayed an access challenge. The earlier failed observation script is retained separately and was not used as evidence.

The portal's named green identity is visually teal. The pilot uses the measured colors, replacing the brief's explicitly provisional forest green and yellow-orange values.

| Token | Observed selector/property | Value | Pilot use |
|---|---|---|---|
| Green | Navigation and ordinary content anchors, computed `color` | `rgb(25,118,137)` / `#197689` | Wordmark, primary actions, source accents |
| Orange | `.colibri-post-category`, computed `color` | `rgb(227,73,38)` / `#E34926` | Large wordmark accent, small decorative markers |
| Body text | `body`, computed `color` | `rgb(37,36,36)` / `#252424` | Body and source text |
| Typography | Computed body / heading families | Open Sans / Montserrat | Recorded; no font binaries copied. The pilot uses the local system font stack. |

Dark green `#115463`, page neutral `#F8FAF9`, tinted surfaces and borders are deliberate pilot adaptations, not claimed portal samples. Exact values, measured contrast and observation hashes are in [design-tokens.json](../pilot_ui/design-tokens.json). Orange is not used for normal-size body copy on white. Controls have at least 44 CSS pixels of touch height; source badges remain visually compact within that target.

The actual portal locale widget is a floating, lower-right Weglot flag-only switcher. Its current-language label has no visible text, and the alternative is a flag link. This conflicts with the product brief's explicit header location, accessible text links and no-flags requirement. The pilot therefore uses `SL / IT` header links with a current-page underline. This is an intentional product adaptation, not an assertion that the portal has an identical text switcher. Locale switching preserves the composer, selected writer and already stored answer language; an explicit action creates a differently localized answer.

The visible portal footer confirms SLORI and the Regione autonoma Friuli Venezia Giulia. The pilot retains compact text references and a link to the portal; no institutional logo was fabricated, and no EU funding claim was added.

Protected capture evidence (screenshots contain only the public portal, no login/session data):

- [Slovene desktop](/data/scratch/damjan/klikai-public-pilot-v2-1-20260910/brand-capture-v2/portal-sl-desktop.png)
- [Italian desktop](/data/scratch/damjan/klikai-public-pilot-v2-1-20260910/brand-capture-v2/portal-it-desktop.png)
- [Slovene mobile](/data/scratch/damjan/klikai-public-pilot-v2-1-20260910/brand-capture-v2/portal-sl-mobile.png)
- [Italian mobile](/data/scratch/damjan/klikai-public-pilot-v2-1-20260910/brand-capture-v2/portal-it-mobile.png)
- [Capture receipt](/data/scratch/damjan/klikai-public-pilot-v2-1-20260910/brand-capture-v2/receipt.json)

Only public same-origin CSS, computed-style JSON and screenshots were retained. Cookies, fonts, authenticated profiles, third-party analytics and whole-theme assets were not exported. No portal changes were made.

The application uses one source registry with text/initial fallbacks, never invented official logos. Source badges are populated only from the selected answer tier's `source_badges`; numeric references open the exact owned evidence. Authors remain inside optional bibliographic disclosure or original quotations and are not added as card bylines. The application does not rewrite original accents or names.

The implementation has 219 matching, nonempty Slovene/Italian keys. Nine focused tests verify prefix-safe routes, Unicode code-point anchors, unchanged canonical blocks, actual-used-source badges, safe publisher links, readable source page labels and absence of browser storage or raw HTML insertion. The real browser renders preserved text nodes; coordinate arrays remain server metadata, and bibliography is closed initially.

The loopback account backend was exercised with real cookies, CSRF checks, SQLite jobs and retained display receipts. A clearly marked synthetic adapter supplied repeatable race/Unicode cases on a separate fixture. Its complete twelve-check browser journey passed after a controlled fixture process restart; before/after row hashes match for seven tables, including answer versions, feedback events, assignments and votes. The browser then reopened the original answer, exact combining-mark feedback and revealed comparison from before the restart. Forced initial password change rotated the session; administrative create/reset/disable/export worked; exports excluded authentication internals. A failed first job-poll exposed a cancellation race, which was fixed by retaining the POST-issued job ID immediately.

Actual Qwen and Gemma generation was separately exercised on the candidate backend with synthetic-origin testing accounts. The twelve-check actual journey passed through short-first generation, one saved expansion, unchanged short text, exact “Huga” feedback, reload, source drawer, named/blind comparison, vote/reveal, second-account denial and logout/back. An initial premature test wait for paired long readiness was corrected; the same saved comparison was resumed without generating it again. All four model/locale combinations generated both tiers. The observed Hugo long answers were 106–141 words for the three matrix cases, and no claim of a 400-word output is made. These were shared-load observations, not a quiet latency benchmark.

Screenshots and receipts stay outside Git because they include private stored model answers, even though testing accounts are synthetic. Relevant private evidence:

- [Actual browser journey](/data/scratch/damjan/klikai-public-pilot-v2-1-20260910/browser-real-v3/browser-resumed.json)
- [Actual model/locale matrix](/data/scratch/damjan/klikai-public-pilot-v2-1-20260910/browser-real-v3/model-matrix.json)
- [Slovene expanded product](/data/scratch/damjan/klikai-public-pilot-v2-1-20260910/browser-real-v3/expanded-sl-desktop.png)
- [Italian Qwen expanded product](/data/scratch/damjan/klikai-public-pilot-v2-1-20260910/browser-real-v3/qwen-it-expanded.png)
- [Italian Gemma mobile product](/data/scratch/damjan/klikai-public-pilot-v2-1-20260910/browser-real-v3/gemma-it-mobile.png)
- [Readable original PDF drawer](/data/scratch/damjan/klikai-public-pilot-v2-1-20260910/browser-real-v3/epravopis-readable-drawer-it.png)
- [Isolated final browser journey](/data/scratch/damjan/klikai-public-pilot-v2-1-20260910/ui-integration-fixture/browser-final.json)
- [Restart row integrity](/data/scratch/damjan/klikai-public-pilot-v2-1-20260910/ui-integration-fixture/restart-proof.json)
- [Original records reopened after restart](/data/scratch/damjan/klikai-public-pilot-v2-1-20260910/ui-integration-fixture/post-restart-browser.json)

Desktop 1440px and mobile 390px/360px were tested. At 200% root text size on 360px there was no horizontal overflow; keyboard focus, dialog escape and the same-window comparison controls were exercised. No personal byline furniture was added. Local verification is distinct from public HTTPS deployment and external-device acceptance, which belong to the coordinator's deployment evidence.


The final candidate-policy browser pass passed all six checks, including sixteen observed blind pending responses containing only symmetric queued sides, no model identities or per-side job IDs. Mobile comparison tabs work with arrow keys. The actual v5 frozen base policy is `4d0aa459c8dc700762d51489f5026889a1329a4ce64bfb7bab23ab7bfea7c8ba`; the effective family policy also includes the tested application settings. Final dictionary source status/annual scope, deterministic text checking and actual saved analysis summaries passed in both interface languages. A separate explicitly injected correction-metadata test shows the correction notice while retaining the original short text; this is not claimed as a naturally occurring model correction.

Five distinct synthetic accounts remained authenticated in separate browser contexts at once. The writer owner limit admitted four requests and rejected the fifth with HTTP 429 `queue_full`; three queued requests and the active test request were explicitly cancelled, and all became terminal. Two source searches and two deterministic text checks rendered in 1.34–1.37 seconds while the writer was still running. The measured immediate UI acknowledgement was 29 ms in this sample. Logins were initialized sequentially and the requests then overlapped; this does not claim five simultaneous human login clicks or an external-device test. An initial concurrent automation-login timeout is retained separately. The three account roles were three testers, one reviewer and one admin.

- [Final six-check browser receipt](/data/scratch/damjan/klikai-public-pilot-v2-1-20260910/browser-final-v5/final-browser.json)
- [Actual frozen-family identities](/data/scratch/damjan/klikai-public-pilot-v2-1-20260910/browser-final-v5/family-identity.json)
- [Five concurrent account sessions](/data/scratch/damjan/klikai-public-pilot-v2-1-20260910/browser-final-v5/five-sessions.json)
- [Final Slovene expanded screen](/data/scratch/damjan/klikai-public-pilot-v2-1-20260910/browser-final-v5/hugo-sl-final.png)
- [Final blind mobile screen](/data/scratch/damjan/klikai-public-pilot-v2-1-20260910/browser-final-v5/blind-mobile-final.png)
- [Final annual-source drawer](/data/scratch/damjan/klikai-public-pilot-v2-1-20260910/browser-final-v5/annual-source-it-final.png)
- [Actual bilingual admin summary, Italian](/data/scratch/damjan/klikai-public-pilot-v2-1-20260910/browser-final-v5/admin-analysis-it-final.png)

The owned loopback fixture on port 18380 was stopped cleanly after verification; its private database and all receipts were retained. No accepted service was restarted by this UI lane. The root delivery controls the persistent application and public access.
