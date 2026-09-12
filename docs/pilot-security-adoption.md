# Retained pilot security adoption

`pilot_backend.adoption` applies `pilot-security-adoption-v1` only to an existing separate adoption database. The infrastructure preparer must first prove that this copy equals its authoritative retained snapshot, then call `migrate_adoption`. The module never initializes or migrates the application schema, restores source data, starts workers, or modifies a historical receipt.

The caller supplies a unique migration ID, authoritative logical identity, historical receipt identities, a protected individual operator password, and the retired signing key for offline inventory verification. The KLIKai candidate root must be in `PYTHONPATH` for that inventory: verification uses `tools.zrc_reference.pilot_evidence.verify`. The retired key is never written to the adoption database or runtime configuration.

Allowed database changes are explicit:

- Keep every account ID and all noncredential account columns. Replace `password_hash`, set `must_change=1`, and change `password_expires` only. Each nonoperator account receives its own discarded random Argon2id credential with an already expired timestamp. The selected existing enabled human administrator receives a private individual temporary credential valid for 24 hours. Multiple eligible administrators require an explicit account ID; an absent operator is a refusal.
- Remove all sessions from the adoption copy. Keep login attempts and historical audit rows.
- Append exactly one `security_adoption` audit event and its sequence advance.
- Add exactly one `meta.recovery_adoption_v1` value binding migration inputs, counts, the operator ID, audit event identity and historical inventory. No schema change or account bootstrap occurs.

`validate_adoption(before_path, after_path, receipt)` independently checks all tables, schema definitions, column/type metadata, application/user versions, multiplicity, SQLite value types, exact text/blob bytes and the declared exceptions. Infrastructure also compares the deletion ledger and source freshness, independently of this database-only function.

The inventory includes only already-retained signed families and their actual saved runs. Family IDs, authenticated owners, full stored family/context/source identity, exact bundle bytes and immutable run bytes are bound. Authorized historical reads check those records again by database ID; source opening uses only the inventoried saved excerpt. The gateway retains its current-key-only signature verification. Arbitrary old-key packets are rejected, including mathematically valid signatures. Existing historical records retain their bytes and are explicitly labelled as exact retained records with no fresh origin assurance. New runs from those historical families are refused; a new question creates a newly signed family. Existing saved long answers remain readable.

The same migration inputs reuse the existing credential and metadata without a second audit or password rotation. Conflicting inputs or changed operator credentials/inventory refuse. The database transaction atomically commits the credential, audit and inventory changes. The infrastructure preparer additionally pins complete output bytes and checks partial output stages before reuse.

## Validation

Synthetic application and gateway tests use real Argon2, auth/session/CSRF/ownership routes, SQLite, KLIKai signing, a newly built temporary fulltext snapshot, `SnapshotRegistry`, `PilotRuntime` and gateway HTTP consumers. Model generation in the application/browser fixtures is explicitly synthetic; the real gateway integration uses CPU source search and does not start a model.

Run from the LingHub candidate with both candidate roots in `PYTHONPATH`:

```sh
/data/envs/lingstack/pilot/bin/python -m pytest -q tests/test_pilot_backend.py tests/test_pilot_backend_recovery.py tests/test_pilot_backend_replay.py tests/test_pilot_human_feedback.py tests/test_pilot_cpu_capabilities.py tests/test_pilot_adoption_security.py
```

The focused run passed 104 tests. The adoption file is included in that count (15 tests); do not add those counts together. A separate live loopback Chrome browser run passed five checks covering retired credentials/session rejection, individual operator login and forced change, retained saved answer/source reading, new answer and feedback persistence after reload, and logout. Its server and browser entrypoints are `tests/test_pilot_ui_adoption_server.py` and `tests/test_pilot_ui_adoption.cjs`.

`pilot_backend.adoption_probe.probe_candidate_adoption` copies the entire supplied adoption file set privately before opening SQLite. It tests the real application on that disposable copy and verifies the frozen input hashes did not change. Returned results contain only check names and counts. Never place the private onboarding file, raw databases, signed payloads or password hashes in a review archive.

For the final prepared adoption, pass `recovery_helper` (the pinned infrastructure `pilot-recovery-proof.py`) and `recovery_pins` (including final `git_commits`) to `probe_candidate_adoption`. The probe adds two clearly synthetic current-key families only on its disposable copy, invokes the real backup/support/restore helper, verifies original saved records, accounts, feedback, audit and login history, and returns `recovery.status` plus separate current/historical counts and the helper SHA-256. It never supplies the retired key to recovery. The separate infrastructure recovery integration suite covers this exact call, markerless v1 recovery, and corruption/forgery refusals.
