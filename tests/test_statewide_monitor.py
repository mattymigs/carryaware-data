"""Statewide planning, bounded I/O, ambiguous names, and human-review safety."""
import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from check_fee_sources import check, matches, registered_sources, report
from validate_fee_relief import ROOT

FEED = json.loads((ROOT / 'nj_carry_fee_relief.json').read_text())
REGISTRY = json.loads((ROOT / 'schemas/nj_municipalities.json').read_text())['municipalities']
SOURCES = json.loads((ROOT / 'monitoring/sources.json').read_text())['sources']


def fake_source(url):
    return 'Kinnelon Borough is considering carry permit fee refunds.', [], 'digest'


class StatewideMonitorTests(unittest.TestCase):
    def test_statewide_coverage_is_not_policy_verification(self):
        before = copy.deepcopy(FEED)
        result = check(FEED, SOURCES, REGISTRY, {}, plan_only=True)
        self.assertEqual(result['coverage'], dict(authoritative_municipalities=564,
            directory_municipalities=564, directory_counties=21, directory_coverage_percent=100.0,
            researched_policy_records=20, policy_research_percent=3.55, confirmed_relief=2,
            reported_relief=17, pending_proposals=1, policy_not_yet_verified=544))
        self.assertEqual(len(result['research_queue']), 564)
        self.assertEqual(result['monitoring']['municipalities_with_registered_policy_sources'], 20)
        self.assertEqual(result['monitoring']['municipalities_without_registered_policy_sources'], 544)
        self.assertEqual(FEED, before)

    def test_plan_only_has_no_network_and_leaves_policies_unreviewed(self):
        def forbidden(url):
            self.fail('Planning must not make network requests')
        result = check(FEED, SOURCES, REGISTRY, {}, forbidden, plan_only=True)
        self.assertEqual(result['monitoring']['urls_attempted_this_run'], 0)
        unknown = [row for row in result['research_queue'] if row['current_status'] == 'policy_not_yet_verified']
        self.assertEqual(len(unknown), 544)
        self.assertTrue(all(row['last_policy_reviewed_at'] is None for row in unknown))
        self.assertTrue(all(row['latest_automated_source_check'] is None for row in unknown))
        self.assertTrue(all(row['registered_policy_source_urls'] == [] for row in unknown))
        self.assertIn('A complete directory does not mean every policy has been researched.', report(result))

    def test_rotating_research_batch_and_county_selection(self):
        first = check(FEED, SOURCES, REGISTRY, {}, plan_only=True, batch_size=10)
        second = check(FEED, SOURCES, REGISTRY, first, plan_only=True, batch_size=10)
        codes = lambda result: {row['municipality_code'] for row in result['research_batch']}
        self.assertFalse(codes(first) & codes(second))
        county = check(FEED, SOURCES, REGISTRY, second, plan_only=True, county='atlantic', batch_size=100)
        self.assertTrue(all(row['county'] == 'Atlantic' for row in county['research_batch']))
        self.assertEqual(len(county['research_batch']), 23)
        self.assertEqual(len(county['research_queue']), 564)
        self.assertEqual(county['research_cursors']['statewide'], second['research_cursors']['statewide'])
        with self.assertRaisesRegex(ValueError, 'Unknown county'):
            check(FEED, SOURCES, REGISTRY, {}, plan_only=True, county='Not a county')

    def test_source_requests_capped_and_rotate_without_losing_state(self):
        calls = []
        def fetcher(url):
            calls.append(url)
            return fake_source(url)
        first = check(FEED, SOURCES, REGISTRY, {}, fetcher, '2026-09-24', max_sources=2)
        self.assertEqual(len(calls), 2)
        first_urls = set(calls)
        calls.clear()
        second = check(FEED, SOURCES, REGISTRY, first, fetcher, '2026-09-25', max_sources=2)
        self.assertEqual(len(calls), 2)
        self.assertFalse(first_urls & set(calls))
        self.assertTrue(first_urls <= set(second['observations']))
        self.assertTrue(all(candidate in second['candidates'] for candidate in first['candidates']))
        self.assertTrue(all(candidate['verified_at'] is None and candidate['status'] == 'needs_human_review'
                            for candidate in second['candidates']))

    def test_link_discovery_obeys_budget_and_retains_deferred_links(self):
        calls = []
        def fetcher(url):
            calls.append(url)
            links = ['/minutes-1', '/minutes-2', 'https://unrelated.example/fee', 'javascript:alert(1)']
            return 'A municipal carry permit fee resolution needs human review.', links, 'digest'
        minimal_feed = dict(municipalities=[])
        sources = [dict(url='https://example.com/', source_type='official_agenda', follow_links=True, max_links=2)]
        first = check(minimal_feed, sources, REGISTRY, {}, fetcher, max_sources=1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(first['discovered_sources']), 2)
        self.assertEqual(first['monitoring']['deferred_urls'], 2)
        calls.clear()
        second = check(minimal_feed, sources, REGISTRY, first, fetcher, max_sources=1)
        self.assertEqual(len(calls), 1)
        self.assertNotEqual(calls[0], 'https://example.com/')
        self.assertTrue(all(source['url'].startswith('https://example.com/') for source in second['discovered_sources']))

    def test_previous_discoveries_cannot_bypass_same_host_rule(self):
        previous = dict(discovered_sources=[dict(url='https://unrelated.example/fee',
            parent_url=SOURCES[0]['url'], source_type='advocacy_reporting', follow_links=False)])
        calls = []
        def fetcher(url):
            calls.append(url)
            return fake_source(url)
        result = check(FEED, SOURCES, REGISTRY, previous, fetcher, max_sources=100)
        self.assertNotIn('https://unrelated.example/fee', calls)
        self.assertFalse(result['discovered_sources'])

    def test_global_sources_still_checked_for_unresearched_county(self):
        calls = []
        def fetcher(url):
            calls.append(url)
            return fake_source(url)
        result = check(FEED, SOURCES, REGISTRY, {}, fetcher, county='Atlantic', max_sources=1)
        self.assertEqual(calls, [SOURCES[0]['url']])
        self.assertEqual(result['scope'], 'Atlantic')

    def test_global_followed_sources_remain_available_in_county_rotation(self):
        source = dict(url='https://example.com/', source_type='advocacy_reporting',
                      scope='statewide', municipality_codes=['1506'], follow_links=True)
        empty_feed = dict(municipalities=[])
        calls = []
        def fetcher(url):
            calls.append(url)
            return 'Carry permit fee relief is proposed.', ['/carry-fee-story'], 'digest'
        first = check(empty_feed, [source], REGISTRY, {}, fetcher, county='Atlantic', max_sources=1)
        calls.clear()
        second = check(empty_feed, [source], REGISTRY, first, fetcher, county='Atlantic', max_sources=1)
        self.assertEqual(calls, ['https://example.com/carry-fee-story'])
        self.assertEqual(second['discovered_sources'][0]['scope'], 'statewide')

    def test_new_directory_entry_remains_a_new_candidate(self):
        before = copy.deepcopy(FEED)
        result = check(FEED, SOURCES, REGISTRY, {}, fake_source, '2026-09-24', max_sources=1)
        candidates = result['candidates']
        self.assertTrue(candidates)
        self.assertTrue(all(candidate['kind'] == 'new_candidate' for candidate in candidates))
        self.assertTrue(all(candidate['verified_at'] is None for candidate in candidates))
        self.assertEqual(FEED, before)
        kinnelon = next(row for row in result['research_queue'] if row['municipality_code'] == '1415')
        self.assertEqual(kinnelon['current_status'], 'policy_not_yet_verified')
        self.assertIsNone(kinnelon['last_policy_reviewed_at'])

    def test_unknown_towns_are_not_stale_verified_policies(self):
        result = check(FEED, SOURCES, REGISTRY, {}, fake_source, '2027-01-01', max_sources=1)
        self.assertEqual(len(result['stale']), 20)
        self.assertTrue(all(row['last_policy_reviewed_at'] == '2026-09-23' for row in result['stale']))

    def test_names_do_not_merge_across_counties_or_types(self):
        result = matches('Franklin Township is considering a carry permit fee refund.', REGISTRY)
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]['ambiguous'])
        self.assertGreater(len({row['county'] for row in result[0]['possible_municipalities']}), 1)
        point = matches('Point Pleasant Beach Borough considers a carry permit fee refund.', REGISTRY)
        self.assertEqual({row['municipality_code'] for row in point[0]['possible_municipalities']}, {'1526'})
        self.assertEqual(matches('New Jersey considers carry permit fee relief.', REGISTRY), [])

    def test_registered_official_sources_are_preserved(self):
        sources = registered_sources(FEED, SOURCES)
        by_url = {source['url']: source for source in sources}
        self.assertEqual(by_url['https://www.wallpolice.org/firearm-forms/']['municipality_codes'], ['1352'])
        self.assertEqual(by_url['https://www.berkeleytownship.org/235/Firearm-Applications']['municipality_codes'], ['1506'])
        self.assertEqual(by_url['https://ptboro.com/public-notices/']['municipality_codes'], ['1525'])
        for source in SOURCES:
            self.assertIn(source['url'], by_url)

    def test_invalid_batch_sizes_rejected(self):
        for value in (0, 101):
            with self.assertRaisesRegex(ValueError, 'between 1 and 100'):
                check(FEED, SOURCES, REGISTRY, {}, plan_only=True, max_sources=value)
            with self.assertRaisesRegex(ValueError, 'between 1 and 100'):
                check(FEED, SOURCES, REGISTRY, {}, plan_only=True, batch_size=value)


if __name__ == '__main__':
    unittest.main()
