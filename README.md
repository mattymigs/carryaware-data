# CarryAwareNJ data

Public, county-partitioned restricted-place data used by CarryAwareNJ.

## Dataset generations

- `nj_county_manifest.json` and `data/*.json` are the frozen v1 feed used by
  released builds that understand the original six place types.
- `nj_county_manifest_v2.json` and `data/v2/*.json` are the expanded feed.
  Keeping a separate manifest prevents an older app from failing when it sees a
  new enum value.
- `nj_restricted_place_categories_v2.json` records the statutory category,
  current court-treatment assumptions, mapping limitations and app type mapping.
- `nj_dataset_summary_v2.json` contains generated statewide and county counts.

## Sources

The v2 builder merges and deduplicates:

- existing CarryAwareNJ verified school, court, government, airport, transit and
  college records;
- [New Jersey Department of Children and Families licensed child care centers](https://data.nj.gov/Reference-Data/Licensed-Child-Care-Centers/cru5-4rmm/about_data);
- [New Jersey Department of Health acute-care facilities](https://data.nj.gov/Health/Acute-Care-Facilities/mrzk-zrvp/about_data) and
  [long-term-care facilities](https://healthapps.nj.gov/facilities/acFacilityList.aspx);
- [New Jersey Cannabis Regulatory Commission dispensary listings](https://www.nj.gov/cannabis/dispensaries/find/); and
- [New Jersey Division of Alcoholic Beverage Control active retail-license listings](https://www.njoag.gov/about/divisions-and-offices/division-of-alcoholic-beverage-control-home/licensing-bureau-applications-and-information/licensing-reports/)
  for licensed on-premises alcohol locations; and
- [OpenStreetMap](https://www.openstreetmap.org/) for categories without one suitable statewide government
  coordinate feed, including parks, beaches, theaters, casinos, racetracks,
  supplemental alcohol-serving locations, transit, energy and federal facilities.

The ABC import includes active, addressable municipal license codes 31, 32, 33,
34, 35, 36 and 37, which the State's [license-type reference](https://nj.gov/oag/abc/downloads/2023-0109_License-Types.pdf)
identifies as authorizing on-premises consumption. It omits licenses
with an inactivity start date and distribution-only codes 43 and 44. The dated
`ABC_RETAIL_URL` in the builder should be updated whenever ABC publishes a new
retail-license workbook. This municipal retail workbook does not include every
separately state-issued on-premises class (for example, certain brewery,
winery, transit or state-concession licenses), so those remain a documented
future source expansion rather than being inferred from business categories.

OpenStreetMap data is © OpenStreetMap contributors and available under the
[Open Database License](https://www.openstreetmap.org/copyright).

## Rebuild

```bash
python scripts/refresh_restricted_places.py \
  --abc-only \
  --abc-retail-file /path/to/RETAIL-LICENSE-REPORT.xlsx
```

`--abc-only` is the targeted July 2026 hotfix path: it layers reviewed official
ABC rows onto the current v2 datasets without refreshing or removing unrelated
places. Do not use it as a general source-removal reconciliation: a later
monthly ABC update that removes licenses requires a reviewed full-source rebuild
so any underlying OpenStreetMap marker can be reconsidered (the pre-hotfix
baseline remains recoverable from Git history).

For a repeatable full-source run, first save the Overpass response and NJ DOH
workbook, then pass `--osm-file` and `--ltc-file` and omit `--abc-only`.
The reviewed provider-aware cache at
`data_sources/nj_abc_geocodes_2026-07.json` is keyed
by license number, premise address and municipality, so a moved license is
automatically geocoded again while accepted unchanged premises are not
re-geocoded.
Each rebuild also writes `nj_abc_unmatched_v2.json`, which records parser audit
counts and every otherwise-eligible license that lacks an address or did not
receive a conservatively accepted geocode. Coordinates are also checked against
the county encoded in the official license number. Review that report instead
of silently dropping or misplacing those premises. Keep the workbook date,
download URL and reviewed SHA-256 together in the builder before each update.
The companion `data_sources/nj_abc_geocode_review_2026-07.json` preserves the
provider results and explicit rejection reasons for addressable licenses that
did not meet the release quality gate; release provenance pins both artifacts
by SHA-256.

An exploratory Census refresh must use an explicit, separate cache/report pair:

```bash
python scripts/refresh_restricted_places.py \
  --abc-retail-file /path/to/RETAIL-LICENSE-REPORT.xlsx \
  --refresh-abc-geocodes \
  --abc-geocode-cache /tmp/nj-abc-refresh-cache.json \
  --abc-unmatched-report /tmp/nj-abc-refresh-review.json
```

Refresh mode refuses to overwrite the pinned release cache or published
unmatched report, and does not attach the prior release's NJGIN review
provenance to new results. It writes only the explicit staging cache/report
pair outside the repository; county datasets, their manifest and release
summary are left unchanged.
Review and freeze a refreshed cache/rejection pair before changing the release
constants or published datasets.

## Important limitations

Markers are safety prompts, not legal boundary determinations. Some restrictions
depend on ownership, government designation, event timing, a temporary use, or
posted rules. Film/television locations and the public-open private-property
default are kept out of the active v2 feed while the identified federal
injunction remains operative. Users must verify current law, court orders,
signage and exact property boundaries. CarryAwareNJ does not provide legal advice.
