
# Changelog

## [1.1.0] - 2026-11-01

### Added
- `UNEXPECTED_UNIT` flags numeric results with blank units or units that do not exactly match the settings.
- Unit checks preserve the reported value and run alongside existing rules, allowing multiple findings for one result.
- Synthetic examples cover a mismatched glucose unit and a missing potassium unit.
- Tests cover matching, mismatched, and blank units, missing values, and combinations with other rules.

## [1.0.0] - 2026-10-30

### Added
- CSV intake with rejected-row reasons.
- Age- and sex-based reference ranges with fixed borderline bands.
- Flags for abnormal, missing, and unsupported-demographic results.
- SQLite storage for results, checks, and correction history.
- Saved reports, patient history, and inactive-patient reporting.
- Optional report email with printed output retained.
- Docker packaging with persistent database storage.
- Verified database backup and restore.
- Automated tests and continuous integration.

### Known limitations
- Historical reports can display corrected values beside original flag messages.
- The no-flags summary can imply all results passed even when rows were rejected.
- Unexpected-unit detection is planned for version 1.1.0.
