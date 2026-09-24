import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { createHash } from 'node:crypto';

// Static compatibility review against the actual pinned source, not a substitute
// for Swift decoding, a signed Build 18 device test, or a Firebase delivery test.
const evidence = JSON.parse(readFileSync(new URL('./fixtures/alert-build18-source.json', import.meta.url)));
const draftURL = new URL('../review/alerts/point-pleasant-carry-fee-refund-2026-09-21.json', import.meta.url);
const isDraft = existsSync(draftURL);
const alert = isDraft ? JSON.parse(readFileSync(draftURL)) : JSON.parse(readFileSync(new URL('../nj_legal_updates.json', import.meta.url))).updates.find(item => item.id === 'point-pleasant-carry-fee-refund-2026-09-21');
assert.ok(alert, 'Draft or separately approved published alert exists');
assert.equal(evidence.ref, '8898d4e921d656998c01e8eacf1043d82015bea9');
for (const file of evidence.files) {
  const bytes = Buffer.from(file.content, 'utf8');
  const blobSHA = createHash('sha1').update(`blob ${bytes.length}\0`).update(bytes).digest('hex');
  assert.equal(blobSHA, file.git_blob_sha, `Pinned source integrity: ${file.path}`);
}
const source = name => evidence.files.find(file => file.path.endsWith('/' + name)).content;
const model = source('LegalUpdate.swift');
const root = source('RootView.swift');
const archive = source('LegalUpdatesView.swift');
const keyBlock = model.match(/private enum CodingKeys: String, CodingKey \{([\s\S]*?)\n    \}/)?.[1];
assert.ok(keyBlock, 'Pinned decoder CodingKeys found');
const modelKeys = Array.from(keyBlock.matchAll(/\bcase (\w+)/g), match => match[1]);
assert.deepEqual(Object.keys(alert).sort(), modelKeys.sort(), 'Only existing Build 18 fields');
for (const key of modelKeys) {
  assert.equal(typeof alert[key], key === 'isImportant' ? 'boolean' : 'string', `${key} type`);
}
assert.equal(alert.type, 'legal');
if (isDraft) assert.equal(alert.isImportant, true, 'Keep popup eligibility flag in unpublished draft');
assert.equal(alert.id, 'point-pleasant-carry-fee-refund-2026-09-21');
assert.equal(alert.sourceURL, 'https://mignonelabs.com/nj-carry-permit-fee-refunds/');
assert.equal(alert.sourceTitle, 'CarryAwareNJ Municipal Carry-Fee Tracker');
assert.equal(new URL(alert.sourceURL).protocol, 'https:');
assert.match(alert.status, /Pending/);
assert.match(alert.summary, /Adoption has not yet been independently verified\./);
assert.match(alert.summary, /Teacher in NJ \(@teacher_in_nj\) and Jay Costa \(@jaycostausa\)/);
assert.match(alert.summary, /N\.J\.S\.A\. 2C:58-4/);
assert.match(model, /return URL\(string: sourceURL\)/);
assert.match(root, /primaryButton: \.default\(Text\("View Source"\)\)/);
assert.match(root, /if let source = update\.source \{\s*openURL\(source\)/);
assert.match(archive, /Link\(destination: source\)/);
assert.match(archive, /Label\("View Source"/);
console.log('PASS: pinned Build 18 schema, source integrity, single-link wiring, pending status and attribution.');
console.log('LIMIT: static source checks only; Swift/device rendering and private notification test remain pending.');
