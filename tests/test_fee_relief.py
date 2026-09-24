import copy
import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from validate_fee_relief import ROOT, validate, totals, normalize, https
from check_fee_sources import check, matches
FEED=json.loads((ROOT/'nj_carry_fee_relief.json').read_text())
REGISTRY=json.loads((ROOT/'schemas/nj_municipalities.json').read_text())['municipalities']
PROD=json.loads((ROOT/'nj_legal_updates.json').read_text())
DRAFTS=[json.loads(p.read_text()) for p in (ROOT/'review/alerts').glob('*.json')]
ALERT=next((a for a in DRAFTS+PROD['updates'] if a['id']=='point-pleasant-carry-fee-refund-2026-09-21'),None)
class FeeTests(unittest.TestCase):
 def test_valid(self): validate(FEED,PROD,DRAFTS)
 def test_totals(self): self.assertEqual(totals(FEED),dict(total=564,researched=20,unverified=544,confirmed=2,reported=17,pending=1,full=18,partial=1,counties=21,percent=3.55))
 def test_schema_missing(self):
  x=copy.deepcopy(FEED);del x['municipalities'][0]['notes']
  with self.assertRaises(Exception):validate(x)
 def test_duplicate_municipality(self):
  x=copy.deepcopy(FEED);x['municipalities'].append(x['municipalities'][0])
  with self.assertRaisesRegex(AssertionError,'Duplicate municipality'):validate(x)
 def test_duplicate_alert(self):
  with self.assertRaisesRegex(AssertionError,'Duplicate alert'):validate(FEED,PROD,[ALERT,ALERT])
 def test_invalid_status(self):
  x=copy.deepcopy(FEED);x['municipalities'][0]['status']='verified'
  with self.assertRaises(Exception):validate(x)
 def test_https(self):
  for url in ['http://example.com','javascript:alert(1)','https://','https://u:p@example.com','https://example.com/a b']:
   self.assertFalse(https(url))
  x=copy.deepcopy(FEED);next(r for r in x['municipalities'] if r['evidence'])['evidence'][0]['url']='http://example.com'
  with self.assertRaises(Exception):validate(x)
 def test_normalization(self):
  self.assertEqual(normalize('Franklin Boro.'),'franklin borough')
  x=copy.deepcopy(FEED);x['municipalities'][0]['county']='Sussex'
  with self.assertRaisesRegex(AssertionError,'Noncanonical county'):validate(x)
 def test_bad_date(self):
  x=copy.deepcopy(FEED);x['municipalities'][0]['verified_at']='2026-02-30'
  with self.assertRaises(Exception):validate(x)
 def test_pending_not_verified(self):
  r=next(r for r in FEED['municipalities'] if r['municipality_code']=='1525')
  self.assertEqual(r['status'],'announced_pending_documents');self.assertIsNone(r['verified_at'])
  self.assertIsNone(r['refund_amount']);self.assertIsNone(r['effective_date'])
 def test_point_pleasant_beach(self):
  x=matches('Point Pleasant Beach Borough is considering a carry permit fee refund.',REGISTRY)
  self.assertEqual(len(x),1);self.assertEqual(x[0]['possible_municipalities'][0]['municipality_code'],'1526')
  beach=next(r for r in FEED['municipalities'] if r['municipality_code']=='1526')
  self.assertEqual(beach['status'],'policy_not_yet_verified');self.assertFalse(beach['evidence'])
 def test_state_and_county_names_not_municipalities(self):
  x=matches('New Jersey carry permit refunds in Passaic County.',REGISTRY)
  self.assertEqual(x,[])
 def test_aliases_deduplicate_municipality(self):
  def fake(u):return ('Wall Township offers carry permit fee refunds. Wall offers handgun fee refunds.',[],'abc')
  r=check(FEED,[dict(url='https://example.com',source_type='official_notice')],REGISTRY,{},fake,'2026-09-23')
  found=[x for x in r['candidates'] if x['source_url']=='https://example.com']
  self.assertEqual(len(found),1)
 def test_franklin_ambiguity(self):
  x=matches('Franklin may offer carry permit fee refunds.',REGISTRY)
  self.assertTrue(x[0]['ambiguous'])
 def test_berkeley_exact_cost(self):
  r=next(r for r in FEED['municipalities'] if r['municipality_code']=='1506')
  self.assertEqual((r['refund_amount'],r['net_municipal_cost'],r['effective_date']),(100,50,'2026-05-18'))
 def test_monitor_does_not_mutate(self):
  before=copy.deepcopy(FEED)
  def fake(u): return ('Kinnelon Borough offers carry permit fee refunds.',[],'abc')
  result=check(FEED,[dict(url='https://example.com',source_type='official_notice')],REGISTRY,{},fake,'2026-09-23')
  self.assertEqual(FEED,before);self.assertTrue(result['human_review_required'])
  self.assertTrue(all(c['verified_at'] is None for c in result['candidates']))
  self.assertTrue(any(c['kind']=='new_candidate' for c in result['candidates']))
 def test_monitor_changed_broken_stale(self):
  def fake(u):
   if 'ptboro' in u:raise ValueError('HTTP 404')
   return ('Handgun carry fee refund review.',[],'new')
  u='https://example.com'; old={'observations':{u:{'sha256':'old'}}}
  result=check(FEED,[dict(url=u,source_type='official_notice')],REGISTRY,old,fake,'2027-01-01')
  self.assertIn(u,result['changed']);self.assertTrue(result['broken_or_unreadable']);self.assertEqual(len(result['stale']),20)
 def test_monitor_deduplicates_and_preserves_discovery(self):
  def fake(u):return ('Kinnelon Borough offers carry permit fee refunds. Kinnelon Borough refunds carry permit fees.',[],'abc')
  sources=[dict(url='https://example.com',source_type='official_notice')]*2
  r=check(FEED,sources,REGISTRY,{},fake,'2026-09-23');r2=check(FEED,sources,REGISTRY,r,fake,'2026-09-30')
  self.assertEqual(len(r['candidates']),len(r2['candidates']))
  self.assertTrue(all(c['discovered_at']=='2026-09-23' for c in r2['candidates']))
 def test_no_production_sender_in_workflow(self):
  s=(ROOT/'.github/workflows/municipal-fee-review.yml').read_text()
  for value in ['id-token: write','contents: write','update-notifications.mjs send','google-github-actions/auth','firebase-admin','git push']:
   self.assertNotIn(value,s)
 def test_exact_credit(self):self.assertIn('Community credit: Teacher in NJ (@teacher_in_nj) and Jay Costa (@jaycostausa).',ALERT['summary'])
 def test_build18_single_link_alert(self):
  self.assertEqual(set(ALERT),{'id','type','title','summary','category','status','date','sourceTitle','sourceURL','isImportant'})
  self.assertEqual(ALERT['sourceURL'],'https://mignonelabs.com/nj-carry-permit-fee-refunds/')
  self.assertEqual(ALERT['sourceTitle'],'CarryAwareNJ Municipal Carry-Fee Tracker')
  self.assertEqual(ALERT['category'],'Permit & Licensing Update')
  self.assertIn('Pending',ALERT['status'])
if __name__=='__main__':unittest.main()
