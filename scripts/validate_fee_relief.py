"""Validate editorial data without loading notification or publishing code."""
import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit
from jsonschema import Draft202012Validator, FormatChecker
ROOT = Path(__file__).resolve().parents[1]
STATUSES = {'confirmed_full_or_substantial','confirmed_partial','reported_full_or_substantial',
            'announced_pending_documents','policy_not_yet_verified','under_consideration','inactive_or_repealed'}
UNKNOWN_STATUS = 'policy_not_yet_verified'
CONFIRMED_STATUSES = {'confirmed_full_or_substantial', 'confirmed_partial'}
OFFICIAL_POLICY_SOURCES = {'official_resolution', 'official_minutes', 'official_police', 'official_notice'}

def normalize(value):
    value = re.sub(r'\bboro\b', 'borough', value.lower())
    value = re.sub(r'\btwp\b', 'township', value)
    return re.sub(r'[^a-z0-9 ]', '', value).strip()

def https(value):
    p = urlsplit(value)
    return p.scheme == 'https' and bool(p.hostname) and not p.username and not p.password and not re.search(r'\s',value)

def totals(feed):
    rows = feed['municipalities']
    researched = sum(r['status'] != UNKNOWN_STATUS for r in rows)
    return dict(total=len(rows), researched=researched, unverified=len(rows)-researched,
                confirmed=sum(r['status'] in CONFIRMED_STATUSES for r in rows),
                reported=sum(r['status']=='reported_full_or_substantial' for r in rows),
                full=sum(r['status'] in {'confirmed_full_or_substantial','reported_full_or_substantial'} for r in rows),
                partial=sum(r['status']=='confirmed_partial' for r in rows),
                pending=sum(r['status'] in {'announced_pending_documents','under_consideration'} for r in rows),
                counties=len({r['county'] for r in rows}), percent=round(100*researched/feed['state_municipality_count'],2))

def validate(feed, production=None, drafts=()):
    # Keep duplicate errors actionable even when a duplicate also exceeds the
    # complete-roster array length constraint.
    if isinstance(feed,dict) and isinstance(feed.get('municipalities'),list):
        codes = [r.get('municipality_code') for r in feed['municipalities']
                 if isinstance(r,dict) and isinstance(r.get('municipality_code'),str)]
        assert len(codes) == len(set(codes)), 'Duplicate municipality'
    schema = json.loads((ROOT/'schemas/nj_carry_fee_relief.schema.json').read_text())
    Draft202012Validator(schema,format_checker=FormatChecker()).validate(feed)
    directory = json.loads((ROOT/'schemas/nj_municipalities.json').read_text())
    registry = directory['municipalities']
    by_code = {r['municipality_code']:r for r in registry}
    assert len(by_code) == len(registry) == feed['state_municipality_count'] == 564
    assert len({r['county'] for r in registry}) == 21
    assert feed['directory_roster']['source_url'] == directory['source_url']
    assert feed['directory_roster']['checked_at'] == directory['checked_at']
    seen = set()
    for r in feed['municipalities']:
        code = r['municipality_code']
        assert code not in seen, f'Duplicate municipality: {code}'
        seen.add(code)
        assert code in by_code, f'Unknown municipality code: {code}'
        for key in ('municipality','county','municipality_type'):
            assert r[key] == by_code[code][key], f'Noncanonical {key}: {r[key]}'
        assert r['directory_source_url'] == feed['directory_roster']['source_url']
        assert r['directory_checked_at'] == feed['directory_roster']['checked_at']
        if r['status'] in CONFIRMED_STATUSES or r['status']=='reported_full_or_substantial':
            assert r['verified_at'], f'Missing verification date: {code}'
        if r['status']=='announced_pending_documents':
            assert r['verified_at'] is None, f'Pending is not verified: {code}'
        if r['status']==UNKNOWN_STATUS:
            assert not r['evidence'], f'Unresearched municipality has policy evidence: {code}'
            for key in ('refund_amount','net_municipal_cost','effective_date','retroactive_date','verified_at','last_checked_at','official_source_url','secondary_source_url','source_type'):
                assert r[key] is None, f'Unresearched municipality has policy {key}: {code}'
        else:
            assert r['official_source_url'] or r['secondary_source_url'], f'Missing policy source: {code}'
        if r['source_type'] and r['source_type'].startswith('official_'):
            assert r['official_source_url'], f'Missing official source: {code}'
        if r['source_type']=='advocacy_reporting':
            assert r['secondary_source_url'], f'Missing advocacy attribution: {code}'
        if r['status'] in CONFIRMED_STATUSES:
            assert r['source_type'] in OFFICIAL_POLICY_SOURCES, f'Confirmation requires municipal evidence: {code}'
            assert any(e['source_type'] in OFFICIAL_POLICY_SOURCES and e['verified_at'] for e in r['evidence']), f'Missing reviewed municipal policy evidence: {code}'
        if r['refund_amount'] is not None and r['net_municipal_cost'] is not None:
            assert r['refund_amount'] + r['net_municipal_cost'] == r['statutory_municipal_portion']
        for key in ('effective_date','retroactive_date','verified_at','last_checked_at'):
            if r[key]: date.fromisoformat(r[key])
        if r['verified_at']:
            assert r['verified_at'] <= r['last_checked_at'] <= feed['last_checked_at']
    assert seen == set(by_code), 'Tracker must contain the complete canonical statewide roster'
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
