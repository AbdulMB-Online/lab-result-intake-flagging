# Client brief: Lab Result Intake and Flagging

**Team 5 · Release 1.1.0 · 1 November 2026**

## Users and terminology — Ansh Harjai

The clinic manager oversees the workflow, lab staff supply results, and nurses
and clinicians review them. An **order** requests a test; a **result** reports a
measurement; a **unit** supplies its scale; a **reference range** supplies
comparison limits. A **flag** identifies a finding for review. The clinician
interprets the result. MedlinePlus explains that ranges vary by patient group
and laboratory and that measurements use different units. These requirements
support configurable age/sex bands and explicit unit checks. [S1, S2, S6]

## Workflow and problems — Neeraj Pankhania

The clinic sends blood samples to an outside lab and reviews returned files
line by line. Garbled values, absent results, and inaccessible history make
review difficult. The client requires current results with correction history,
reliable report access, and fallback reporting. SAFER guidance identifies risks
from mishandled results and recommends clear displays and follow-up
responsibility. The tool separates rejected records from accepted results,
records rule findings, and retrieves saved reports and patient history.
[S1, S3, S6]

## Privacy, records, and continuity — Abdul Malik

The client requires **at least ten years of patient-record retention**, with
case-by-case authorization, review, and approval before deletion. The tool
retains stored records; its ten-year inactivity report only lists patients.
This retention period is a clinic requirement. For HIPAA-regulated entities,
HHS describes safeguards and contingency planning; its six-year documentation
period is distinct from medical-record retention. [S4, S5, S6]

The client prioritizes continued access during software changes. Persistent
storage, saved-report retrieval, verified backup/restore, and rollback support
that requirement. The completed checks verify recovery and preservation of
the tested history; uninterrupted access during updates has not been verified.
See the [1.1.0 release validation](https://github.com/AbdulMB-Online/lab-result-intake-flagging/releases/tag/v1.1.0).

## Delivered solution

The Docker-packaged CLI validates synthetic CSV files, flags findings, and
stores results and checks in SQLite. Version **1.0.0** provides seven rules,
correction history, reports, patient history, inactivity reporting, optional
email, and recovery. **1.1.0** adds exact unit matching without conversion.
Both releases are published. See the [decision log](assumptions_log.md).

## Sources

The named sections follow the research areas assigned in S1.

- **S1:** Instructor, *Team 5: Lab Result Intake and Flagging*, Week 3 project brief.
- **S2:** [NLM/MedlinePlus, How to Understand Your Lab Results](https://medlineplus.gov/lab-tests/how-to-understand-your-lab-results/), “What is a reference range?”
- **S3:** [SAFER, Test Results Reporting and Follow-Up](https://healthit.gov/wp-content/uploads/2025/06/SAFER-Guide-8.-Test-Results-Reporting-Final.pdf), August 2024 edition, practices 2.4–2.7, printed pp. 15–18.
- **S4:** [HHS, Summary of the HIPAA Security Rule](https://www.hhs.gov/hipaa/for-professionals/security/laws-regulations/index.html), contingency planning and documentation.
- **S5:** [HHS, Medical-record retention FAQ](https://www.hhs.gov/hipaa/for-professionals/faq/does-hipaa-require-covered-entities-to-keep-medical-records-for-any-period/index.html).
- **S6:** Team 5, *Client requirements*, post-meeting notes dated 28 October 2026: demographic ranges, correction history, retention, availability, and reporting requirements.
