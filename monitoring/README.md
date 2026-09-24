# Statewide municipal fee research

The directory contains all 564 New Jersey municipalities. It currently has collected policy evidence for 20 municipalities: 2 with confirmed relief, 17 with reported relief, and 1 pending proposal. The remaining 544 policies are not yet verified. Directory membership and roster review dates are not policy research or verification.

`sources.json` preserves the registered advocacy and municipal sources. A source may be statewide or explicitly attached to municipality codes. Existing evidence URLs are also included. The monitor does not invent police/clerk URLs or treat the authoritative municipality roster as evidence about fees. Currently only the existing 20 evidence-bearing municipalities have registered policy sources; statewide advocacy monitoring is not comprehensive official-policy monitoring.

Run from this review checkout using the existing Python environment:

```sh
python scripts/check_fee_sources.py --output review-output --plan-only
python scripts/check_fee_sources.py --output review-output --previous previous-review/candidates.json --county Ocean --batch-size 20 --max-sources 24
```

`--plan-only` creates the complete human research queue without contacting any source. `--county` selects a county's municipal sources and human queue, while still checking registered statewide sources. Each live run attempts at most 24 URLs by default, including followed links. Individual responses are capped at 16 MiB and 20 seconds; followed links must use HTTPS on the same host, match the policy/meeting terms, and remain within the per-parent link cap. Batches and source rotation advance using the prior review artifact. Supply that artifact to avoid starting each run at the beginning. Use a separate output directory; never use monitoring output as a public data source.

Review artifacts contain:

- `research-queue.json`: every municipality, its current policy status, policy review date, registered policy source URLs, latest automated source check, and next human research action. Unresearched towns have no invented source or policy review date.
- `candidates.json`: directory/research counts, a rotating human research batch (20 towns by default), bounded source observations, unresolved candidates, failures, and continuation cursors. Candidates and observations outside the current batch are retained.
- `review-report.md`: separate directory completeness, policy research progress, source-check coverage, batch details, stale existing evidence, and unverified findings.

An automated source check is not a policy review. A matching page is a **candidate requiring human review**, including for towns already present in the complete directory. Shared municipality names remain ambiguous until a reviewer resolves the code, county, and type. Missing, unreadable, or changed sources do not change a status or imply no relief, a zero fee, or repeal. Unverified policies are queued for initial research instead of being labeled stale verified policies.

A reviewer must inspect municipal documentation, resolve identity and dates, and propose dataset/evidence changes separately. The monitor never edits `nj_carry_fee_relief.json`, the production legal feed, policy dates, status, or amounts. It never publishes findings or sends notifications. The existing workflow has read-only repository permissions and retains review artifacts only; no workflow run was dispatched as part of this local implementation.
