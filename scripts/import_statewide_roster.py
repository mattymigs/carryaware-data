"""Refresh NJ directory identities without researching or changing carry-fee policy.

By default this fetches two official sources and prints the comparison. --write
updates only the local registry/tracker. Nothing publishes or sends alerts.
"""
import argparse
import copy
import hashlib
import io
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
PRIMARY = 'https://maps.nj.gov/arcgis/rest/services/Framework/Government_Boundaries/MapServer/2'
QUERY = PRIMARY + '/query?' + urlencode(dict(where='1=1', outFields='MUN,COUNTY,MUN_LABEL,MUN_TYPE,NAME,MUN_CODE', returnGeometry='false', f='json'))
SECONDARY = 'https://www.nj.gov/treasury/taxation/pdf/25-anc-1ins.pdf'
SOURCE_FILES = {'gis-layer.json': PRIMARY+'?f=pjson', 'gis-municipalities.json': QUERY,
                'nj-2025-anc1-instructions.pdf': SECONDARY}
# Differences in the two official directories, reviewed by code and county.
# These are directory aliases, not evidence of a carry-fee policy.
REVIEWED_NAME_VARIANTS = {
    '0703': ('Caldwell Borough', 'Caldwell Borough Township'),
    '0706': ('Essex Fells Borough', 'Essex Fells Township'),
    '0717': ('City of Orange Township', 'Orange City'),
    '0719': ('South Orange Village', 'South Orange Village Twp.'),
    '1815': ('Peapack-Gladstone Borough', 'Peapack & Gladstone Bor.'),
}


def normalized_name(value):
    value = value.lower()
    for short, long in [('bor', 'borough'), ('boro', 'borough'), ('twp', 'township'), ('mt', 'mount'), ('pt', 'point')]:
        value = re.sub(r'\b' + short + r'\b\.?', long, value)
    return re.sub(r'[^a-z]', '', value)


def parse_gis(raw):
    document = json.loads(raw)
    if document.get('error') or document.get('exceededTransferLimit'):
        raise ValueError('GIS error or truncated roster; do not replace the directory')
    rows = []
    for feature in document['features']:
        r = feature['attributes']
        rows.append(dict(municipality=r['MUN_LABEL'], municipality_type=r['MUN_TYPE'],
                         county=r['COUNTY'].title(), municipality_code=r['MUN_CODE']))
    codes = [r['municipality_code'] for r in rows]
    if len(rows) != 564 or len(set(codes)) != 564 or any(not re.fullmatch(r'\d{4}', c) for c in codes):
        raise ValueError('Expected 564 unique four-digit NJ municipality codes; review any roster change manually')
    if len({r['county'] for r in rows}) != 21:
        raise ValueError('Expected 21 counties')
    return sorted(rows, key=lambda r: r['municipality_code'])


def parse_tax_roster(raw):
    """Read only the code appendix; derive county prefixes from its own headings.

    PDF extraction order can cross columns. Each county heading's first coded
    row establishes that county's prefix, after which all rows are matched by
    the explicit four-digit code. No county is inferred from the GIS input.
    """
    pages = [p.extract_text() or '' for p in PdfReader(io.BytesIO(raw)).pages]
    text = '\n'.join(p for p in pages if '2025 County/Municipality Codes' in p)
    rows, county_by_prefix, pending_county = {}, {}, None
    for line in text.splitlines():
        heading = re.fullmatch(r'([A-Z ]+) COUNTY', line.strip())
        if heading:
            pending_county = heading.group(1).title()
            continue
        match = re.fullmatch(r'(.+?)\s+(\d{4})', line.strip())
        if not match:
            continue
        name, code = match.groups()
        if code in rows:
            raise ValueError('Duplicate municipality in secondary directory: ' + code)
        rows[code] = name
        if pending_county:
            prefix = code[:2]
            if prefix in county_by_prefix and county_by_prefix[prefix] != pending_county:
                raise ValueError('Conflicting secondary county headings')
            county_by_prefix[prefix] = pending_county
            pending_county = None
    if len(rows) != 564 or len(county_by_prefix) != 21:
        raise ValueError('Secondary directory did not yield 564 municipalities and 21 counties')
    return {c: dict(municipality=n, county=county_by_prefix[c[:2]]) for c, n in rows.items()}


def compare_sources(rows, secondary):
    codes = {r['municipality_code'] for r in rows}
    if codes != set(secondary):
        raise ValueError('Official source municipality codes disagree')
    variants = []
    for row in rows:
        code = row['municipality_code']
        other = secondary[code]
        if row['county'] != other['county']:
            raise ValueError('Official source counties disagree: ' + code)
        names = (row['municipality'], other['municipality'])
        if normalized_name(names[0]) != normalized_name(names[1]) or REVIEWED_NAME_VARIANTS.get(code) == names:
            if REVIEWED_NAME_VARIANTS.get(code) != names:
                raise ValueError('Unreviewed official-source name difference: ' + code + ' ' + repr(names))
            variants.append(dict(municipality_code=code, county=row['county'], gis_name=names[0], taxation_name=names[1]))
    return dict(primary_count=len(rows), secondary_count=len(secondary), matching_codes=len(codes),
                matching_counties=len(rows), county_count=21, reviewed_name_variants=variants,
                county_counts=dict(sorted(Counter(r['county'] for r in rows).items())))


def metadata(checked_at):
    date.fromisoformat(checked_at)
    return dict(source_title='NJ Office of Geographic Information Systems municipal boundary directory',
                source_url=PRIMARY, query_url=QUERY,
                secondary_source_title='NJ Division of Taxation 2025 ANC-1 county/municipality code appendix',
                secondary_source_url=SECONDARY, checked_at=checked_at,
                municipality_count=564, county_count=21,
                notes='All 564 four-digit codes and county assignments match the two official sources. Five reviewed display-name variants are recorded in the registry; GIS canonical names are retained. This checks directory identity only, not any carry-fee policy. Taxation codes are used here as an identity cross-check, not tax advice.')


def expand_feed(feed, rows, roster_metadata):
    result = copy.deepcopy(feed)
    existing = {r['municipality_code']: r for r in result['municipalities']}
    if len(existing) != len(result['municipalities']):
        raise ValueError('Duplicate existing policy record')
    if not set(existing).issubset({r['municipality_code'] for r in rows}):
        raise ValueError('Existing policy identity disappeared from the roster; manual review required')
    expanded = []
    for identity in rows:
        code = identity['municipality_code']
        if code in existing:
            row = existing[code]
            for key in ('municipality', 'municipality_type', 'county'):
                if row[key] != identity[key]:
                    raise ValueError('Existing policy identity changed: ' + code + ' ' + key)
            if row['status'] == 'verified_full_or_substantial':
                row['status'] = 'confirmed_full_or_substantial' if code == '1352' else 'reported_full_or_substantial'
            elif row['status'] == 'verified_partial':
                row['status'] = 'confirmed_partial' if code == '1506' else row['status']
        else:
            row = dict(identity, status='policy_not_yet_verified', relief_type='unknown',
                       statutory_municipal_portion=150, refund_amount=None, net_municipal_cost=None,
                       effective_date=None, retroactive_date=None,
                       eligibility_summary='Carry-permit fee-relief policy has not yet been verified for this municipality.',
                       application_instructions='Contact the municipal clerk or police firearms unit for current fees and any refund procedure.',
                       official_source_url=None, secondary_source_url=None, source_type=None,
                       verified_at=None, last_checked_at=None,
                       notes='Included in the statewide municipality directory. No carry-fee policy research is recorded here yet; this does not mean the municipality offers no relief.', evidence=[])
        row['directory_source_url'] = roster_metadata['source_url']
        row['directory_checked_at'] = roster_metadata['checked_at']
        expanded.append(row)
    result['schema_version'] = 2
    result['directory_roster'] = roster_metadata
    result['verification_policy'] = ('Directory identity and carry-fee policy evidence are separate. Officially confirmed relief requires municipal evidence; reported full/substantial relief is attributed advocacy reporting. Point Pleasant Borough remains pending adopted documents. Policy not yet verified means no policy research is recorded, not that no relief exists. Existing verified_at dates describe editorial evidence review and are preserved; roster checks do not create or refresh policy dates. Automated findings never change policy status or publish alerts. Unknown amounts and dates remain null.')
    result['municipalities'] = sorted(expanded, key=lambda r: (r['municipality'].casefold(), r['county']))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path, help='Replay previously downloaded source files instead of network access')
    parser.add_argument('--evidence-dir', type=Path, help='Save fetched sources and comparison report here')
    parser.add_argument('--checked-at', default=date.today().isoformat())
    parser.add_argument('--write', action='store_true', help='Update local JSON files only after both official sources agree')
    args = parser.parse_args()
    raw = {}
    for filename, url in SOURCE_FILES.items():
        if args.source_dir:
            raw[filename] = (args.source_dir/filename).read_bytes()
        else:
            with urlopen(Request(url, headers={'User-Agent': 'CarryAwareNJ-directory-review/1.0'}), timeout=30) as response:
                raw[filename] = response.read(10_000_001)
            if len(raw[filename]) > 10_000_000:
                raise ValueError('Unexpectedly large roster source')
    layer = json.loads(raw['gis-layer.json'])
    if layer.get('name') != 'Municipalities' or 'Office of Geographic Information Systems' not in layer.get('copyrightText', ''):
        raise ValueError('Unexpected primary source metadata')
    rows = parse_gis(raw['gis-municipalities.json'])
    proof = compare_sources(rows, parse_tax_roster(raw['nj-2025-anc1-instructions.pdf']))
    proof['source_sha256'] = {name: hashlib.sha256(content).hexdigest() for name, content in raw.items()}
    proof['primary_attribution'] = layer['copyrightText']
    roster_metadata = metadata(args.checked_at)
    registry = dict(roster_metadata, verification=proof, municipalities=rows)
    feed_path = ROOT/'nj_carry_fee_relief.json'
    feed = expand_feed(json.loads(feed_path.read_text()), rows, roster_metadata)
    if args.evidence_dir:
        args.evidence_dir.mkdir(parents=True, exist_ok=True)
        for name, content in raw.items():
            (args.evidence_dir/name).write_bytes(content)
        (args.evidence_dir/'source-comparison.json').write_text(json.dumps(registry, indent=2)+'\n')
    if args.write:
        (ROOT/'schemas/nj_municipalities.json').write_text(json.dumps(registry, indent=2, ensure_ascii=False)+'\n')
        feed_path.write_text(json.dumps(feed, indent=2, ensure_ascii=False)+'\n')
    print(json.dumps(dict(proof, checked_at=args.checked_at, policy_status_counts=dict(Counter(r['status'] for r in feed['municipalities'])), local_files_written=args.write), indent=2))


if __name__ == '__main__':
    main()
