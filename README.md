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
- [OpenStreetMap](https://www.openstreetmap.org/) for categories without one suitable statewide government
  coordinate feed, including parks, beaches, theaters, casinos, racetracks,
  alcohol-serving locations, transit, energy and federal facilities.

OpenStreetMap data is © OpenStreetMap contributors and available under the
[Open Database License](https://www.openstreetmap.org/copyright).

## Rebuild

```bash
python scripts/refresh_restricted_places.py
```

For a repeatable local run, first save the Overpass response and NJ DOH workbook,
then pass `--osm-file` and `--ltc-file`.

## Important limitations

Markers are safety prompts, not legal boundary determinations. Some restrictions
depend on ownership, government designation, event timing, a temporary use, or
posted rules. Film/television locations and the public-open private-property
default are kept out of the active v2 feed while the identified federal
injunction remains operative. Users must verify current law, court orders,
signage and exact property boundaries. CarryAwareNJ does not provide legal advice.
