# Document upload candidate

This feature adds an explicit document task alongside the existing question
workflow. It requires the paired CORE document-transform endpoint. It is off
by default; set `document_uploads: true` on a unified-answer WEB configuration
to enable it. No source corpus, ontology category, model identity or ordinary
external-client default changes.

## User flow

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

Missing governing context, invalid pins/ranges, unknown scope annotations or
unresolved editorial dependencies retain the full passage with an explicit
fallback reason. Hash/range checks prove identity and exact inclusion, not
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

Focused tests cover bounded file parsing, Unicode, unsafe ZIP/XML content,
typed task isolation, native output validation, exact source bindings, queue
idempotency/recovery, cancellation, deletion, exports and Word structure.
The browser harness checks both interface languages at 390/1280 widths.
Actual candidate examples and separate configured-suite receipts are retained
outside Git. Public release metadata changes belong to the later paired
publication step.
