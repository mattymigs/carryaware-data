"""Read-only source monitor. Emits candidates/reports; never edits a feed or sends FCM.
Remote text is evidence only, never executable instructions. Run with --output DIR.
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
from urllib.request import Request, urlopen, build_opener, HTTPRedirectHandler
from pypdf import PdfReader
from validate_fee_relief import ROOT, normalize, https
LIMIT = 16 * 1024 * 1024
KEYWORDS = re.compile(r'carry|concealed|handgun|2c:58',re.I)
RELIEF = re.compile(r'refund|rebat|municipal.{0,30}fee|permit.{0,30}fee',re.I)
class Page(HTMLParser):
    def __init__(self): super().__init__(); self.text=[]; self.links=[]; self.skip=0
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style','nav','header','footer'): self.skip+=1
        if tag=='a':
            h=dict(attrs).get('href')
            if h:self.links.append(h)
    def handle_endtag(self,tag):
        if tag in ('script','style','nav','header','footer'):self.skip=max(0,self.skip-1)
    def handle_data(self,data):
        if not self.skip:self.text.append(data)
class SameHostRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        if not https(newurl) or urlsplit(newurl).hostname!=urlsplit(req.full_url).hostname:
            raise ValueError('Redirect requires human review: '+newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def fetch(url):
    if not https(url):raise ValueError('Only HTTPS sources are allowed')
    with build_opener(SameHostRedirect()).open(Request(urldefrag(url)[0],headers={'User-Agent':'CarryAwareNJ-SourceReview/1.0 (+https://mignonelabs.com)'}),timeout=20) as r:
        body=r.read(LIMIT+1);kind=r.headers.get('Content-Type','')
        if len(body)>LIMIT:raise ValueError('Source exceeds size limit')
    links=[]
    if body.startswith(b'%PDF'):
        reader=PdfReader(io.BytesIO(body)); text=' '.join(p.extract_text() or '' for p in reader.pages)
        if len(text.strip())<40:return '', [], hashlib.sha256(body).hexdigest()
    elif 'html' in kind or 'text/' in kind:
        p=Page();p.feed(body.decode('utf-8',errors='replace'));text=' '.join(p.text);links=p.links
    else:raise ValueError('Unsupported source content type: '+kind)
    text=re.sub(r'\s+',' ',text).strip()
    if len(text)<40:raise ValueError('Empty or unreadable source')
    return text,links,hashlib.sha256(text.encode()).hexdigest()

def matches(text,registry):
    """Longest matching names first prevents Point Pleasant Beach becoming Borough.
    A short name shared by multiple counties/types remains ambiguous, never guessed.
    """
    normalized=normalize(text); hits=[];spans=[]
    aliases={}
    for r in registry:
        full=normalize(r['municipality']);short=re.sub(r' (borough|township)$','',full)
        for name in {full,short}:aliases.setdefault(name,[]).append(r)
    for name in sorted(aliases,key=len,reverse=True):
        for m in re.finditer(r'(?<!\w)'+re.escape(name)+r'(?!\w)',normalized):
            if any(m.start()<b and m.end()>a for a,b in spans):continue
            if re.match(r'\s+county\b', normalized[m.end():]) or normalized[max(0,m.start()-10):m.start()].endswith('county of '): continue
            spans.append(m.span())
            choices={x['municipality_code']:x for x in aliases[name]}
            # Context is retained for a reviewer; no county is inferred from proximity.
            excerpt=normalized[max(0,m.start()-130):m.end()+180]
            if not KEYWORDS.search(excerpt) or not RELIEF.search(excerpt):continue
            hits.append(dict(matched_name=name,possible_municipalities=list(choices.values()),ambiguous=len(choices)>1,supporting_text=excerpt))
    return hits

def check(feed,sources,registry,previous,fetcher=fetch,today=None):
    today=today or date.today().isoformat();observations={};candidates=[];seen=set();errors=[];queue=list(sources)
    # Existing official + secondary evidence URLs also receive link/content checks.
    for row in feed['municipalities']:
        for e in row['evidence']: queue.append(dict(url=e['url'],source_type=e['source_type'],follow_links=False))
    for source in queue:
        url=urldefrag(source['url'])[0]
        if url in seen:continue
        seen.add(url)
        try:
            text,links,digest=fetcher(url)
            observations[url]=dict(sha256=digest,checked_at=today,source_type=source['source_type'])
            if not text.strip(): errors.append(dict(url=url,error='Scanned PDF: link and fingerprint checked; OCR/manual review required',checked_at=today))
            for h in matches(text,registry):
                codes={r['municipality_code'] for r in h['possible_municipalities']}
                tracked={r['municipality_code'] for r in feed['municipalities']}
                candidates.append(dict(h,source_url=url,source_type=source['source_type'],discovered_at=today,verified_at=None,status='needs_human_review',kind='changed_or_existing' if codes & tracked else 'new_candidate'))
            if source.get('follow_links'):
                found=[]
                for link in links:
                    dest=urldefrag(urljoin(url,link))[0]
                    if https(dest) and urlsplit(dest).hostname==urlsplit(url).hostname and re.search(r'carry|concealed|fee|agenda|minutes|resolution',dest,re.I) and dest not in seen and dest not in found:found.append(dest)
                def recency(dest):
                    ym=re.search(r'/(20[0-9]{2})/([01][0-9])/',dest)
                    md=re.search(r'_([01][0-9])([0-3][0-9])(20[0-9]{2})',dest)
                    return (md.group(3)+md.group(1)+md.group(2) if md else ym.group(1)+ym.group(2)+'00' if ym else '',dest)
                for dest in sorted(found,key=recency,reverse=True)[:source.get('max_links',8)]:
                    queue.append(dict(url=dest,source_type=source['source_type'],follow_links=False))
        except Exception as e: errors.append(dict(url=url,error=str(e),checked_at=today))
    def candidate_key(x):return (x['source_url'],tuple(sorted(r['municipality_code'] for r in x['possible_municipalities'])))
    unique={}
    for x in candidates:unique.setdefault(candidate_key(x),x)
    old_candidates={candidate_key(x):x for x in previous.get('candidates',[])}
    for key,c in unique.items():
        if key in old_candidates:c['discovered_at']=old_candidates[key]['discovered_at']
    old=previous.get('observations',{})
    added=[u for u in observations if u not in old]
    changed=[u for u,o in observations.items() if u in old and o['sha256']!=old[u]['sha256']]
    stale=[dict(municipality=r['municipality'],last_verified_at=r['verified_at']) for r in feed['municipalities'] if not r['verified_at'] or (date.fromisoformat(today)-date.fromisoformat(r['verified_at'])).days>90]
    return dict(checked_at=today,observations=observations,candidates=list(unique.values()),added=added,changed=changed,stale=stale,broken_or_unreadable=errors,human_review_required=True)

def report(result):
    def line(s):return str(s).replace('\n',' ').replace('<','&lt;').replace('>','&gt;')
    sections=['# Municipal fee source review',f'Checked {result["checked_at"]}. Automated findings are unverified. No feed or notification was published.']
    for key in ('added','changed','stale','broken_or_unreadable'):
        values=result[key];sections+=['',f'## {key.replace("_"," ").title()} ({len(values)})']
        sections += ['- '+line(json.dumps(x,ensure_ascii=False) if isinstance(x,dict) else x) for x in values] or ['- None']
    sections+=['',f'## Candidates ({len(result["candidates"])})']
    for c in result['candidates']:
        sections.append('- '+line(c['matched_name'])+' | '+c['kind']+(' | AMBIGUOUS — resolve county/type' if c['ambiguous'] else '')+' | '+line(c['source_url'])+' | '+line(c['supporting_text']))
    return '\n'.join(sections)+'\n'

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--previous',type=Path);a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=True)
    previous=json.loads(a.previous.read_text()) if a.previous and a.previous.exists() else {}
    result=check(json.loads((ROOT/'nj_carry_fee_relief.json').read_text()),json.loads((ROOT/'monitoring/sources.json').read_text())['sources'],json.loads((ROOT/'schemas/nj_municipalities.json').read_text())['municipalities'],previous)
    (a.output/'candidates.json').write_text(json.dumps(result,indent=2)+'\n')
    (a.output/'review-report.md').write_text(report(result))
    print('Review artifacts written:',a.output)
