"""Directory expansion and policy-evidence separation regression checks."""
import copy
import json
import sys
import unittest
from pathlib import Path
from jsonschema import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from import_statewide_roster import compare_sources, expand_feed, metadata, parse_gis, REVIEWED_NAME_VARIANTS
from validate_fee_relief import validate, totals

FEED = json.loads((ROOT/'nj_carry_fee_relief.json').read_text())
REGISTRY = json.loads((ROOT/'schemas/nj_municipalities.json').read_text())
ROWS = {r['municipality_code']: r for r in FEED['municipalities']}
UNKNOWN = 'policy_not_yet_verified'


class StatewideRosterTests(unittest.TestCase):
    def test_complete_unique_roster(self):
        self.assertEqual(len(FEED['municipalities']), 564)
        self.assertEqual(len(ROWS), 564)
        self.assertEqual(set(ROWS), {r['municipality_code'] for r in REGISTRY['municipalities']})
        self.assertEqual(len({r['county'] for r in ROWS.values()}), 21)
        validate(FEED)

    def test_sources_agree_on_all_codes_and_counties(self):
        proof = REGISTRY['verification']
        self.assertEqual((proof['primary_count'], proof['secondary_count'], proof['matching_codes'], proof['matching_counties']), (564, 564, 564, 564))
        self.assertEqual(len(proof['reviewed_name_variants']), 5)
        self.assertEqual(sum(proof['county_counts'].values()), 564)
        self.assertEqual(len(proof['county_counts']), 21)

    def test_counts_distinguish_research_from_directory_coverage(self):
        self.assertEqual(totals(FEED), dict(total=564, researched=20, unverified=544,
                         confirmed=2, reported=17, full=18, partial=1, pending=1, counties=21, percent=3.55))

    def test_unknown_records_make_no_policy_claims(self):
        unknown = [r for r in ROWS.values() if r['status'] == UNKNOWN]
        self.assertEqual(len(unknown), 544)
        for row in unknown:
            with self.subTest(code=row['municipality_code']):
                self.assertEqual(row['relief_type'], 'unknown')
                self.assertEqual(row['evidence'], [])
                for key in ('refund_amount', 'net_municipal_cost', 'effective_date', 'retroactive_date',
                            'official_source_url', 'secondary_source_url', 'source_type', 'verified_at', 'last_checked_at'):
                    self.assertIsNone(row[key], key)
                self.assertIn('does not mean', row['notes'])
                self.assertEqual(row['directory_source_url'], FEED['directory_roster']['source_url'])
                self.assertEqual(row['directory_checked_at'], FEED['directory_roster']['checked_at'])

    def test_confirmed_and_reported_evidence_are_separate(self):
        self.assertEqual({r['municipality_code'] for r in ROWS.values() if r['status'].startswith('confirmed_')}, {'1352', '1506'})
        self.assertEqual(ROWS['1352']['refund_amount'], 150)
        self.assertEqual((ROWS['1506']['refund_amount'], ROWS['1506']['net_municipal_cost']), (100, 50))
        reported = [r for r in ROWS.values() if r['status'] == 'reported_full_or_substantial']
        self.assertEqual(len(reported), 17)
        self.assertTrue(all(r['source_type'] == 'advocacy_reporting' for r in reported))

    def test_point_pleasant_and_beach_remain_distinct(self):
        self.assertEqual(ROWS['1525']['municipality'], 'Point Pleasant Borough')
        self.assertEqual(ROWS['1525']['status'], 'announced_pending_documents')
        self.assertIsNone(ROWS['1525']['verified_at'])
        self.assertEqual(ROWS['1526']['municipality'], 'Point Pleasant Beach Borough')
        self.assertEqual(ROWS['1526']['status'], UNKNOWN)

    def test_repeated_names_keep_county_and_type(self):
        franklin = [r for r in ROWS.values() if r['municipality'] in {'Franklin Borough', 'Franklin Township'}]
        self.assertEqual({(r['municipality'], r['county']) for r in franklin}, {
            ('Franklin Borough', 'Sussex'), ('Franklin Township', 'Gloucester'),
            ('Franklin Township', 'Hunterdon'), ('Franklin Township', 'Somerset'), ('Franklin Township', 'Warren')})
        self.assertEqual({r['county'] for r in ROWS.values() if r['municipality'] == 'Mansfield Township'}, {'Burlington', 'Warren'})

    def test_roster_refresh_never_refreshes_policy_dates_or_evidence(self):
        updated = expand_feed(FEED, REGISTRY['municipalities'], metadata('2026-09-25'))
        by_code = {r['municipality_code']: r for r in updated['municipalities']}
        for code, original in ROWS.items():
            result = by_code[code]
            self.assertEqual(result['directory_checked_at'], '2026-09-25')
            self.assertEqual({k:v for k,v in original.items() if k not in {'directory_source_url','directory_checked_at'}},
                             {k:v for k,v in result.items() if k not in {'directory_source_url','directory_checked_at'}})
        self.assertEqual(updated['last_checked_at'], FEED['last_checked_at'])
        self.assertEqual(updated['last_verified_at'], FEED['last_verified_at'])

    def test_legacy_expansion_preserves_every_policy_field(self):
        old = copy.deepcopy(FEED)
        old['schema_version'] = 1
        old['municipalities'] = [r for r in old['municipalities'] if r['status'] != UNKNOWN]
        for row in old['municipalities']:
            if row['status'] in {'confirmed_full_or_substantial','reported_full_or_substantial'}:
                row['status'] = 'verified_full_or_substantial'
            elif row['status'] == 'confirmed_partial':
                row['status'] = 'verified_partial'
            row.pop('directory_source_url'); row.pop('directory_checked_at')
        result = expand_feed(old, REGISTRY['municipalities'], FEED['directory_roster'])
        by_code = {r['municipality_code']: r for r in result['municipalities']}
        self.assertEqual(len(result['municipalities']), 564)
        for row in old['municipalities']:
            for key, value in row.items():
                if key != 'status': self.assertEqual(by_code[row['municipality_code']][key], value)

    def test_unknown_cannot_borrow_directory_date_or_zero_refund(self):
        for key, value in [('last_checked_at', FEED['directory_roster']['checked_at']), ('verified_at', '2026-09-24'),
                           ('refund_amount', 0), ('official_source_url', FEED['directory_roster']['source_url'])]:
            bad = copy.deepcopy(FEED)
            next(r for r in bad['municipalities'] if r['status'] == UNKNOWN)[key] = value
            with self.subTest(key=key), self.assertRaises((AssertionError, ValidationError)):
                validate(bad)

    def test_advocacy_cannot_be_marked_confirmed(self):
        bad = copy.deepcopy(FEED)
        next(r for r in bad['municipalities'] if r['status'] == 'reported_full_or_substantial')['status'] = 'confirmed_full_or_substantial'
        with self.assertRaises((AssertionError, ValidationError)):
            validate(bad)

    def test_incomplete_roster_is_rejected(self):
        bad = copy.deepcopy(FEED); bad['municipalities'].pop()
        with self.assertRaises((AssertionError, ValidationError)):
            validate(bad)

    def test_gis_truncation_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'truncated'):
            parse_gis(json.dumps({'exceededTransferLimit':True, 'features':[]}))

    def test_source_county_disagreement_is_rejected(self):
        secondary = {r['municipality_code']: dict(municipality=r['municipality'],county=r['county']) for r in REGISTRY['municipalities']}
        secondary['1525']['county'] = 'Monmouth'
        with self.assertRaisesRegex(ValueError, 'counties disagree'):
            compare_sources(REGISTRY['municipalities'], secondary)


if __name__ == '__main__':
    unittest.main()
