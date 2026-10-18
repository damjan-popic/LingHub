# Triage repair, 18 October 2026

The opt-in KLIKai reference flow now shares a single request plan across source discovery, lexical profiles, writer selection and reader cards. Surface text, casing, offsets, direction and explicit context remain traceable. Accepted morphology matches are not discarded merely because the requested surface differs from a dictionary headword. Complete originals remain outside the compact writer projection.

Ordinary Slovene uses `klikai_classla_primary_v1`: one CLASSLA analysis reused by the canonical LORIS matcher. External legacy defaults and Italian analysis remain available. Recorded forms, supported agreement applications and unresolved interpretations are separate; raw predictions are not rewritten. See [lexical profiles](LEXICAL_PROFILES.md).

Typed terminology facts keep language, geographical attestation, form-specific recommendations, rationale, scope and editorial status distinct. Validation operates on cited support ranges and individual assertions. An irrelevant source assessment is omitted rather than transformed into a quotation. A completed refusal, invalid composition, withheld content, supported partial answer and service failure remain different observations. Exports add a derived view while retaining historical labels and snapshots.

`pilot_backend/request_tasks.py` contains a small resource registry checked on 2026-10-18 against the named owned pages. It offers the verified web entry points for forms, the bank glossary, Informascuole, the Italian constitution translation and Zvezek. It does not promise an offline download or invent service-counter information. Navigation and missing-referent replies do not invoke a writer. A referent may use explicit context or an explicitly supplied, owner-checked parent run; shared-account submission order is never context.

Reader cards use meaningful relationship labels (including antonyms), separate a supported interpretation from recorded alternatives, and keep engine/debug details in captured diagnostics. Feedback acknowledgements are scoped to the submitted answer and session, appear only after server acknowledgement, and are cleared for a new question.

The private release evaluator `tools/zrc_reference/triage_release_check.py` has twelve fixed generation cases, source/card parity checks, three deterministic checks and server-reported CPU operation audits. Every selected generation is attempted once. Raw and final outputs are retained separately. Passing automated checks is not linguistic certification: actual prose and citations must be reviewed before website publication. Existing saved questions, answers, feedback revisions and source captures are not rewritten.

The editorial naming dispute and the specific Urgenca newsroom audience concern require source-owner decisions; this code does not change source data or publication permissions. Private participant records and adjudication notes are excluded from the release export.
