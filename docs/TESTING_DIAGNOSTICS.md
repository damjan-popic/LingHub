# Private project testing records

Every accepted run receives a server-derived participant/group/origin observation
in the existing pilot database. CU/SLORI/MK default to project_use; TEHNO defaults
to technical_manual. An explicit origin=automated_test only excludes a submitted
attempt from natural-use totals; it cannot change group, ownership, model or
permissions. Synthetic accounts are always automated. Existing records are not
rewritten. Historical group can be identified by its owner, but an unrecorded
historical origin remains unknown and is excluded from the default report.

Each run links its original family input, immutable frozen sources (including
bounded returned candidates, LORIS tokens/rules/spans), selection and native-token
budget decisions, exact writer requests and final-only output, final display,
server/delivery timing, feedback revisions and existing triage. Extended capture
failure is marked incomplete and does not fail an otherwise valid answer.
Finished failures/cancellations retain their displayed status and accept feedback.
Feedback corrections remain separate revisions. A rating is never teaching approval.

## Operator export (no retrieval or generation)

Each command resolves the currently installed web release itself. It works in a
new terminal without first setting KLIKAI_CODE. An empty or stale PYTHONPATH can
cause `No module named pilot_backend`; the application is not missing.

CU/SLORI/MK project-use attempts, including unrated attempts:

```bash
sudo env PYTHONPATH="$(systemctl show klikai-pilot.service -p WorkingDirectory --value)" /data/envs/lingstack/pilot/bin/python -B -m pilot_backend --config-file /data/services/lingstack/releases/platform-control/synthesis-20260918-review-r3/config/pilot-editorial.json export-attempts --output-dir /data/scratch/damjan/klikai-testing-exports/project-$(date +%Y%m%d-%H%M%S) --owner damjan
```

For ministry-only triage, add `--group mk` to the export command. MK submissions, failures, no-support outcomes and feedback revisions use the same retained trace as the other project groups. A shared group login identifies the group, not an individual tester.

TEHNO manual technical attempts explicitly:

```bash
sudo env PYTHONPATH="$(systemctl show klikai-pilot.service -p WorkingDirectory --value)" /data/envs/lingstack/pilot/bin/python -B -m pilot_backend --config-file /data/services/lingstack/releases/platform-control/synthesis-20260918-review-r3/config/pilot-editorial.json export-attempts --group tehno --output-dir /data/scratch/damjan/klikai-testing-exports/tehno-$(date +%Y%m%d-%H%M%S) --owner damjan
```

Choose a new output directory for each export. Files are private (0600, directory
0700); --owner hands these newly created files to Damjan without granting access
to the account database. Omit --owner when already running as the intended owner.
The config selects only the existing database, not a new store.

Add `--origin automated_test` for smoke fixtures/calls, `--origin unknown` for
historical origin gaps, or `--origin all` explicitly. Repeat `--group cu --group
slori` to select groups explicitly. `--group all --origin all` includes every
known and unknown group/origin. Other filters: `--since`/`--until` (Unix seconds),
`--release` (recorded web revision), `--model`, `--provider`, `--outcome`,
`--degraded true|false`, `--rating`, `--review-status`. `--run-id` exports one
operator-selected case regardless of default group/origin filters.

Outputs: attempts.csv, attempts.jsonl, README.json with separate counts of
attempts/unique exact questions/rated outputs, and one readable Markdown plus
complete private JSON case per run. Sources and prompts are embedded from the
saved database; no disposable scratch reference or service access is necessary.
No feedback means unrated. Unknown fields stay unknown. Native scores retain
source/method labels; bounded search candidates do not represent the whole corpus.

## Limits and retention

Provider checkpoint revision, source-native score method, unavailable analyzer
fields and unobserved browser delivery time remain unknown where not exposed.
Shared Qwen aliases are not invented checkpoint IDs. No credentials, sessions,
headers, environment dumps or hidden reasoning enter the case export. Raw final
content with reasoning markers is omitted and marked incomplete.

The existing online SQLite/NAS backup captures run_observations along with the
other application tables and frozen families. No NAS call occurs on submission.
Ten recovery checkpoints does not mean ten releases of diagnostics. Automatic
180-day retention is deferred while human_testing_mode is enabled. Existing
account deletion/tombstone replay still applies; use the current authenticated
account deletion procedure for authorized deletions. No new purge is installed.
Keep exports private and remove their explicitly chosen export directory manually
when the project retention/review decision authorizes it. Later teaching requires
explicit human review and separately versioned corrections/evidence; no training
or automatic approval is performed by this update.
