# Municipal carry-fee relief — review branch

`nj_carry_fee_relief.json` is the sole authored tracker dataset. It is separate from the root production `nj_legal_updates.json`, which this branch does not modify. The iOS consumer and WordPress plugin both use `https://mattymigs.github.io/carryaware-data/nj_carry_fee_relief.json` after approval. No copy belongs in mignonelabs-data or the app bundle; the isolated simulator preview copies a build fixture only.

The draft alert is `review/alerts/point-pleasant-carry-fee-refund-2026-09-21.json`. It uses the backwards-compatible `legal` type with `permit_fee_update` subtype, category `Permit & Licensing Update`, and three HTTPS actions. Old app versions ignore actions and retain the official agenda source. Multi-action presentation needs the corresponding iOS review branch.

## Evidence policy

- Initial reported full/substantial relief classification uses the owner's requested 18-town list, checked against NRA-ILA's March 5, 2026 report. Advocacy reporting is explicitly labeled; it is not an official municipal source. Unknown amounts, effective dates, and procedures are null/unverified rather than inferred.
- Wall's current police instructions establish $150 relief, eligible municipal payments from February 11, 2026, and receipt/submission requirements. Its refund form link currently points to a placeholder, so instructions direct users to the firearms unit.
- Berkeley's official May 18 minutes contain certified Resolution 2026-219-R on PDF page 41: $100 refunds upon request for future applications and renewals from the resolution date, leaving $50 municipal cost. The official police fee page corroborates $50. There is no documented general retroactive refund window in that resolution.
- Point Pleasant Borough (NJ code 1525) remains `announced_pending_documents`. Its agenda is not proof of adoption. Point Pleasant Beach Borough (1526) is distinct and is not included as a relief municipality. The September 23 review of the meeting archive did not locate the final September 21 resolution or approved minutes.
- `verified_at` means editorial review of the cited evidence, not a claim that municipal staff confirmed current operations. `last_checked_at` records the source check. Automated checks never update either editorial date or a verified status.

The 564-record official NJ GIS registry in `schemas/nj_municipalities.json` supplies canonical names, counties, legal types, and codes. Matches never collapse Borough and Beach or guess a county for an ambiguous name.

The website presents `verified_full_or_substantial` as “Reported full/substantial relief”. The legacy machine identifier remains unchanged for compatibility with the iOS review; it is not a claim of individually verified municipal documents. The website labels the review timestamp “Evidence last reviewed”.

## Weekly review workflow

`.github/workflows/municipal-fee-review.yml` schedules Monday at 13:17 UTC after merge to the default branch. It is prepared, not activated by this local branch. It has read-only contents permission, no cloud credentials, no feed write, no publish, and no notification send. Each run uploads `candidates.json` and `review-report.md` as a 90-day Actions artifact and writes the report to the run summary. The next run downloads the last successful scheduled run’s artifact to preserve previous source fingerprints and original discovery dates (within the 90-day retention window). It uses read-only Actions access. These observations are untrusted and are never used to verify or publish anything. Before the first scheduled run, every fetched source is reported as a new baseline.

The monitor checks known sources, current record evidence URLs, and bounded same-host linked agenda/minute/resolution/fee pages. It detects changed fingerprints, failed requests, unreadable scans, new names, duplicates, ambiguity, and editorial checks older than 90 days. An excerpt near a name is only a lead; it can describe an unrelated fee or a news dateline. Reviewers must read the source before treating it as evidence. Scanned PDFs retain a fingerprint but require OCR/manual review. There is no claim of exhaustive statewide discovery.

Review procedure: download the artifact; inspect added/changed/broken/stale sections; resolve municipality and county against the registry; obtain municipal documents; verify amounts and effective/retroactive dates; create a manual PR editing only the appropriate records; update verification dates and evidence; run validation; obtain owner review. `CODEOWNERS` requests mattymigs, but GitHub must separately require Code Owner approval on main for a technical merge gate. Repository rules have not been changed.

## Local commands (no publishing)

```sh
python3 -m pip install -r requirements-fee-tracker.txt
python3 scripts/validate_fee_relief.py
python3 -m unittest discover -s tests -p 'test_fee_relief.py' -v
python3 scripts/check_fee_sources.py --output review-output --previous .monitor-state/candidates.json
```

`review/candidates.json` records three additional leads found during research (Kinnelon, Mansfield in Warren, Stafford). They are excluded from the published-data totals until human review.

## Release boundary

Do not merge or push this public repository branch before owner review. GitHub Pages serves the root of main. A feature branch also contains an existing notification sender that can send newly added important alerts when merged to main; it is not imported or executed here. Publishing an important alert therefore requires a fresh workflow audit and explicit notification approval. A first archive-only alert release can set `isImportant` false, keeping it in history without an important popup or sender selection. Never run `update-notifications.mjs send` as a validation command.
