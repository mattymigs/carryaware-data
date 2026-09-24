"""Read-only statewide research planner and bounded source monitor.

Remote text is evidence, never instructions. Findings remain candidates for human
review; this program does not modify a policy feed or send notifications.
"""
import argparse
import hashlib
import io
import json
import re
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urldefrag
from urllib.request import Request, build_opener, HTTPRedirectHandler

from pypdf import PdfReader
from validate_fee_relief import ROOT, normalize, https

LIMIT = 16 * 1024 * 1024
KEYWORDS = re.compile(r'carry|concealed|handgun|2c:58', re.I)
RELIEF = re.compile(r'refund|rebat|municipal.{0,30}fee|permit.{0,30}fee', re.I)
UNVERIFIED = 'policy_not_yet_verified'
CONFIRMED = {'confirmed_full_or_substantial', 'confirmed_partial'}
REPORTED = {'reported_full_or_substantial', 'reported_partial'}
PENDING = {'announced_pending_documents', 'under_consideration'}


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text, self.links, self.skip = [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'nav', 'header', 'footer'):
            self.skip += 1
        if tag == 'a' and dict(attrs).get('href'):
            self.links.append(dict(attrs)['href'])

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'nav', 'header', 'footer'):
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if not self.skip:
            self.text.append(data)


class SameHostRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not https(newurl) or urlsplit(newurl).hostname != urlsplit(req.full_url).hostname:
            raise ValueError('Redirect requires human review: ' + newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url):
    if not https(url):
        raise ValueError('Only HTTPS sources are allowed')
    request = Request(urldefrag(url)[0], headers={
        'User-Agent': 'CarryAwareNJ-SourceReview/2.0 (+https://mignonelabs.com)'})
    with build_opener(SameHostRedirect()).open(request, timeout=20) as response:
        body = response.read(LIMIT + 1)
        kind = response.headers.get('Content-Type', '')
        if len(body) > LIMIT:
            raise ValueError('Source exceeds size limit')
    links = []
    if body.startswith(b'%PDF'):
        reader = PdfReader(io.BytesIO(body))
        text = ' '.join(page.extract_text() or '' for page in reader.pages)
        if len(text.strip()) < 40:
            return '', [], hashlib.sha256(body).hexdigest()
    elif 'html' in kind or 'text/' in kind:
        page = Page()
        page.feed(body.decode('utf-8', errors='replace'))
        text, links = ' '.join(page.text), page.links
    else:
        raise ValueError('Unsupported source content type: ' + kind)
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) < 40:
        raise ValueError('Empty or unreadable source')
    return text, links, hashlib.sha256(text.encode()).hexdigest()


def matches(text, registry):
    """Match the statewide roster; shared short names remain explicitly ambiguous."""
    normalized, hits, spans, aliases = normalize(text), [], [], {}
    for row in registry:
        full = normalize(row['municipality'])
        short = re.sub(r' (borough|township)$', '', full)
        for name in {full, short}:
            aliases.setdefault(name, []).append(row)
    for name in sorted(aliases, key=len, reverse=True):
        for match in re.finditer(r'(?<!\w)' + re.escape(name) + r'(?!\w)', normalized):
            if any(match.start() < end and match.end() > start for start, end in spans):
                continue
            if (re.match(r'\s+county\b', normalized[match.end():]) or
                    normalized[max(0, match.start() - 10):match.start()].endswith('county of ')):
                continue
            spans.append(match.span())
            choices = {row['municipality_code']: row for row in aliases[name]}
            excerpt = normalized[max(0, match.start() - 130):match.end() + 180]
            if KEYWORDS.search(excerpt) and RELIEF.search(excerpt):
                hits.append(dict(matched_name=name, possible_municipalities=list(choices.values()),
                                 ambiguous=len(choices) > 1, supporting_text=excerpt))
    return hits


def registered_sources(feed, sources):
    """Bind existing policy evidence to stable municipality codes, never invent URLs."""
    by_url = {}
    rows = list(sources)
    for row in feed['municipalities']:
        rows.extend(dict(url=evidence['url'], source_type=evidence['source_type'],
                         follow_links=False, municipality_codes=[row['municipality_code']])
                    for evidence in row['evidence'])
    for source in rows:
        url = urldefrag(source['url'])[0]
        if not https(url):
            raise ValueError('Only HTTPS sources are allowed: ' + url)
        if url not in by_url:
            by_url[url] = dict(source, url=url, municipality_codes=[])
        existing = by_url[url]
        existing['municipality_codes'] = sorted(set(existing['municipality_codes']) |
                                                set(source.get('municipality_codes', [])))
    return list(by_url.values())


def coverage(feed, registry):
    rows = feed['municipalities']
    researched = sum(bool(row['evidence']) and row['status'] != UNVERIFIED for row in rows)
    registry_codes = {row['municipality_code'] for row in registry}
    listed_codes = {row['municipality_code'] for row in rows}
    return dict(authoritative_municipalities=len(registry_codes), directory_municipalities=len(listed_codes),
                directory_counties=len({row['county'] for row in rows}),
                directory_coverage_percent=round(100 * len(listed_codes & registry_codes) / len(registry_codes), 2),
                researched_policy_records=researched,
                policy_research_percent=round(100 * researched / len(registry_codes), 2),
                confirmed_relief=sum(row['status'] in CONFIRMED for row in rows),
                reported_relief=sum(row['status'] in REPORTED for row in rows),
                pending_proposals=sum(row['status'] in PENDING for row in rows),
                policy_not_yet_verified=sum(row['status'] == UNVERIFIED for row in rows))


def research_queue(feed, registry, sources, observations):
    policies = {row['municipality_code']: row for row in feed['municipalities']}
    links_by_code = {}
    for source in sources:
        for code in source.get('municipality_codes', []):
            links_by_code.setdefault(code, set()).add(source['url'])
    queue = []
    for municipality in registry:
        code = municipality['municipality_code']
        policy = policies.get(code, {})
        status = policy.get('status', UNVERIFIED)
        urls = sorted(links_by_code.get(code, []))
        checked = [observations[url]['checked_at'] for url in urls if url in observations]
        if status == UNVERIFIED:
            next_action = 'Identify municipal clerk/police policy sources and research policy; absence of evidence is not no relief.'
        elif status in REPORTED:
            next_action = 'Seek municipal documentation to assess advocacy reporting; keep reported status until human review.'
        elif status in PENDING:
            next_action = 'Check adopted documents and implementation; keep proposal pending until human review.'
        else:
            next_action = 'Review existing municipal evidence for current policy and implementation changes.'
        queue.append(dict(municipality=municipality['municipality'], county=municipality['county'],
                          municipality_code=code, current_status=status,
                          has_policy_evidence=bool(policy.get('evidence')),
                          last_policy_reviewed_at=policy.get('last_checked_at'),
                          registered_policy_source_count=len(urls), registered_policy_source_urls=urls,
                          latest_automated_source_check=max(checked) if checked else None,
                          next_action=next_action))
    # Every town is in the queue. Rotation starts with the unresearched directory.
    return sorted(queue, key=lambda row: (row['current_status'] != UNVERIFIED,
                                          row['county'], row['municipality'], row['municipality_code']))


def check(feed, sources, registry, previous, fetcher=fetch, today=None,
          county=None, batch_size=20, max_sources=24, plan_only=False):
    if not 1 <= batch_size <= 100 or not 1 <= max_sources <= 100:
        raise ValueError('Batch and source limits must be between 1 and 100')
    today = today or date.today().isoformat()
    counties = {row['county'] for row in registry}
    if county is not None:
        county = next((name for name in counties if name.casefold() == county.casefold()), None)
        if county is None:
            raise ValueError('Unknown county')
    scope = county or 'statewide'
    selected_codes = {row['municipality_code'] for row in registry if county is None or row['county'] == county}
    registered = registered_sources(feed, sources)
    all_sources = {source['url']: source for source in registered}
    for source in previous.get('discovered_sources', []):
        # Followed URLs retain their registered parent. No open-web URL guessing.
        if (source.get('parent_url') in {row['url'] for row in registered} and https(source.get('url', '')) and
                urlsplit(source['url']).hostname == urlsplit(source['parent_url']).hostname):
            all_sources.setdefault(source['url'], source)
    eligible = [source for source in all_sources.values()
                if source.get('scope') == 'statewide' or not source.get('municipality_codes') or selected_codes & set(source['municipality_codes'])]
    source_cursors = dict(previous.get('source_cursors', {}))
    start = source_cursors.get(scope, 0) % max(1, len(eligible))
    pending_sources = eligible[start:] + eligible[:start]
    old_observations = previous.get('observations', {})
    observations = dict(old_observations)
    candidates, seen, errors, successful = [], set(), [], []
    researched_codes = {row['municipality_code'] for row in feed['municipalities']
                        if row['status'] != UNVERIFIED and row['evidence']}
    if not plan_only:
        for source in pending_sources:
            url = source['url']
            if url in seen:
                continue
            if len(seen) >= max_sources:
                break
            seen.add(url)
            try:
                text, links, digest = fetcher(url)
                observations[url] = dict(sha256=digest, checked_at=today, source_type=source['source_type'])
                successful.append(url)
                if not text.strip():
                    errors.append(dict(url=url, error='Scanned PDF: link and fingerprint checked; OCR/manual review required', checked_at=today))
                for hit in matches(text, registry):
                    codes = {row['municipality_code'] for row in hit['possible_municipalities']}
                    candidates.append(dict(hit, source_url=url, source_type=source['source_type'],
                                           discovered_at=today, last_seen_at=today, verified_at=None,
                                           status='needs_human_review',
                                           kind='changed_or_existing' if codes <= researched_codes else 'new_candidate'))
                if source.get('follow_links'):
                    found = set()
                    for link in links:
                        destination = urldefrag(urljoin(url, link))[0]
                        if (https(destination) and urlsplit(destination).hostname == urlsplit(url).hostname and
                                re.search(r'carry|concealed|fee|agenda|minutes|resolution', destination, re.I)):
                            found.add(destination)
                    def recency(destination):
                        ym = re.search(r'/(20[0-9]{2})/([01][0-9])/', destination)
                        md = re.search(r'_([01][0-9])([0-3][0-9])(20[0-9]{2})', destination)
                        return (md.group(3) + md.group(1) + md.group(2) if md else
                                ym.group(1) + ym.group(2) + '00' if ym else '', destination)
                    for destination in sorted(found, key=recency, reverse=True)[:min(source.get('max_links', 8), 20)]:
                        if destination not in all_sources:
                            linked = dict(url=destination, source_type=source['source_type'], follow_links=False,
                                          parent_url=url, municipality_codes=source.get('municipality_codes', []),
                                          scope=source.get('scope'))
                            all_sources[destination] = linked
                            pending_sources.append(linked)
            except Exception as error:
                errors.append(dict(url=url, error=str(error), checked_at=today))
    source_cursors[scope] = (start + len(seen)) % max(1, len(pending_sources))

    def candidate_key(candidate):
        return (candidate['source_url'], tuple(sorted(row['municipality_code'] for row in candidate['possible_municipalities'])))
    # A bounded run must not erase candidates or observations outside this batch.
    unique = {candidate_key(candidate): dict(candidate) for candidate in previous.get('candidates', [])}
    for candidate in candidates:
        key = candidate_key(candidate)
        if key in unique:
            candidate['discovered_at'] = unique[key]['discovered_at']
        unique[key] = candidate
    for candidate in unique.values():
        candidate['status'], candidate['verified_at'] = 'needs_human_review', None
    added = [url for url in successful if url not in old_observations]
    changed = [url for url in successful if url in old_observations and
               observations[url]['sha256'] != old_observations[url]['sha256']]
    stale = [dict(municipality=row['municipality'], municipality_code=row['municipality_code'],
                  last_policy_reviewed_at=row['last_checked_at']) for row in feed['municipalities']
             if row['status'] != UNVERIFIED and row['evidence'] and
             (not row['last_checked_at'] or (date.fromisoformat(today) - date.fromisoformat(row['last_checked_at'])).days > 90)]
    queue = research_queue(feed, registry, registered, observations)
    scoped_queue = [row for row in queue if county is None or row['county'] == county]
    research_cursors = dict(previous.get('research_cursors', {}))
    research_start = research_cursors.get(scope, 0) % max(1, len(scoped_queue))
    research_batch = (scoped_queue[research_start:] + scoped_queue[:research_start])[:batch_size]
    research_cursors[scope] = (research_start + len(research_batch)) % max(1, len(scoped_queue))
    registered_codes = {code for source in registered for code in source['municipality_codes']}
    checked_codes = {code for url in seen for code in all_sources[url].get('municipality_codes', [])}
    return dict(checked_at=today, scope=scope, plan_only=plan_only, coverage=coverage(feed, registry),
                monitoring=dict(registered_urls=len(registered), municipalities_with_registered_policy_sources=len(registered_codes),
                                municipalities_without_registered_policy_sources=len(registry) - len(registered_codes),
                                urls_attempted_this_run=len(seen), urls_checked_this_run=len(successful),
                                municipalities_with_sources_attempted_this_run=len(checked_codes),
                                max_sources_per_run=max_sources, deferred_urls=len(pending_sources) - len(seen),
                                human_research_batch_size=len(research_batch)),
                observations=observations, candidates=list(unique.values()), added=added, changed=changed,
                stale=stale, broken_or_unreadable=errors, research_queue=queue, research_batch=research_batch,
                source_cursors=source_cursors, research_cursors=research_cursors,
                discovered_sources=[source for source in all_sources.values() if source.get('parent_url')],
                human_review_required=True)


def report(result):
    def line(value):
        return str(value).replace('\n', ' ').replace('<', '&lt;').replace('>', '&gt;')
    cov, monitor = result['coverage'], result['monitoring']
    sections = ['# Municipal fee source review',
                f'Prepared {result["checked_at"]}; scope: {result["scope"]}. Automated findings are unverified. No feed or notification was published.',
                '', '## Statewide directory and policy research',
                f'- Directory: {cov["directory_municipalities"]}/{cov["authoritative_municipalities"]} municipalities across {cov["directory_counties"]} counties ({cov["directory_coverage_percent"]}%).',
                f'- Collected policy evidence: {cov["researched_policy_records"]} ({cov["policy_research_percent"]}%); policy not yet verified: {cov["policy_not_yet_verified"]}.',
                f'- Confirmed relief: {cov["confirmed_relief"]}; reported relief: {cov["reported_relief"]}; pending proposals: {cov["pending_proposals"]}.',
                '- A complete directory does not mean every policy has been researched. Missing evidence never establishes no refund or a zero fee.',
                '', '## Source monitoring coverage',
                f'- {monitor["registered_urls"]} registered URLs, attached to {monitor["municipalities_with_registered_policy_sources"]} municipalities. {monitor["municipalities_without_registered_policy_sources"]} municipalities still need policy-source registration.',
                f'- {monitor["urls_attempted_this_run"]} URLs attempted this run (cap {monitor["max_sources_per_run"]}); {monitor["urls_checked_this_run"]} successfully checked; {monitor["deferred_urls"]} deferred.',
                '- An automated source fetch updates monitoring observations only. It never updates a policy review date, status, amount, or evidence.',
                '', f'## Next human research batch ({len(result["research_batch"])})']
    if result['plan_only']:
        sections.append('- Planning only: no URLs were fetched.')
    for row in result['research_batch']:
        sections.append('- ' + line(f'{row["municipality"]} ({row["county"]}; {row["municipality_code"]}) | {row["current_status"]} | {row["next_action"]}'))
    for key in ('added', 'changed', 'stale', 'broken_or_unreadable'):
        values = result[key]
        sections += ['', f'## {key.replace("_", " ").title()} ({len(values)})']
        sections += ['- ' + line(json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else value) for value in values] or ['- None']
    sections += ['', f'## Candidates retained for human review ({len(result["candidates"])})']
    for candidate in result['candidates']:
        sections.append('- ' + line(candidate['matched_name']) + ' | ' + candidate['kind'] +
                        (' | AMBIGUOUS — resolve county/type' if candidate['ambiguous'] else '') +
                        ' | ' + line(candidate['source_url']) + ' | ' + line(candidate['supporting_text']))
    return '\n'.join(sections) + '\n'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--previous', type=Path)
    parser.add_argument('--county', help='Restrict registered municipal sources and human queue to one NJ county; statewide sources still checked.')
    parser.add_argument('--batch-size', type=int, default=20, help='Human review queue size, 1–100 (default 20).')
    parser.add_argument('--max-sources', type=int, default=24, help='Maximum URL requests, 1–100 (default 24).')
    parser.add_argument('--plan-only', action='store_true', help='Build the statewide research queue without any network requests.')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    previous = json.loads(args.previous.read_text()) if args.previous and args.previous.exists() else {}
    result = check(json.loads((ROOT / 'nj_carry_fee_relief.json').read_text()),
                   json.loads((ROOT / 'monitoring/sources.json').read_text())['sources'],
                   json.loads((ROOT / 'schemas/nj_municipalities.json').read_text())['municipalities'], previous,
                   county=args.county, batch_size=args.batch_size, max_sources=args.max_sources, plan_only=args.plan_only)
    (args.output / 'candidates.json').write_text(json.dumps(result, indent=2) + '\n')
    (args.output / 'research-queue.json').write_text(json.dumps(result['research_queue'], indent=2) + '\n')
    (args.output / 'review-report.md').write_text(report(result))
    print('Review artifacts written:', args.output)
