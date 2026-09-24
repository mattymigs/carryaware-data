# Statewide municipal carry-fee tracker — review branch

`nj_carry_fee_relief.json` is the sole authored carry-fee policy dataset. Schema version 2 contains all 564 New Jersey municipalities in 21 counties. Directory coverage is not policy verification: 20 entries currently have researched evidence and 544 have no policy research recorded. The root production `nj_legal_updates.json` is separate and unchanged.

This is a local website/data review. The iOS review branch is untouched; schema 2 is not asserted to be compatible with its unfinished tracker model. No app build or merge is required for the existing released app's single-link alert candidate.

## Policy classifications and counts

| Classification | Count | Meaning |
| --- | ---: | --- |
| `confirmed_full_or_substantial` | 1 | Wall, supported by official municipal police guidance. |
| `confirmed_partial` | 1 | Berkeley, supported by an adopted resolution in official minutes. |
| `reported_full_or_substantial` | 17 | Attributed NRA-ILA reporting; local municipal implementation has not been individually verified. |
| `announced_pending_documents` | 1 | Point Pleasant Borough's proposal; adoption remains unverified. |
| `policy_not_yet_verified` | 544 | Directory entry only. No policy research is recorded; this does **not** mean no relief exists. |

The schema also supports `under_consideration` and `inactive_or_repealed` for future evidence-backed updates. All full/substantial entries together total 18; that combined number must not be labeled officially confirmed. Research coverage is 20/564, or 3.55%, and confirmed relief is 2/564. Directory coverage is 564/564. The Python totals helper exposes `total`, `researched`, `unverified`, `confirmed`, `reported`, `full`, `partial`, `pending`, `counties`, and `percent`; `percent` means research coverage.

Every unknown-policy row has `relief_type: "unknown"`; policy amount, net cost, effective/retroactive date, review/check dates, source type, and policy source URLs are null; `evidence` is empty. Directory URLs are stored separately and must not be displayed as proof of a fee policy. Null amounts are not zero-dollar refunds or a claim that the full $150 is collected.

## Municipality directory and provenance

The current [NJ Office of Geographic Information Systems municipal layer](https://maps.nj.gov/arcgis/rest/services/Framework/Government_Boundaries/MapServer/2) was checked September 24, 2026. Its metadata identifies NJOIT/OGIS; the complete non-geographic query returns 564 unique four-digit municipality codes with no transfer-limit truncation. `schemas/nj_municipalities.json` contains canonical GIS names, counties, types, and codes plus source provenance, fingerprints, and the comparison report.

The independent [NJ Division of Taxation 2025 ANC-1 county/municipality appendix](https://www.nj.gov/treasury/taxation/pdf/25-anc-1ins.pdf), PDF pages 8–11 (printed pages 6–9), has the same 564 codes and all 564 county assignments. Five reviewed name variants are retained in the comparison record: Caldwell, Essex Fells, City of Orange, South Orange, and Peapack-Gladstone. GIS display names and types remain canonical; tax-form naming variants do not change identities. Taxation codes are an identity cross-check, not a statement about tax filing requirements.

`directory_roster.checked_at` describes the roster check. Every row separately has `directory_source_url` and `directory_checked_at`. These fields never establish or refresh policy `verified_at` or `last_checked_at`. Existing policy dates remain September 23, 2026; the 544 directory-only rows have neither policy date.

Point Pleasant Borough (1525) and Point Pleasant Beach Borough (1526) are separate entries. Franklin, Washington, Mansfield, and other repeated names remain distinct through their four-digit codes, county, and legal type. Search results must retain that context.

`scripts/import_statewide_roster.py` fetches both official sources, fails closed on missing/duplicate/truncated codes, mismatched counties, and unreviewed name differences, and prints the result by default. `--write` updates only local registry/tracker files after cross-checking. A changed state total requires a deliberate schema review. Re-importing the roster preserves all existing policy fields and evidence; it only refreshes directory provenance and adds genuinely new directory rows as unverified.

## Existing policy evidence retained

- The initial 18-town full/substantial list came from NRA-ILA's March 5, 2026 report. Seventeen rows remain explicitly reported; unknown amounts, dates, and local procedures are not inferred.
- Wall's police guidance supports $150 relief, municipal fees paid on or after February 11, 2026, and receipt/submission requirements. Its refund-form link was a placeholder at the September 23 review, so instructions direct applicants to the firearms unit.
- Berkeley's official May 18 minutes contain certified Resolution 2026-219-R on PDF page 41: a $100 refund upon request for future applications and renewals from the resolution date, leaving $50 municipal cost. The official police fee page corroborates $50. No general retroactive refund window was found in that resolution.
- Point Pleasant Borough remains pending. The September 21 agenda is a proposal, not proof of adoption. The September 23 archive review did not locate an adopted resolution or approved minutes. Point Pleasant Beach's policy is unverified.
- `verified_at` is the preserved editorial review date for the cited evidence. On reported rows it does not mean a municipal document was verified. `last_checked_at` records a human policy-source check. Automation never changes either date, policy status, or live data.

No additional municipal carry-fee claims were researched or promoted during the directory expansion. Kinnelon, Mansfield in Warren, and Stafford remain unverified directory entries, with their earlier candidate leads outside the policy evidence. A review lead is not a verified policy.

## Draft alert

`review/alerts/point-pleasant-carry-fee-refund-2026-09-21.json` uses existing Build 18 fields: type `legal`, category `Permit & Licensing Update`, and `sourceURL` pointing to the planned tracker. The released app's action label remains “View Source”; `sourceTitle` is supporting archive text. The ID, title, body, pending status, dates, credits, and important flag are unchanged by this expansion. The unused `actions` and `subtype` fields were already removed in R2.

`tests/alert-build18-check.mjs` checks the payload against pinned source snapshots independently matched to GitHub commit `8898d4e921d656998c01e8eacf1043d82015bea9`. This does not attest Apple's distributed binary or device/push behavior. The candidate stays outside the production feed, and its destination must be public and usable before any separately approved alert release.

## Weekly review workflow

The proposed workflow is review-only. It has no feed write, publishing step, cloud credentials, or notification send. Its findings and rotating research queue support human review; they do not establish a policy. Directory-only rows are counted separately from researched evidence and must not receive a false stale-policy date from the roster import.

Evidence checks detect source changes, failed requests, unreadable scans, new names, ambiguity, and old editorial policy checks. Name matching preserves county/type ambiguity. A mention near a fee term can be unrelated or a news dateline. Scanned PDFs require manual/OCR review. The monitor does not claim exhaustive statewide discovery.

A reviewer must resolve the municipality by code/county, inspect official documents, establish amounts and dates, and update the individual record with evidence before changing status. Run validation and obtain owner review. `CODEOWNERS` requests review, but required branch protection must be configured separately. None is enabled or modified by this local work.

## Local commands

```sh
python3 -m pip install -r requirements-fee-tracker.txt
python3 scripts/validate_fee_relief.py
python3 -m unittest discover -s tests -p 'test_*fee*.py' -v
python3 -m unittest discover -s tests -p 'test_statewide_*.py' -v
node tests/alert-build18-check.mjs
python3 scripts/import_statewide_roster.py
```

For a deliberate local roster refresh, add `--write --evidence-dir review-output/roster` to the importer. To replay saved sources without networking, use `--source-dir PATH --checked-at YYYY-MM-DD`. The three inputs are `gis-layer.json`, `gis-municipalities.json`, and `nj-2025-anc1-instructions.pdf`. Do not assign a new check date when merely replaying an old source snapshot.

## Release boundary

No push, merge, production feed edit, WordPress publication, or FCM send is authorized by this directory work. GitHub Pages serves main, so merging the dataset would publish it. The separate important-alert sender must be re-audited before any alert release. `isImportant: true` remains only in the unpublished candidate. Never run a notification sender as a validation step. Keep the iOS review branch untouched.
