# Smart Agreement - Final Adversarial Audit Report

**Audit state: `PASS WITH LIMITATIONS — NO KNOWN BLOCKING DEFECTS, BUT SPECIFIED CHECKS REMAIN UNVERIFIED`**

This is not a claim that the contract is absolutely secure. It states what was attacked, what was fixed, and what could not be verified here.

Auditor note: the audit was performed by the same AI coding assistant that wrote the implementation. To reduce self-review bias, the equivalence rule was attacked with an **independently written oracle** and randomized forgeries, state-machine invariants were checked with a stateful fuzzer, and every guard was validated by mutation (break the guard, the suite must fail). An independent human review is still recommended before production use.

---

## Revision 2 - expanded limits and rename (this revision)

| Item | Revision 1 (audited below) | Revision 2 |
|---|---|---|
| File | `contracts/workproof.py` | `contracts/smart_agreement.py` (class `SmartAgreement`) |
| SHA-256 | `14d94364fe24ff5511d6ec1ab5bc35635aabc0470efc9c81d45ad4748b94b51a` | `58fd38776f25d54f22a9d1560f78b348215ac13d3a863d4f7be00609b9355f57` |
| Public methods | 8 (4 write, 4 view) | 9: adds the view `get_limits()` |
| Limits | 2-5 criteria; 120 / 1,000 / 300 characters (title / description / criterion); URL 500; deliverable 100,000 bytes; quote 8-200 | 1-20 criteria; 200 / 5,000 / 1,000 characters; URL 2,048; deliverable 250,000 bytes; quote 8-300 (deliveries stay at 2) |

Changes to the contract: the limit constants; the prompt now states the enforced quote bounds from the constants instead of a hard-coded "200"; `get_limits()`; the class and file were renamed and all comments and docstrings were rewritten (the comment-only edit was verified by comparing the code's AST with docstrings removed: identical). Method names, ids (`job-<n>`, `rcpt-<n>`), storage layout and the canonical hash formats are unchanged.

Re-verification of this revision (executed): official linter passed (9 methods); 340 direct-mode tests pass (the 283 tests of revision 1 adapted to derive their numbers from one table, plus 57 new limit tests: exact boundaries, characters vs bytes, worst case end to end, ordering beyond nine criteria, single-criterion agreements, forgeries and differential fuzzing at 12 and 20 criteria); mutation check 51 mutants, 50 killed and 1 equivalent (`M03`), including 18 limit-specific mutants that are also killed by behavioral tests; the pure logic at maximum sizes is byte-identical to native in GenVM's own CPython (normal and deterministic builds, both runner generations).

Audit state of revision 2: **`PASS WITH LIMITATIONS`**, with the same unverified items as revision 1 (no Studio / real-consensus run, no deployment) **plus** these new, unmeasured ones: the validators' model context window at a ~272,000-character prompt, acceptance of ~26 KB create transactions, gas, and the disagreement rate of real models with up to 20 criteria. The ceilings were tested for logic, not on a network; start with lower soft limits in an SDK (see `docs/DESIGN.md`, section 11). No new independent human review was performed.

## 1. Audit target and source commitment (Phase A)

- **Deployment evidence: none exists.** The audited working tree (a copy of the official GenLayer boilerplate) contained no Smart Agreement address, no deployment transaction and no `.env`; its `artifacts/` directory was empty, its deploy script targeted the boilerplate's `football_bets.py` and its frontend was the boilerplate betting UI, with no reference to Smart Agreement. This repository ships no deployment artifacts. **The relationship between this source and any deployed contract is NOT verified, and no deployment can be called audited.** Nothing was deployed or overwritten by the audit.
- Public methods (read from the SDK markers `__gl_public__`/`__gl_readonly__`/`__gl_payable__`, not from names): writes `create_job`, `accept_job`, `submit_delivery`, `evaluate_delivery`; views `get_job`, `get_receipt`, `get_receipt_id`, `get_job_receipts`. **No method is payable and no code path moves value** (no transfers, tokens or escrow exist; asserted by a test).
- Runner: `py-genlayer:1jb45aa8...` (SDK std `11rhn002...`, CPython 3.13.1 WASI). `genlayer-test` 0.29.2, `genvm-linter` 0.11.1-rc.2.
- **Commitment procedure** (reproducible): `sha256sum <files>` from the repository root, bytes as stored (UTF-8, LF).

| File | SHA-256 |
|---|---|
| `contracts/workproof.py` (**audited, final**) | `14d94364fe24ff5511d6ec1ab5bc35635aabc0470efc9c81d45ad4748b94b51a` |
| `variants/next-sdk/workproof_next_sdk.py` (generated) | `b92c934caa9c358efb380478efa4ec749e1ba3e4369849b417fcce86cd9a1bf3` |
| `tests/direct/test_workproof_audit.py` | `f86a0ee2cc6bba52e7a905f009f1ac6c01bf5f1aa21024953dd3c9fef09a20bc` |
| `tests/direct/workproof_support.py` | `7a16dc86b8f57b399837db04fb1541bcdb723a51d6c46943e86d1b0a6e7ca84d` |
| *(superseded)* contract delivered before the audit | `9e3bd35f57dfb8aa703ff07075107b572988f3d03bb3e7113de8233000d9e7d2` |

**Anything deployed from the superseded source (`9e3bd35f...`) does NOT contain fixes F1-F3 and must not be described as audited.** The owner must deploy `contracts/workproof.py` (hash above) and re-run the on-chain source comparison; the official `genlayer` CLI (`genlayer code <address>`, `genlayer schema <address>`) should be used to retrieve the deployed code and compare its hash with the table. Schema/ABI: regenerate with `genvm-lint check contracts/workproof.py --json` (8 methods) and the CLI at deployment.

## 2. Executed verification summary

| Layer | Result |
|---|---|
| Lint, `genvm-lint check` (contract and variant) | OK, 3 rules passed, 8 methods each (variant validated against the real new-generation SDK) |
| Existing boilerplate tests, before and after | 43 / 43 pass |
| Full direct suite | **326 passed, 0 failed** (43 original + 88 functional + 132 equivalence + 63 audit) |
| Audit suite on the UNFIXED contract | 37 of 59 failed: 30 caused by the 3 defects below, 7 were defects in the audit's own new tests (fixed, see 7) |
| Mutation round 1 (validator rules, 15 mutants) | 14 killed; 1 equivalent (`M03`) |
| Mutation round 2 (state machine, identity, fixes, 18 mutants incl. N18) | all killed after adding the corrupted-state tests (2 initial survivors, see 7) |
| Pure helpers inside GenVM's real CPython (normal + deterministic builds, both runner generations), re-run on the FINAL source incl. the new URL/port/TLD and quote rules | 4/4 environments byte-identical to native (one-off harness built on `wasmtime`; not included in this repository) |
| Integration tests (7, Studio) | **Collected, NOT executed** (no Docker, Studio unreachable; GLSim 0.29.2 unusable, see 8) |
| Real multi-validator consensus | **NOT tested** |

*Test counts above refer to the audited working tree, which also contained the 43 tests of the boilerplate's unrelated example contract. This repository contains only the Smart Agreement suites: in a clean install from `requirements.txt` all **283** direct-mode tests pass (88 functional + 132 equivalence + 63 audit) and the 7 integration tests are collected.*

## 3. Phase B - Actual execution path of `evaluate_delivery`

```
tx -> evaluate_delivery(receipt_id)                      [deterministic, reads storage only]
  |- checks: receipt exists; caller is client|worker; receipt PENDING; job IN_PROGRESS;
  |          receipt == job.receipt_ids[delivery_index-1]; receipt.worker == job.worker;
  |          receipt.agreement_hash == job.agreement_hash   (F3)
  |- copies url, agreement_hash, criteria[{id,text}] to plain Python (ctx)
  v
run_nondet_unsafe(leader_fn, validator_fn)               [no storage writes allowed inside]
  LEADER  : web.get(url) -> status/size/UTF-8 checks -> sha256(bytes) -> fixed prompt
            -> model JSON -> _normalize_model_results (ids forced, reason_code derived,
            non-substantive MET -> UNVERIFIABLE) -> dict{job_id, receipt_id, delivery_index,
            agreement_hash, evidence_hash, evidence_size, results[], overall_status}
  VALIDATOR: if leader errored -> re-run, compare error class (EXPECTED/EXTERNAL exact,
            TRANSIENT any transient, LLM_ERROR/VM error never agree)
            else: (1) _check_verdict_structure(leader result, ctx from storage)
                  (2) own fetch + own sha256 -> evidence_hash and evidence_size must equal
                  (3) every leader quote substantive+literal in OWN evidence
                  (4) own model run -> status and reason_code of EVERY criterion must equal
            any exception -> Disagree
  v
verdict (= leader result accepted by consensus)
  |- _check_verdict_structure(verdict, ctx) again       (defense in depth)
  |- ONLY NOW write: receipt.{status,evidence_*,overall_status,results[]} and job.status
```

### 3.1 Field-by-field answers (Section 4.1/4.2)

| Field | Protection |
|---|---|
| `job_id`, `receipt_id`, `delivery_index`, `agreement_hash` | exact equality with storage-derived ctx (leader and post-consensus guard) |
| `evidence_hash`, `evidence_size` | validator fetches the URL itself and recomputes; exact equality |
| criterion set, ids, order | `results` length and `criterion_id == C1..Cn` in order, no dup/extra/missing |
| per-criterion `status`, `reason_code` | exact equality with the validator's OWN evaluation; reason must be the single code for the status |
| `evidence_excerpt` | **the only field allowed to differ**: must be canonical (whitespace-normalized, <=200), literal in the validator's own copy, and >= 8 chars (or the whole short document); `UNVERIFIABLE` must be empty. Safe because it never drives logic: status/reason/overall are compared exactly |
| `overall_status` | re-derived deterministically from the statuses; any mismatch rejects |
| summary / score / threshold / bucket | **none exist.** The model's extra keys (`summary`, `score`) are ignored and never stored (test). No threshold-dependent branches; no scoring system was added |

Allowed difference: excerpt wording only. Ignored data: model prose, extra model keys, HTTP headers. Persistent state depending on the result: `receipt.*` result fields and `job.status` only. A contradictory result cannot be accepted: every path in Section 4.2 is covered by a test (section 6).

Semantic vs deterministic: the *interpretation* ("does the text satisfy the criterion") is semantic, done by the model on both sides and reconciled by exact status equality. Everything around it (ids, hashes, vocabulary, derivation, literal quotes, bindings) is deterministic. The validator does not ask a model whether two reports look similar.

## 4. Findings

Confirmed = reproduced by a failing test before the fix and a passing regression test after it.

### F1 - Trivial quotes satisfy the "literal evidence" rule (Medium, CONFIRMED, FIXED)
- **Where:** `_normalize_model_results`, `_excerpt_is_acceptable` (before: `excerpt in content_norm`).
- **Fields:** `results[].evidence_excerpt`, `status`, `overall_status`, `job.status`.
- **Precondition:** the leader's and the validators' models all answer `MET` (a prompt injection or a weak model). Otherwise the exact status comparison rejects it.
- **Reproduction:** differential fuzz (diagnostic run of 1,500 mutations on one base; the committed test runs 700 mutations x 3 seeds x 3 bases = 6,300): the ONLY disagreement class between the validator and the independent oracle was acceptance of 1-7 character quotes (`e`, `a`, `x`, `MIT`, `License`). `test_probe_trivial_quotes_cannot_support_an_approval[e|a|.|MIT|the]` returned `VERIFIED`.
- **Impact:** the guarantee "approval needs evidence from the document" was nominal: any single character is a substring of nearly any text.
- **Fix:** `MIN_EXCERPT_LEN = 8`; a quote is evidence only if literal and >= 8 chars, or equal to the whole (shorter) document (`_quote_is_substantive`), enforced in normalization and in the validator; non-substantive `MET` downgrades to `UNVERIFIABLE`, non-substantive `NOT_MET` quotes are dropped.
- **Regression:** `test_probe_trivial_quotes_*`, `test_probe_validator_rejects_a_trivially_short_quote`, `test_probe_short_whole_document_may_be_quoted_in_full`, 9 differential-fuzz tests, mutant N11.
- **Residual:** an 8+ character literal quote proves presence, not relevance (see I7).
- *One earlier test depended on a 3-char quote (`"MIT"`) being acceptable; it was changed to a legitimate 18-char literal quote. This is the only pre-existing assertion modified, justified by this finding.*

### F2 - Incomplete host/port validation of the delivery URL (Medium, CONFIRMED, FIXED)
- **Where:** `_clean_url` (called by `submit_delivery`). **State:** `receipt.url`, then the evaluation of whatever it points to.
- **Precondition:** worker supplies the URL; validators' web module can reach internal networks.
- **Reproduction:** 14 of 18 hostile URLs were accepted: `https://localhost./x`, `https://foo.localhost/x`, `https://0x7f.0.0.1/x`, `https://metadata.google.internal/x`, `https://router.lan/x`, `https://example.com./x`, `https://example.com:6379/x`, non-ASCII/underscore/invalid-TLD hosts.
- **Impact:** a worker could aim validator nodes at internal services; a UTF-8 response is hashed and up to 200 characters per criterion are stored as public excerpts (exfiltration of internal content). Whether nodes expose such networks is outside the contract.
- **Fix:** only the default HTTPS port; no trailing dot; ASCII `[a-z0-9-.]`; valid labels; the TLD must be alphabetic (or `xn--` punycode) and not reserved (`localhost, local, internal, localdomain, lan, home, corp, intranet, invalid, test, example`).
- **Regression:** `test_probe_non_public_or_ambiguous_hosts_are_rejected` (18 cases), `test_probe_legitimate_public_urls_still_accepted` (7 cases incl. punycode, `:443`, upper-case), mutants N12-N14.
- **Residual (cannot be solved in-contract):** DNS names that resolve to private addresses (e.g. `127.0.0.1.nip.io`). The web module / node network policy must block private ranges.

### F3 - Evaluation did not re-check receipt/job agreement consistency (Low, CONFIRMED by test, FIXED)
- **Where:** `evaluate_delivery`. Not reachable through the public API (the stateful fuzz proves the invariant over 1,320 random steps), but corrupted or migrated state would have been evaluated.
- **Fix:** `receipt.agreement_hash == job.agreement_hash` is now required.
- **Regression:** `test_probe_evaluation_refuses_inconsistent_stored_bindings`, mutant N15. Also pinned by `test_probe_evaluation_refuses_corrupted_state` (job status, delivery index, receipt worker, agreement hash), which killed mutants N03, N10, N18.

### Informational (not defects; documented behavior)
- **I1 Prompt injection residual.** Delimiters, fixed prompt, forced ids, literal substantive quotes and exact status agreement raise the cost of an attack but cannot make the model immune: if both leader and validator models are fooled the same way and cite a real 8+ char sentence, `VERIFIED` is possible. Mitigation is model quality plus short, objective criteria.
- **I2 Mutable URL / timing.** Either party may trigger evaluation when it likes; the worker controls the content behind the URL. The stored `evidence_hash` proves which bytes were judged; a later content change requires a new delivery (max 2).
- **I3 Criteria are client-authored free text.** Vague criteria yield `INCONCLUSIVE`; the worker sees the terms before accepting. Criteria text is passed as JSON data but is not delimiter-sanitized (it is the client's own agreement).
- **I4 First-come acceptance.** Any address except the client can accept an open job (specified behavior).
- **I5 Dead ends.** After two deliveries (or two dead URLs) a job cannot progress. Consistent with the MVP limit; no funds are locked because no funds exist.
- **I6 Redundant guards kept on purpose** (job-status check, delivery-index check, `status` comparison in the validator): redundant for API-reachable states, retained as defense in depth and pinned by tests. Mutant `M03` is equivalent: `reason_code` is 1:1 with `status` and is also compared.
- **I7 Relevance of quotes is not verified**, only presence/length/canonical form.
- **I8 Public excerpts.** Up to 200 characters per criterion of the deliverable are stored on chain. The raw document is not.
- **I9 Web module behavior** (redirect following, content-type handling, timeouts) is defined by GenVM and could not be exercised offline.

## 5. Records, concurrency, thresholds, permissions (Phases D-F)

- **Identity:** `receipt_id`/`job_id` come from persistent counters, keys equal ids, `job.receipt_ids[i]` maps delivery `i+1`. No wallet-list diffing, no `latest` pointer, no wallet-keyed receipts exist (code search). Failed calls never advance counters (test + stateful fuzz, which keeps checking invariants *after reverted calls*, where direct mode does not roll back, so any partial write would have been caught).
- **Append-only:** an `EVALUATED` receipt is frozen (fuzz ledger compares the full record every step); the only mutation of an older receipt is `PENDING -> SUPERSEDED`, which keeps it.
- **Concurrency:** GenLayer serializes transactions per contract; direct mode cannot simulate true concurrency. Interleaved operations across jobs/workers are tested; real concurrent submissions on a network remain unverified.
- **Thresholds/scores:** none; confirmed and nothing added.
- **State machine/permissions:** tested for each of the four writes (section 6, scenarios 21-24); terms are immutable (no entry point; `terms_hash` recomputed from stored terms after every fuzz step).

## 6. Scenario matrix (Phase H) - generated from a real verbose run

Command: `pytest tests/direct -p no:cacheprovider -v | grep -E " (PASSED|FAILED|ERROR)" > pytest-verbose.txt; python tools/scenario_matrix.py pytest-verbose.txt`

| # | Scenario | Tests | Passed | Failed | Result |
|---|---|---|---|---|---|
| 1 | Leader VERIFIED while a criterion is NOT_MET | 4 | 4 | 0 | PASS |
| 2 | Leader omits a required criterion | 2 | 2 | 0 | PASS |
| 3 | Leader duplicates a criterion | 2 | 2 | 0 | PASS |
| 4 | Leader introduces an unknown criterion | 4 | 4 | 0 | PASS |
| 5 | Leader changes a criterion ID / order | 6 | 6 | 0 | PASS |
| 6 | Reason code contradicts status | 4 | 4 | 0 | PASS |
| 7 | Summary contradicts findings (no free-text summary exists) | 9 | 9 | 0 | PASS |
| 8 | Evidence excerpt not in the source / trivial | 15 | 15 | 0 | PASS |
| 9 | Evidence hash does not match the source bytes | 8 | 8 | 0 | PASS |
| 10 | Same global status, conflicting material finding | 1 | 1 | 0 | PASS |
| 11 | Materially different result, same category / near score | 12 | 12 | 0 | PASS |
| 12 | Leader returns malformed / unexpected type | 15 | 15 | 0 | PASS |
| 13 | Validator meets malformed evidence / model output | 5 | 5 | 0 | PASS |
| 14 | Agreement / receipt ID differs from requested record | 13 | 13 | 0 | PASS |
| 15 | Several deliveries from one wallet -> distinct receipts | 2 | 2 | 0 | PASS |
| 16 | Overlapping / interleaved submissions | 14 | 14 | 0 | PASS |
| 17 | Two agreements cannot share a receipt | 1 | 1 | 0 | PASS |
| 18 | Second evaluation cannot overwrite earlier receipt | 3 | 3 | 0 | PASS |
| 19 | Failed attempt leaves no partial success record | 33 | 33 | 0 | PASS |
| 20 | Receipt queries return the exact requested id | 6 | 6 | 0 | PASS |
| 21 | Unauthorized submit / evaluate | 3 | 3 | 0 | PASS |
| 22 | Invalid state transition | 4 | 4 | 0 | PASS |
| 23 | Repeated evaluation of an evaluated receipt | 2 | 2 | 0 | PASS |
| 24 | Maximum delivery count reached | 1 | 1 | 0 | PASS |
| 25 | Empty / inaccessible / oversized evidence | 9 | 9 | 0 | PASS |
| 26 | Prompt injection in source content | 8 | 8 | 0 | PASS |
| 27 | Temporary HTTP error | 14 | 14 | 0 | PASS |
| 28 | Conflicting / changing source content | 3 | 3 | 0 | PASS |
| 29 | Inconclusive evaluation | 3 | 3 | 0 | PASS |
| 30 | Execution error is never a business outcome | 15 | 15 | 0 | PASS |

Every row above is a Direct-Mode result with a modelled validator. **None of this is real multi-validator consensus.**

## 7. Corrections to the author's own work during the audit

- 7 of the 37 first-run failures were bugs in the new tests (6 stateful-fuzz run-length thresholds, 1 SDK import ordering). Fixed; none of them hid an invariant violation (the fuzz had asserted no invariant failure).
- The mutation round showed that two guards (job-status check, delivery-index check) were not pinned by any test (they are unreachable through the API). Added `test_probe_evaluation_refuses_corrupted_state`; the guards are now killed-on-removal.
- **Process incident:** the first second-round mutation run was cut by a 300 s tool limit while a mutant was written *over the real contract file*. It was detected by hash comparison against a reconstruction (original + the 3 audit patches), restored, re-linted and re-tested with the full suite, and the tooling was redesigned so mutants live in a temporary directory and are loaded through `WORKPROOF_CONTRACT`; the real file is never modified by tooling now. The restored file hash is the one in section 1.

## 8. What remains UNVERIFIED (blocks the first audit state)

1. **Studio/real consensus:** `tests/integration/test_workproof.py` (7 tests) was not run: no Docker, Studio unreachable. GLSim 0.29.2 cannot be used (alternate deployments fail even for the boilerplate's `FootballBets`; it ignores the signing account). Run `gltest tests/integration/test_workproof.py -v -s` on Studio; optional live test: `WORKPROOF_LIVE_URL=https://... gltest ... -k live -s`.
2. **Deployment/source match:** no deployment exists in the repository; see section 1 for the exact steps.
3. Behavior of the real web module (I9), real LLM behavior under injection (I1), real concurrency, appeals/finality.
4. The new-SDK variant is linted and schema-validated only, not executed.
5. A fresh independent human review.

## 9. Conditions to upgrade to `PASS — AUDITED SOURCE AND AVAILABLE TESTS VERIFIED`

(a) Deploy `contracts/workproof.py` (hash in section 1) and confirm with `genlayer code <address>` that the deployed source hash is identical; (b) run the 7 integration tests on Studio and keep the logs; (c) optionally run the live test with real validators on a page you control, including one containing an injection attempt.

## 10. Changes made by the audit

- `contracts/workproof.py`: +56 changed lines (F1, F2, F3) relative to the pre-audit source.
- `tests/direct/workproof_support.py`: `WORKPROOF_CONTRACT` environment override (lets tooling test mutated copies).
- `tests/direct/test_workproof_equivalence.py`: one assertion changed (a 3-character quote replaced by a legitimate 18-character literal quote, see F1).
- Added: `tests/direct/test_workproof_audit.py`, `tools/mutation_check_audit.py`, `tools/scenario_matrix.py`, `tools/port_to_next_sdk.py`.
- The frontend and deploy scripts of the upstream boilerplate are not part of this repository. No deployment was made.
