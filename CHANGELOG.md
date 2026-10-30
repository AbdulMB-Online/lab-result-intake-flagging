
# Changelog

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
