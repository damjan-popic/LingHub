# Document upload and evidence-based source review

Document review is part of the standard authenticated KLIKai website. It uses
the same maintained source capture, linguistic processing, account store and
frozen readers as ordinary consultation. Its availability follows deployment
configuration and account permission, independently of the hosting machine.

## Using the online feature

Sign in at the deployment's normal HTTPS address with an enabled account.
Open **Pregled besedila z viri**.

1. Choose a UTF-8 TXT or DOCX file and press **Naložite in preglejte besedilo**.
   Uploading saves a preview and does not start a review.
2. Inspect **Izvirnik** in every extracted section, then press **Začnite**.
   The saved document list reopens work after the browser is closed.
3. Read proposals and conditions. **Odpri celotni vir** and
   **Odprite odgovor in vire** open the saved supporting material.
4. Download the separate report and proposed draft in TXT or Word. Check
   wording and formatting before use. Partial drafts are explicitly identified
   and preserve unfinished sections.

This evidence-only mode supports Slovenian review. General document AI
authoring, editorial summaries and translation remain disabled. Completion
records the processed scope; it does not certify that every error was found.

## Deployment and account access

Enable the existing unified-answer application with `document_uploads`,
`evidence_review`, `document_resources` and `evidence_review_only` true. Keep
`ai_suggestions`, `editorial_summary` and `private_translation` false. These are
standard application settings, not a separate local service requirement.

Set `document_account_ids` to the stable IDs of the existing permitted accounts.
The same check controls advertised capabilities and every document endpoint,
including the upload boundary before the binary body is accepted. Document
ownership and CSRF checks still apply. An empty list permits no accounts.
An omitted/null value preserves the earlier all-authenticated-accounts policy;
use an explicit list for a restricted online pilot. Administrators are not
implicitly exempt. Moving the same store preserves these account identities.

Configure `allowed_origins`, `secure_cookies`, optional `base_path`, `store`,
provider URLs/tokens and source configuration for the target deployment. Keep
secrets outside the repository. Browser requests and saved-work navigation use
same-origin application paths. Exported report reader links use the configured
HTTPS origin and application prefix at download time. No office address or
loopback URL belongs in user links. The existing authenticated edge/proxy
serves upload, history and download paths with the same security boundary.

New consultation and document review must use the intended current versioned
source configuration on the shared application backend. A separate terminology
edition must be an explicit deployment choice. Existing frozen work keeps its
original captures, source revisions and retained-code identities.

## Moving an installation

Use the existing protected SQLite backup/restore mechanism and deletion journal
for accounts, documents, original file packages, section links, frozen answer
bundles and results. Restore into a new destination, never over live activity.
Keep the matching versioned source data, database, manifests, retained code and
gateway retention configuration. Reconfigure host-specific paths and provider
addresses without rewriting capture bodies or rebinding historical work.
Sessions are revoked by the backup/restore mechanism; users sign in again.

Before switching traffic, validate configuration and verify an owned historical
document, original, result, source reader and all downloads, then one new TXT
and DOCX through the target HTTPS address. Verify private ownership with a
different account. Hardware can change processing time, while feature and
source-handling contracts remain the same. An office-local test alone does not
establish online availability or a completed migration.

Uploads remain off by default. With uploads off, startup retains the ordinary
account-store schema without initializing document tables. Existing documents
are neither removed nor migrated away by disabling the feature.

## General document contract

Upload UTF-8 `.txt` or `.docx`, inspect the extracted original, choose linguistic
review or translation and explicitly select the source language. Translation
supports Slovenian to Italian and Italian to Slovenian. Processing starts only
after the user presses Start. It continues when the browser is closed and can
be reopened from the document list.

Each section retains its exact original, proposed wording, explanations and
frozen sources. The user can download a report as TXT/Word and, after every
section has a usable draft, download the proposed Word document. A failed,
cancelled or unreviewed section, including a recorded failed requested
sub-operation, blocks the complete draft download. Bounded source support by
itself does not block export. Reports identify missing work.

The user separately authorized partial proofreading Word drafts on 11 November
2026. An explicit partial download is available after proofreading is terminal:
it applies available bound proposals and retains the original text where no
proposal is available. Its filename and visible opening label identify it as
partial; the report identifies each retained/proposed section. Saved model
results stay unchanged. This option does not apply to translation and does not
relax complete-draft eligibility or edit validation.

Word drafts use the uploaded package to retain paragraph styles, tables,
images and relationships. Body paragraphs, table cells, headers, footers,
footnotes, endnotes and original comments are processed as text; retention of
an image or package part does not mean its content was reviewed or translated.
Translation attaches supported inline emphasis and hyperlinks to exact,
explicitly mapped translated expressions. Missing or ambiguous mappings block
Word export instead of guessing where a link or emphasis belongs. Overlapping
or complex inline structures are rejected before processing. Formatting still
requires a human check after translation.
Plain-text input produces a new Word text document. Reports use their own
layout. Images are retained where applicable but are not linguistically
reviewed or translated. Existing comments are retained text, not newly generated
review comments. Clean drafts and separate reports do not contain generated
tracked revisions. Unsupported fields, tracked changes, embedded objects and complex
text constructs are rejected before processing rather than discarded.

## Boundaries and storage

Uploads are limited to 2 MiB, 200,000 codepoints and 100 sections of at most
3,000 codepoints. Accounts can retain 20 documents within 20 MiB of source
uploads and process one document at a time. Large source paragraphs target
1,500-codepoint sections, preferring sentence boundaries and otherwise using
whitespace. Protected inline expressions stay together within the 3,000-codepoint
hard limit. Adjacent exact slices retain all original characters and are rejoined
for draft export. This bounded packaging does not guarantee that a provider
operation will finish; failures retain partial reports and block a complete draft.
For a terminal proofreading job, an explicit separate partial Word download is
available. It carries a visible partial-review title and explanation, retains
available proposals, and preserves the exact original text where no proposal
exists. The report identifies these sections and any failed sub-operation.
Translation has no partial Word export. A recorded failed proofreading
sub-operation blocks the complete draft even when a separate supported comma
correction is available; partial source coverage alone does not block it.

Original text, original Word packages, task options, section links and frozen
results live in the existing protected account SQLite store and its existing
backup/retention mechanism. Uploaded prose never becomes dictionary or
answer-bank evidence. Authentication, same-origin checks, CSRF and ownership
checks apply to upload, processing, history, source viewing and downloads.
The larger binary request allowance applies only to the authenticated upload
endpoint; ordinary JSON request limits remain unchanged.

Deleting a document removes its document data and linked answers/follow-ups.
Deletion is rejected while any of those jobs are active. Account deletion
cascades through the added tables. Cancellation prevents subsequent sections;
an already running provider operation can finish before its result is discarded.

## Transformation contract

The private `/internal/pilot/document-transform` endpoint accepts a bounded
typed task, exact UTF-8 source hash and the existing immutable source-capture
packet. Uploaded prose is data and bypasses the question interpreter. The
existing local writer, native token counter, identity verification and shared
provider lease are reused. No fallback model or automatic retry is added.
Later translation sections can include up to two exact earlier completed
source/draft pairs from the same owned document and language direction, within
a combined 6,000-codepoint bound. Hashes bind both sides. They provide continuity
for wording and references, are counted in the native input budget and remain
draft context rather than source authority or a shared translation memory.
Source lookup uses one literal contiguous snippet of at most 500 codepoints
per section, preserving phrase adjacency within the existing capture limits.
Direct recorded advice and term matches are fitted ahead of generic material;
the full frozen records and their qualifications remain available in readers.
Pinned native morphology nominations can provide the exact observed source
surface for input relevance and terminology hints. A whole query returned by a
contained raw lookup is not treated as a term occurrence. These annotations
retain native ambiguity, release pins and scope; they do not select an
equivalent or establish that a source applies.
When a review has exact native advice or term nominations, only those source
parents enter its writer request; omitted records remain in the saved readers
and coverage is explicitly partial. Ordinary requests without an inspected
selection plan retain complete passages as exact ordered segments with closed
paragraph support IDs, including whitespace and long qualifications.

The internal review contract also accepts an explicit `source_selections` plan
from the server-owned, hash-bound `writer_source_selections` frozen field.
Public document/task options and model output cannot supply this authority.
This bounded representation work does not automatically create such plans.
The offline CU example is a task-local inspected selection, not automatic
discovery of all governing scope or an optimisation of ordinary requests.

Each plan binds the exact document hash, parent capture and rendered-passage
hashes, parent record revision, complete recorded advice IDs, and mandatory
governing-context ranges. The `document-source-representation.v2` payload labels
selected excerpts and required context explicitly; its segments need not be
contiguous or concatenate to the full source. Exact offsets/hashes, omitted
ranges, edition/status, complete advice annotations including `scope_note`, and
the unchanged full frozen-reader identity remain available. Only the complete
selected advice units receive support IDs. This creates no executable rule or
automatic preferred equivalent; A5e remains qualified, proposed source advice.

Malformed plan schemas, duplicate parent plans and plans naming an absent
parent reject before projection. Structurally valid plans with missing governing
context, invalid pins/ranges, unknown scope annotations or unresolved editorial
dependencies retain the full passage with an explicit fallback reason.
Hash/range checks prove identity and exact inclusion, not
semantic completeness. Establishing the required scope dependencies is the
explicit inspected-plan assumption; a client or model cannot self-certify it.
Translation selections remain unavailable in this bounded version. Typed ready comma tasks budget the actual
closed application catalogue, retaining full raw proof server-side; the
existing input and output caps still apply.

Reviews propose nonoverlapping, source-located edits using exact server-owned
support units. The server derives offsets and reconstructs the proposed text.
For Slovenian input with the existing comma checker configured, supported
comma changes reuse the existing source-bound application graph and its
rendered explanation. The general review also runs for independent non-comma
issues in the same section; exact edits merge only when they do not conflict.
That independent operation explicitly excludes comma changes, which remain
owned by the existing checker and source-bound application graph.
Conflicting or unexplained changes remain unapplied, and the report states
partial coverage. An unchanged passage never certifies that the document has
no errors. Italian review uses existing bilingual terminology support where
applicable; it does not offer comprehensive Italian grammar checking.
Translations return a complete generated draft without claiming that a
dictionary source attests an entire translated paragraph. Sources admitted to
the writer remain distinct from additional retrieved sources. Unsupported
review coverage is explicit and never becomes a claim that the text is correct.

All output remains a draft requiring human review. Schema, source membership,
quoted support and protected values are mechanically checked; these checks do
not establish linguistic entailment, complete error coverage or translation
quality. Original failures and provider observations remain preserved.
Standalone reports include real source titles and authenticated frozen-reader
links, plus the saved answer destination. They distinguish edit support from
sources present in a retained writer request and from additional retrieval
context. Missing or ambiguous writer-selection observations are marked unknown;
neither retrieval nor presence in the writer input proves linguistic use.
These references require the original KLIKai instance and the same account.
Valid independent dotted dates can vary padding and spacing while retaining
the same ordered day, month and year. Changed values, identifiers, URLs and
other protected numerals retain their strict checks.

Exact replay of an earlier translation for a different slice of the same
source paragraph blocks Word export and appears in the report and interface.
The stored model response is retained unchanged. Identical source slices and
independent source paragraphs are not rejected merely for repeated wording.
This guard does not establish semantic equivalence, completeness or correct
attachment of emphasis and links: even a structurally valid whole-phrase
mapping can point to the wrong translated expression.

The private November 2026 product pass is not linguistically accepted. It
demonstrates valid source-bound comma correction and usable file/report
mechanics, but the current writer still misses supported non-comma changes,
can return invalid reviews, leave labels untranslated, repeat earlier content
and assign an inline link to unrelated text. Those results are retained as
failed product evidence. Source advice without an existing executable
application is not converted into a new automatic correction rule.

## Validation

### Explicit private SL→IT candidate

The separate `private_translation: true` WEB capability is off by default and
requires document uploads. It exposes a deliberate `translategemma_sl_it`
selection only for translation from Slovenian to Italian; ordinary writing,
review and default translation requests retain their routes. The browser can
submit this choice, but cannot supply its provider profile, origin or model
settings. CORE's configured native profile is fetched and the exact complete
server-owned pin is saved at document Start and included in every section's
frozen task. The browser receives only the choice and opaque profile hash.
Changed configuration cannot substitute a different profile in that document.

Start performs one token-count-only preflight of all original sections before
any completion. Native input is limited to 2,048 tokens including BOS; original
source hashes retain outer whitespace even where the native template trims it.
No previous generated draft is attached on this route. Frozen reader records
may remain available as additional material, but the native model receives no
KLIKai evidence, glossary, source-excerpt plan or requested JSON schema. A
source-bound native receipt records the empty admitted-evidence list without
inventing a legacy writer request. Its plain-text result remains an unverified
draft; terminology consultation and semantic inline alignment are not claimed.

Initial containment defaults are four extracted sections, one active candidate
document with at most two waiting documents, 1,800 seconds waiting and 1,500
seconds for the document. Optional `private_translation_limits` may tighten
`max_sections`, `waiting_documents`, `queue_seconds` and `document_seconds`.
These limits are private safeguards, not measured throughput. Candidate jobs
use a distinct resource key in the existing durable queue and remain serial;
ordinary writer jobs take priority between candidate operations. Cancellation
records a source/profile/request-ID-bound cancellation at CORE as well as the
existing durable job cancellation. Late results cannot complete cancelled jobs.
Queue/runtime expiry is an explicit failure. Recovery never replays a job that
may already have dispatched a completion.

Hyperlink/emphasis spans make this selection ineligible before dispatch. Its
complete clean TXT and simple Word drafts reuse `draft_blocks`, `draft_text`,
`draft_docx` and the existing original-package rewrite path: no replacement
export engine or fabricated range mappings. The UI distinguishes clean
translation TXT/Word from comparison reports. All sections must pass the
existing complete-draft predicate; no partial translation download or silent
original-text fill is added. Complete/partial proofreading policy is unchanged.
The existing empty-edit review result means no proposal within the processed
scope, while absent and no-op edits remain rejected. Broader Italian grammar,
long-document translation quality and concurrent writer performance remain
unproven. The overall product is not linguistically accepted.

Focused tests cover bounded file parsing, Unicode, unsafe ZIP/XML content,
typed task isolation, native output validation, exact source bindings, queue
idempotency/recovery, cancellation, deletion, exports and Word structure.
The browser harness checks both interface languages at 390/1280 widths.
Actual candidate examples and separate configured-suite receipts are retained
outside Git. Public release metadata changes belong to the later paired
publication step.
