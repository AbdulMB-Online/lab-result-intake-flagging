# Lab Result Intake and Flagging

A command-line tool for a clinic that receives lab results as CSV files. It
checks incoming records, flags findings for staff review, and keeps results and
check history in SQLite so staff can retrieve reports and earlier measurements.

**Synthetic data only.** The tool flags and reports; it does not diagnose,
interpret a patient's condition, or recommend treatment. The clinician decides.

The current application version is **1.0.0**. It includes
age/sex-based ranges, saved-report retrieval, optional report email, and an
inactive-patient list. The versioned releases are still being prepared;
[release status and procedure](#releases-and-rollback) appear below.

## Run with Docker

Use Docker with Linux containers. Run these commands from the repository root
on the updated `main` branch or a branch containing its merged changes.

```bash
docker build -t lab-result-intake:dev .
docker volume create lab-history
```

Define a helper in the same terminal. Each call starts a new container while
reusing the database volume; input files and settings are mounted read-only.

```bash
lab() {
  docker run --rm \
    --mount type=volume,source=lab-history,target=/data \
    --mount "type=bind,source=$PWD/data,target=/inputs,readonly" \
    --mount "type=bind,source=$PWD/config,target=/config,readonly" \
    lab-result-intake:dev "$@"
}

lab run --results /inputs/results.csv \
  --orders /inputs/orders.csv --patients /inputs/patients.csv
```

Actual first-run summary from the checked-in synthetic files at `1a9a92a`,
verified in Docker on 30 October 2026:

```text
Saved 50 results and 51 orders. Check run 1 recorded 9 flags and 11 rejected rows.
```

The full report groups findings by severity and rule, then lists rejected rows
with their line numbers and reasons. A new check is recorded on every import;
use `report` to retrieve an existing check without importing again.

```bash
lab report                 # latest stored check
lab report --run 1         # a specific stored check
lab history PAT-0002 GLU    # one patient's stored results for one test
lab inactive --years 10 --as-of 2026-10-30
```

Actual history from that run:

```text
Casey Okonkwo (PAT-0002), Glucose, Fasting (GLU), reference range 70-99 mg/dL

  2026-09-26        85.6 mg/dL  reported 2026-09-27
  2026-10-06        48.2 mg/dL  reported 2026-10-08  flagged: OUT_OF_RANGE

This report flags results against the clinic's reference ranges. It does not interpret them. The clinician decides.
```

The inactivity command uses each patient's newest stored **collection date**.
It lists patients at or beyond the cutoff, excludes patients with no stored
result, and does not delete anything. With the same files, it prints:

```text
Patients with no result collected in 10 years or more, as of 2026-10-30: 2
  PAT-9002   Morgan Gallagher         last result 2011-10-25
  PAT-9001   Logan Petrov             last result 2015-10-25
```

The Docker image uses Python 3.13 and runs as the non-root `lab` user. The
application lives in `/app`; `/data/lab.db` lives on the named volume. Removing
a container therefore preserves history. Keep the volume: it is the working
database, not a backup.

## Local setup and configuration

Use Python 3.13 with SQLite 3.37 or newer (`STRICT` tables are required). The
application uses Python's standard library; pytest is a development dependency.

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
export PYTHONPATH="$PWD/src"

python -m lab_intake run --results data/results.csv \
  --orders data/orders.csv --patients data/patients.csv
python -m lab_intake report
python -m lab_intake history PAT-0002 GLU
```

| Setting | Local default | Docker default |
|---|---|---|
| `LAB_DB_PATH` / `--db` | `lab_intake.db` in the repository root | `/data/lab.db` |
| `LAB_SETTINGS_PATH` / `--settings` | `config/settings.json` | `/config/settings.json` |
| `--env-file` | `.env` in the repository root | `/app/.env`, if supplied |

Global options go **before** the subcommand; `--db` and `--settings` override
their environment variables. For example:

```bash
python -m lab_intake --db demo.db --settings config/settings.json report
python -m lab_intake --help
```

To generate a separate, repeatable dataset without replacing `data/`:

```bash
python scripts/generate_synthetic_data.py --out /tmp/lab-demo-data \
  --settings config/settings.json --seed 20251026 \
  --patients 12 --today 2026-10-26
```

The generator adds cases for malformed rows, missing values/results, boundary
values, demographic exceptions, and two inactive patients. The requested patient
count is the base count; additional patients support the planted cases. Use a
fresh database when reproducing first-run counts.

## Input records and validation

CSV files use a header row and UTF-8 text. Dates use `YYYY-MM-DD`; collection
must not be after reporting. Test codes are normalized to uppercase.

| File | Columns |
|---|---|
| Results | `result_id, patient_id, test_code, value, unit, collected_date, reported_date, order_id` |
| Ordered tests | `order_id, patient_id, test_code, ordered_date` |
| Patients | `patient_id, display_name, birth_date, sex` |

`result_id` and `order_id` are the source identifiers. A result's `order_id` may
be blank or its column omitted for legacy files. The patients file is required:
sex (`F`, `M`, or `X`) and birth date select the applicable range. Names in the
sample files are invented. Missing demographic information is not replaced
with an assumed patient's range.

**Rejecting and flagging are different:**

- Missing identifiers, unknown test codes, malformed rows, invalid dates,
  negative/non-finite numbers, unreadable numbers, and duplicate IDs within a
  file produce rejected-row records with reasons. They do not become accepted
  results or enter the rules engine.
- A blank value or a recognized marker such as `PENDING`, `NA`, or `N/A` is kept
  as a result with no numeric measurement and receives `MISSING_VALUE`.
- A valid numeric record is kept even when a rule flags its value. The reported
  unit is retained; blank or unexpected units are not yet flagged separately.

A result naming an order answers that ID. For legacy rows without an order ID,
matching uses patient, test, and a collection date on or after the order date.
A missing-result finding means no match in the supplied input; it is not an
elapsed-time or overdue test. Legacy matching can satisfy multiple orders with
one result, and an explicit ID match does not cross-check patient/test identity.

## Reference ranges and rules

[config/settings.json](config/settings.json) defines the ranges; changing them
does not require editing rule code. The current settings format is `1.2` and
covers `GLU`, `K`, `HGB`, `CREA`, `TSH`, and `PSA`. This is a settings-version
label, separate from the application's release number.

One complete test definition looks like this:

```json
{
  "schema_version": "1.2",
  "tests": {
    "GLU": {
      "name": "Glucose, Fasting",
      "unit": "mg/dL",
      "ranges": [
        {
          "sex": "ANY",
          "age_min": 18,
          "age_max": 200,
          "low_lt": 54,
          "normal_min": 70,
          "normal_max": 99,
          "high_gte": 126
        }
      ]
    }
  }
}
```

Age is measured in whole years on the collection date. Age bounds and normal
limits are inclusive. A matching `F` or `M` band takes precedence over `ANY`;
`X` can use `ANY` but never borrows an `F` or `M` band. Tests may specify
`error_for_sex`, as the current PSA definition does for `F`.

**Borderline bands are fixed numeric intervals outside the normal range.**
For the GLU definition above:

| Value in mg/dL | Classification |
|---|---|
| Below 54 | `OUT_OF_RANGE` |
| 54 up to, but not including, 70 | `BORDERLINE` |
| 70 through 99, including both limits | No range flag |
| Above 99 up to, but not including, 126 | `BORDERLINE` |
| 126 or higher | `OUT_OF_RANGE` |

`high_gt` means strictly above the upper cut; `high_gte` includes the cut itself.
Each band specifies exactly one of these. `low_lt` is optional; without it,
values below `normal_min` are out of range with no borderline-low interval.
The old `borderline_pct` format is rejected. Missing/unreadable settings,
malformed JSON, invalid cuts, or overlapping bands for the same sex stop the
command rather than selecting a default range.

| Rule | What triggers it | Example using the supplied configuration |
|---|---|---|
| `OUT_OF_RANGE` | Beyond an outer cut | Adult GLU 48.2 mg/dL, below 54 |
| `BORDERLINE` | In a fixed band outside the normal range | Adult GLU 69 mg/dL |
| `MISSING_VALUE` | Result arrived without a number | TSH marked `PENDING` |
| `MISSING_RESULT` | Supplied order has no matching accepted result | An order absent from the result file |
| `NO_REFERENCE_RANGE` | Numeric result has no applicable demographic band | HGB with sex `X` |
| `UNDER_18` | Numeric result belongs to a patient under 18 | GLU for a 17-year-old |
| `SEX_NOT_APPLICABLE` | Numeric result has a sex listed in `error_for_sex` | PSA with sex `F` in this configuration |

The last three findings prevent comparison against an unsuitable range.
`BORDERLINE` has medium severity; the other current rules have high severity.
These are workflow labels. Independent subclasses of `ResultRule` produce the
findings; most implement `check()` for one result, while `MissingResultRule`
implements `evaluate()` across orders and results. The `RULES` registry selects
the rules to run.

## Stored records and reports

SQLite tables are defined in [storage.py](src/lab_intake/storage.py). Foreign
keys are enabled on every application connection, and all tables are `STRICT`.

| Table | Columns |
|---|---|
| `patients` | `patient_id` (PK), `display_name`, `birth_date`, `sex`, `last_result_date` |
| `orders` | `order_id` (PK), `patient_id` (FK), `test_code`, `ordered_date` |
| `results` | `result_id` (PK), `patient_id` (FK), `test_code`, `value`, `unit`, `collected_date`, `reported_date`, `order_id` (FK, nullable) |
| `rules` | `rule_code` (PK), `description`, `severity`, `release` |
| `check_runs` | `check_run_id` (PK), `run_at`, `release`, `settings_version`, `results_file`, `orders_file` |
| `flags` | `flag_id` (PK), `check_run_id` (FK), `rule_code` (FK), `severity`, `patient_id` (FK), `test_code`, `result_id` (FK, nullable), `order_id` (FK, nullable), `message`, `details` (JSON text) |
| `rejected_rows` | `rejected_id` (PK), `check_run_id` (FK), `source_line`, `record_id`, `reason_code`, `reason` |

Patient links join results and orders to demographics. Each flag belongs to a
check run and references a result or an unmatched order. Each save function
uses a transaction; a check, its flags, and its rejection records are saved
together. The full import uses separate transactions, so a later failure does
not undo every earlier save.

Repeated result/order IDs retain the stored record in this baseline. Every
import still creates a new check. Corrected-result version history is not yet
part of the reviewed merged baseline. `last_result_date` is added and backfilled
when an older database is opened.

Stored reports preserve findings and messages, but displayed ranges use current
patient details and the settings supplied when the report is opened. A settings-version label is not a
full settings snapshot: retain the matching configuration with each release.
History collects the rule codes previously associated with each result. Always
read rejected rows alongside the flags; a report with no flags does not mean
that rejected records were checked.

## Optional report email

`run` and `report` accept `--email-to`. They print the report first; a handled
SMTP failure adds a warning while preserving the printed and stored report.
Email success does not establish that someone reviewed the result.

Copy `.env.example` to a local `.env` and configure `LAB_SMTP_HOST`,
`LAB_MAIL_FROM`, and any required credentials. `LAB_SMTP_PORT` defaults to 587;
`LAB_SMTP_STARTTLS` defaults to `1`. Keep credentials out of Git and the image.
The current code loads the file only when email is requested, and existing
environment values take precedence.

```bash
# Local use after configuring .env; use an authorized test recipient.
python -m lab_intake --env-file .env report --run 1 --email-to recipient@example.org

# Docker passes the configuration at runtime; .env is not copied into the image.
docker run --rm --env-file .env \
  --mount type=volume,source=lab-history,target=/data \
  --mount "type=bind,source=$PWD/config,target=/config,readonly" \
  lab-result-intake:dev report --run 1 --email-to recipient@example.org
```

Only use synthetic reports for this project. Email is optional; the ordinary
run, report, history, and recovery workflows need no mail configuration.

## Backup and recovery

Backups use `sqlite3.Connection.backup()`, then verify database integrity and
foreign keys before publishing the copy. Both backup and restore refuse an
existing destination. They work without the settings file, allowing recovery
when configuration is missing or damaged.

Create separate backup and restore volumes. Use unused destination names when
repeating the demonstration; do not reuse an existing backup filename.

```bash
docker volume create lab-backups
docker volume create lab-restored

docker run --rm \
  --mount type=volume,source=lab-history,target=/data \
  --mount type=volume,source=lab-backups,target=/backups \
  lab-result-intake:dev backup --output /backups/baseline.db

docker run --rm \
  --mount type=volume,source=lab-restored,target=/data \
  --mount type=volume,source=lab-backups,target=/backups,readonly \
  lab-result-intake:dev restore --backup /backups/baseline.db
```

Actual success output from the verified Docker run:

```text
Backup saved to /backups/baseline.db
PRAGMA integrity_check: ok
PRAGMA foreign_key_check: ok
Restored database to /data/lab.db
PRAGMA integrity_check: ok
PRAGMA foreign_key_check: ok
```

These fixed success messages are printed only after the checks pass. Check the
restored history in a new container before switching the working volume:

```bash
docker run --rm \
  --mount type=volume,source=lab-restored,target=/data \
  --mount "type=bind,source=$PWD/config,target=/config,readonly" \
  lab-result-intake:dev history PAT-0002 GLU
```

On 30 October 2026, source and restored database contents matched across all
seven tables: 17 patients, 51 orders, 50 results, 7 rules, 1 check, 9 flags, and
11 rejects. The history output matched exactly. A second restore to the same
destination was refused without changing the restored data.

For local recovery, after local setup:

```bash
python -m lab_intake --db lab_intake.db backup --output backups/baseline.db
python -m lab_intake --db restored/lab_intake.db restore --backup backups/baseline.db
python -m lab_intake --db restored/lab_intake.db history PAT-0002 GLU
```

Pause writers before changing the working database path/volume. Keep the
original until the restored copy is verified. Backup scheduling, access,
retention, and acceptable recovery time remain clinic operating decisions;
the inactivity cutoff is not a deletion policy.

## Tests and CI

```bash
python -m pytest -q
```

[GitHub Actions](https://github.com/AbdulMB-Online/lab-result-intake-flagging/actions/workflows/ci.yml)
runs tests on pushes and pull requests, using Ubuntu 24.04 and Python 3.13. It
checks dependencies and SQLite compatibility first; no collected tests is a
failure. Require the **Python tests** check in the repository's branch rules
to enforce passing tests before merge.

The integration test generates synthetic files, invokes the real CLI in
separate processes, and checks reports and persisted history. Separate tests
cover rule boundaries, invalid inputs, transaction rollback, saved reports,
inactivity, mail behavior, and backup/restore. Mail tests use mocks; neither
they nor the CLI integration test prove live email delivery. Container volume
persistence and release upgrade/rollback need their own demonstration.

Verification for merged commit `1a9a92a`: **303 tests passed locally** on Python
3.15; its GitHub Python 3.13 run also passed. Docker import, saved-report
retrieval, history, inactivity, backup, restore, and overwrite refusal were
verified separately on 30 October 2026. Published release rollback is still
pending.

## Releases and rollback

| Release | Scope | Status at the reviewed baseline |
|---|---|---|
| `1.0.0` | Current intake, seven rules, storage/report/history, inactivity, optional email, and recovery | Implemented; Git tag, versioned image handoff, and changelog entry pending |
| `1.1.0` | Add `UNEXPECTED_UNIT` for a blank or mismatched unit; no automatic conversion | Planned; rule and release demonstration pending |

The unit rule remains Ansh's implementation task. The plan is to retain
independent rule findings, so a mismatch may appear alongside a range flag.
Confirm the new behavior and its tests before assigning the `1.1.0` version.

For each release:

1. Agree the release contents. Update `__version__` in
   `src/lab_intake/__init__.py`, add the matching `CHANGELOG.md` entry, and update
   the run instructions through a pull request.
2. Merge the reviewed PR with passing CI. From that clean release commit, build
   and check the image with synthetic inputs and a fresh volume.
3. Tag that exact commit and retain its settings with the image. For the first
   release, after the checks pass:

   ```bash
   git tag -a v1.0.0 -m "Release 1.0.0 - Abdul Malik"
   git archive v1.0.0 | docker build -t lab-result-intake:1.0.0 -
   mkdir -p release-config/1.0.0
   git show v1.0.0:config/settings.json > release-config/1.0.0/settings.json
   ```

   Verify that tagged image with the run and recovery commands above, using
   its saved settings. Then publish the tag and export the tested image:

   ```bash
   git push origin v1.0.0
   docker image save -o lab-result-intake-1.0.0.tar lab-result-intake:1.0.0
   ```

   The tar file can be shared and loaded with
   `docker image load -i lab-result-intake-1.0.0.tar`. Keep published version tags fixed. Repeat the
   process with `1.1.0` only when its implementation is complete.

To demonstrate upgrade and rollback after both releases exist:

1. Run `1.0.0` with its saved configuration on a dedicated history volume.
   Record baseline result IDs/values and check IDs, then take a verified backup.
2. Run `1.1.0` on the same volume with its corresponding configuration and a
   synthetic unexpected-unit case. Confirm the new flag and `1.1.0` check label.
3. Return to the `1.0.0` image and its matching settings on that same volume.
   For a baseline stored in `lab-history`:

   ```bash
   docker run --rm \
     --mount type=volume,source=lab-history,target=/data \
     --mount "type=bind,source=$PWD/release-config/1.0.0,target=/config,readonly" \
     lab-result-intake:1.0.0 history PAT-0002 GLU
   ```

4. Verify the baseline results and previous checks remain intact. A new release
   may add flags, so history text need not be identical. Preserve the actual
   commands, versions, and comparison output as release evidence.

Rollback compatibility must be tested. The older percentage-based settings and
the current age/sex format are incompatible; changing the image alone does not
switch the mounted settings. The current database also adds `last_result_date`.
If the older image cannot use the upgraded database, restore the verified
pre-upgrade backup to a fresh volume and account for work since that backup.
Do not describe recovery from a backup as proof of an in-place rollback.
