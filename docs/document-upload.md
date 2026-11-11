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
cancelled or unreviewed section blocks the complete draft download; partial
reports remain available and identify missing work.

Word drafts use the uploaded package to retain paragraph styles, tables,
images and relationships. Changed inline text styles follow the source runs
as closely as possible; translated word order can require a formatting check.
Plain-text input produces a new Word text document. Reports use their own
layout. Images are retained where applicable but are not linguistically
reviewed. Unsupported fields, tracked changes, embedded objects and complex
text constructs are rejected before processing rather than discarded.

## Boundaries and storage

Uploads are limited to 2 MiB, 200,000 codepoints and 100 sections of at most
3,000 codepoints. Accounts can retain 20 documents within 20 MiB of source
uploads and process one document at a time. Large source paragraphs are split
at whitespace into adjacent exact slices and rejoined for draft export.

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

Reviews propose nonoverlapping, source-located edits using exact server-owned
support units. The server derives offsets and reconstructs the proposed text.
For Slovenian input with the existing comma checker configured, supported
comma changes reuse the existing source-bound application graph and its
rendered explanation. That result explicitly identifies its comma-only scope.
Conflicting or unexplained changes remain unapplied. A zero-change prediction
continues through general source-supported draft review; it never certifies
that the document has no errors.
Translations return a complete generated draft without claiming that a
dictionary source attests an entire translated paragraph. Sources admitted to
the writer remain distinct from additional retrieved sources. Unsupported
review coverage is explicit and never becomes a claim that the text is correct.

All output remains a draft requiring human review. Schema, source membership,
quoted support and protected values are mechanically checked; these checks do
not establish linguistic entailment, complete error coverage or translation
quality. Original failures and provider observations remain preserved.

## Validation

Focused tests cover bounded file parsing, Unicode, unsafe ZIP/XML content,
typed task isolation, native output validation, exact source bindings, queue
idempotency/recovery, cancellation, deletion, exports and Word structure.
The browser harness checks both interface languages at 390/1280 widths.
Actual candidate examples and separate configured-suite receipts are retained
outside Git. Public release metadata changes belong to the later paired
publication step.
