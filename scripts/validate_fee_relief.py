"""Validate editorial data without loading notification or publishing code."""
import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit
from jsonschema import Draft202012Validator, FormatChecker
ROOT = Path(__file__).resolve().parents[1]
STATUSES = {'verified_full_or_substantial','verified_partial','announced_pending_documents','under_consideration','inactive_or_repealed'}

def normalize(value):
    value = re.sub(r'\bboro\b', 'borough', value.lower())
    value = re.sub(r'\btwp\b', 'township', value)
    return re.sub(r'[^a-z0-9 ]', '', value).strip()

def https(value):
    p = urlsplit(value)
    return p.scheme == 'https' and bool(p.hostname) and not p.username and not p.password and not re.search(r'\s',value)

def totals(feed):
    rows = feed['municipalities']
    return dict(total=len(rows), full=sum(r['status']=='verified_full_or_substantial' for r in rows),
                partial=sum(r['status']=='verified_partial' for r in rows),
                pending=sum(r['status'] in {'announced_pending_documents','under_consideration'} for r in rows),
                counties=len({r['county'] for r in rows}), percent=round(100*len(rows)/feed['state_municipality_count'],2))

def validate(feed, production=None, drafts=()):
    schema = json.loads((ROOT/'schemas/nj_carry_fee_relief.schema.json').read_text())
    Draft202012Validator(schema,format_checker=FormatChecker()).validate(feed)
    registry = json.loads((ROOT/'schemas/nj_municipalities.json').read_text())['municipalities']
    by_code = {r['municipality_code']:r for r in registry}
    seen = set()
    for r in feed['municipalities']:
        code = r['municipality_code']
        assert code not in seen, f'Duplicate municipality: {code}'
        seen.add(code)
        assert code in by_code, f'Unknown municipality code: {code}'
        for key in ('municipality','county','municipality_type'):
            assert r[key] == by_code[code][key], f'Noncanonical {key}: {r[key]}'
        if r['status'].startswith('verified_'):
            assert r['verified_at'], f'Missing verification date: {code}'
        if r['status']=='announced_pending_documents':
            assert r['verified_at'] is None, f'Pending is not verified: {code}'
        assert r['official_source_url'] or r['secondary_source_url']
        if r['source_type'].startswith('official_'):
            assert r['official_source_url'], f'Missing official source: {code}'
        if r['source_type']=='advocacy_reporting':
            assert r['secondary_source_url'], f'Missing advocacy attribution: {code}'
        if r['refund_amount'] is not None and r['net_municipal_cost'] is not None:
            assert r['refund_amount'] + r['net_municipal_cost'] == r['statutory_municipal_portion']
        for key in ('effective_date','retroactive_date','verified_at','last_checked_at'):
            if r[key]: date.fromisoformat(r[key])
        if r['verified_at']:
            assert r['verified_at'] <= r['last_checked_at'] <= feed['last_checked_at']
    def urls(x):
        if isinstance(x,dict):
            for k,v in x.items():
                if (k.endswith('_url') or k=='url') and v is not None: assert https(v),f'Invalid HTTPS URL: {v}'
                urls(v)
        if isinstance(x,list):
            for v in x: urls(v)
    urls(feed)
    updates = list((production or {}).get('updates',[])) + list(drafts)
    ids = set()
    for u in updates:
        assert u['id'] not in ids,f'Duplicate alert ID: {u["id"]}'
        ids.add(u['id'])
        date.fromisoformat(u['date'])
        assert https(u['sourceURL'])
        for a in u.get('actions',[]): assert a['title'].strip() and https(a['url'])
        if u.get('subtype')=='permit_fee_update':
            assert u['type']=='legal' and u['category']=='Permit & Licensing Update'
            assert len(u['actions'])==3
    return totals(feed)

if __name__=='__main__':
    feed=json.loads((ROOT/'nj_carry_fee_relief.json').read_text())
    production=json.loads((ROOT/'nj_legal_updates.json').read_text())
    drafts=[json.loads(p.read_text()) for p in (ROOT/'review/alerts').glob('*.json')]
    print(json.dumps(validate(feed,production,drafts),indent=2))
