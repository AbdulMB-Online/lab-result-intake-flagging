# Decisions and customer answers

**Release 1.1.0** · Sources S1–S6 are listed in the [client brief](client_brief.md#sources).

## Current decisions

| Decision | Released behavior or operating requirement | Source |
|---|---|---|
| Scope | Use synthetic data. The tool flags findings for clinician review. | S1; [generator](../scripts/generate_synthetic_data.py). |
| Reference ranges | Select an age/sex band using whole years at collection and inclusive age bounds. Sex-specific bands precede `ANY`; `X` only uses `ANY`. | S6; [settings](../src/lab_intake/settings.py), [configuration](../config/settings.json). |
| Boundary behavior | Normal bounds are inclusive. For configured adult GLU, 70 and 99 mg/dL are normal, 54 is borderline, and 126 is out of range. | [Configuration](../config/settings.json), [boundary tests](../tests/test_settings.py). |
| Borderline bands | Use fixed numeric intervals outside the normal band, with optional `low_lt` and exactly one of `high_gt` or `high_gte`. | [Settings](../src/lab_intake/settings.py). |
| Demographic exceptions | Numeric results under 18, results excluded for a sex, or results without a matching band receive the applicable finding instead of an unsuitable range comparison. | [Rules](../src/lab_intake/rules.py), [rule tests](../tests/test_rules.py). |
| Units | In 1.1.0, numeric results with blank or mismatched units receive `UNEXPECTED_UNIT`. Matching is exact and case-sensitive. Values are retained without conversion; independent rules still run. | [Unit-rule PR](https://github.com/AbdulMB-Online/lab-result-intake-flagging/pull/16), [rules](../src/lab_intake/rules.py). |
| Missing values and results | Keep recognized no-value markers with `MISSING_VALUE`. Flag unmatched orders within the input batch as `MISSING_RESULT`. | [Loader](../src/lab_intake/loader.py), [rules](../src/lab_intake/rules.py). |
| Corrections | Ignore identical repeated results. For changed fields on an existing ID, retain the previous version in `result_revisions` and update the current result. | S6; [storage](../src/lab_intake/storage.py), [storage tests](../tests/test_storage.py). |
| Report access and email | Retrieve saved checks without another import. Print reports before optional email; retain output after a handled SMTP failure. | S6; [CLI](../src/lab_intake/cli.py), [mail tests](../tests/test_mailer.py). |
| Retention | Keep patient records for at least ten years. Deletion requires case-by-case authorization, review, and approval. The application does not automatically delete records. | S6; [CLI commands](../src/lab_intake/cli.py). |
| Inactivity | List patients whose latest stored collection date is at least ten years old. Exclude patients without stored results. Listing does not delete records. | S6; [storage](../src/lab_intake/storage.py), [CLI](../src/lab_intake/cli.py). |
| Backup and recovery | Use `Connection.backup`, integrity and foreign-key checks, and a fresh restore destination. Verify the restored copy before switching the working database. | [Backup implementation](../src/lab_intake/backup.py), [recovery tests](../tests/test_backup.py), [release validation](https://github.com/AbdulMB-Online/lab-result-intake-flagging/releases/tag/v1.1.0). |
| Availability and updates | Prioritize continued access. Persistent volumes, saved reports, retained settings, and verified rollback support recovery; uninterrupted updates have not been verified. | S6; [release validation](https://github.com/AbdulMB-Online/lab-result-intake-flagging/releases/tag/v1.1.0). |

## Customer questions and recorded answers

Source: S6, Team 5 post-meeting notes dated 28 October 2026.

| ID | Question | Client response | Resulting decision |
|---|---|---|---|
| Q1 | Are age/gender-specific ranges needed? | Yes, with configurable upper and lower thresholds. | Implement demographic settings. |
| Q2 | Should history retain changes to an existing result? | Retain the current ID and its changes. | Store the current result and previous revisions. |
| Q3 | Which retention and deletion policy applies? | At least ten years, with case-by-case authorization, review, and approval before deletion. | Retain records without automatic deletion. |
| Q4 | May access pause during updates? | Downtime cannot be afforded; access is the priority. | Preserve stored history and reporting fallbacks; record uninterrupted access as the operational requirement. |
