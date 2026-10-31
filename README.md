# Smart Agreement

An [Intelligent Contract](https://docs.genlayer.com/developers/intelligent-contracts) for [GenLayer](https://www.genlayer.com) that turns a digital work agreement into a verifiable outcome.

A client records an agreement with up to **20 acceptance criteria** that are fixed before any work starts. A worker accepts it and delivers a public HTTPS URL. GenLayer's validators then evaluate the delivered content **against exactly those criteria**, and the result is stored per criterion with literal evidence quotes and the SHA-256 of the exact bytes that were judged.

No payments, tokens, escrow, fees, reputation or arbitration are part of it.

> **Naming.** The product is *Smart Agreement* (contract class `SmartAgreement`, file `contracts/smart_agreement.py`). An agreement is called a **job** in the public API; the method names (`create_job`, ...) and ids (`job-<n>`, `rcpt-<n>`) are deliberately unchanged so API/SDK clients keep a stable interface.

> **Status:** audited in direct-test mode (`docs/AUDIT_REPORT.md`, state **`PASS WITH LIMITATIONS`**), then extended with larger limits (revision 2) and re-verified (see [Verification](#verification)). It has **not** been run through real multi-validator consensus on GenLayer Studio or a live network, and no deployment has been source-verified. The larger limits are tested for *logic*; model behavior at those sizes is **unmeasured** (see [Limits](#limits)).

## Limits

Defined in one block at the top of the contract, enforced on-chain and returned by `get_limits()`. They are application-level choices, **not GenLayer protocol limits**.

| Limit | `get_limits()` key | Revision 1 | **Now** | Unit |
|---|---|---|---|---|
| Criteria per agreement | `min_criteria` / `max_criteria` | 2 / 5 | **1 / 20** | count (distinct, case-insensitive) |
| Title | `max_title_len` | 120 | **200** | characters |
| Description (newlines and tabs allowed) | `max_description_len` | 1,000 | **5,000** | characters |
| Each criterion | `max_criterion_len` | 300 | **1,000** | characters |
| Delivery URL | `max_url_len` | 500 | **2,048** | characters |
| Deliverable (fetched page/file) | `max_content_bytes` | 100,000 | **250,000** | bytes (UTF-8) |
| Evidence quote, maximum | `max_excerpt_len` | 200 | **300** | characters |
| Evidence quote, minimum | `min_excerpt_len` | 8 | 8 | characters |
| Deliveries per agreement | `max_deliveries` | 2 | 2 | count |

- Text limits count **characters**, not bytes: a 5,000-character description of emoji is 20,000 bytes of calldata. `max_content_bytes` counts bytes of the fetched response, and the deliverable must be valid UTF-8.
- At the maximum, the prompt each validator sends to its model is about **272,000 characters**, `get_job` returns about **26 KB** and `get_receipt` about **6 KB** (measured in the test suite).
- **The ceilings were tested for logic, not on a live network.** The validators' model context window, transaction/calldata acceptance, gas and the real disagreement rate of models with many criteria were not measured (no limit for them was found in GenLayer's documentation). Every criterion must match *exactly* on every validator, so more criteria means more chances of a disagreement. **Recommendation:** make your SDK read `get_limits()`, start with lower *soft* limits than the ceilings (for example the previous values: 5 criteria, 100,000 bytes) and raise them as you measure real validators. Prefer short, objective, independently checkable criteria.
- Each ceiling is a single constant in the contract, so it can be lowered or raised without touching the logic.

## How it works

```
create_job ──► OPEN ──accept_job──► IN_PROGRESS ──submit_delivery──► receipt PENDING
                                          │
                                 evaluate_delivery (consensus)
                      ┌───────────────────┼────────────────────┐
                  VERIFIED          NEEDS_REVISION        INCONCLUSIVE
                  (terminal)     (corrective delivery, up to max_deliveries per job)
```

`evaluate_delivery(receipt_id)` takes **only** the receipt id. Terms, parties and criteria always come from contract storage.

The security property the contract is built around:

> No verification result is stored unless the validators have independently checked every decisive field of the receipt, using the same agreement terms and evidence they fetched themselves.

- **Independent evidence.** Every validator fetches the URL itself, hashes the exact bytes (SHA-256) and must obtain the same `evidence_hash` and size as the leader.
- **Exact per-criterion agreement.** Each criterion's `status` (`MET` / `NOT_MET` / `UNVERIFIABLE`) and `reason_code` must match the validator's own evaluation. Agreeing only on the overall status is not enough.
- **Complete and ordered.** The result must cover exactly the original criteria `C1..Cn`, in list order, with nothing missing, duplicated, extra or renamed (`C10` is never sorted before `C2`).
- **Evidence you can check.** A `MET` needs a quote that appears literally in the deliverable (between `min_excerpt_len` and `max_excerpt_len` characters, or the whole short document). Without it the criterion is downgraded to `UNVERIFIABLE`, so an invented approval cannot become `VERIFIED`.
- **Derived, never trusted.** The overall status is computed by code from the criterion statuses. There is no score, threshold or free-text summary.
- **Unambiguous records.** Receipts are keyed by a contract-generated id, bound to their job, delivery index and agreement hash, and never overwritten.
- **Fail safe.** HTTP errors, empty/oversized/non-UTF-8 content and unusable model output abort the transaction and store nothing. `INCONCLUSIVE` is a completed business outcome, not an error.

Full details: [`docs/DESIGN.md`](docs/DESIGN.md).

## Public interface

| Method | Kind | Arguments | Returns |
|---|---|---|---|
| `create_job` | write | `title: str`, `description: str`, `criteria: list[str]` | `job_id` (`"job-<n>"`) |
| `accept_job` | write | `job_id: str` | - |
| `submit_delivery` | write | `job_id: str`, `url: str` | `receipt_id` (`"rcpt-<n>"`) |
| `evaluate_delivery` | write | `receipt_id: str` | - |
| `get_job` | view | `job_id: str` | job dict (`found: false` if absent) |
| `get_receipt` | view | `receipt_id: str` | receipt dict (`found: false` if absent) |
| `get_receipt_id` | view | `job_id: str`, `delivery_index: int` (1-based) | receipt id or `""` |
| `get_job_receipts` | view | `job_id: str` | list of receipt dicts |
| `get_limits` | view | - | dict of every limit above (+ `schema_version`) |

Write calls revert with a message prefix: `[EXPECTED]` (invalid call), `[EXTERNAL]` (source problem), `[TRANSIENT]` (source temporarily unavailable) or `[LLM_ERROR]` (unusable model output).

### Building an API or SDK on top

1. **Read the limits once** with `get_limits()` and validate input client-side (counts, character lengths, URL length) to avoid failed transactions.
2. **Flow:** `create_job` -> (worker) `accept_job` -> (worker) `submit_delivery` -> (client or worker) `evaluate_delivery` -> `get_receipt`.
3. **Identify records by id, never by wallet history.** A write's return value is in its transaction result; after `submit_delivery`, `get_receipt_id(job_id, n)` is the authoritative lookup.
4. **Interpret outcomes correctly.** A receipt holds a valid evaluation only when `status == "EVALUATED"`. A reverted, pending, cancelled or undetermined transaction means *no evaluation yet*; it is not the business result `INCONCLUSIVE`. `SUPERSEDED` receipts are history and cannot be evaluated.
5. **Show the evidence.** `results[]` carries, per criterion, the status, a fixed `reason_code` and the quote; `evidence_hash` identifies the exact bytes that were judged.
6. **Size your payloads.** Worst case: `get_job` about 26 KB, `get_receipt` about 6 KB, `get_job_receipts` up to `max_deliveries` receipts.

## Repository layout

```
contracts/smart_agreement.py    The contract (class SmartAgreement)
variants/next-sdk/              Mechanically ported variant for the newer SDK generation (lint-checked only)
tests/direct/                   Direct-mode tests: functional, equivalence attacks, adversarial audit, expanded limits
tests/integration/              Consensus tests for GenLayer Studio (written, not executed)
tools/                          Mutation checks, scenario matrix, variant generator, GLSim launcher
docs/DESIGN.md                  Design and behavior reference, including what really bounds the limits
docs/AUDIT_REPORT.md            Audit findings, evidence, revision history and remaining limitations
```

## Getting started

Requirements: Python 3.12 and internet access (the GenVM runners are downloaded on first use).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

genvm-lint check contracts/smart_agreement.py     # lint + schema validation (9 methods)
pytest tests/direct -q                            # 340 direct-mode tests
```

The toolchain is pinned by commit in `requirements.txt` (`genlayer-test` 0.29.2, `genvm-linter` 0.11.1-rc.2, `genlayer-py` 0.18.0). The contract targets the SDK runner `py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6`.

## Verification

| Check | Result |
|---|---|
| Official linter | passed, 9 methods (5 view, 4 write) |
| Direct-mode tests (`pytest tests/direct`) | **340 passed**: 283 audited-revision tests adapted to the new limits + 57 new limit tests |
| New limit tests | exact boundary (maximum accepted, maximum + 1 rejected) for every limit; characters vs bytes; worst case end to end (20 criteria of 1,000 characters, 250,000-byte deliverable, 2,048-character URL, 300-character quote); ordering with more than nine criteria; single-criterion agreements; 17 forged results at 20 criteria; differential fuzzing against an independent oracle at 12 and 20 criteria; `get_limits` equals the constants |
| Mutation check | 51 mutants: **50 killed, 1 equivalent** (`M03`, documented). 18 target the new limits one by one (each constant off by one in both directions, exclusive vs inclusive comparison, a wrong `get_limits` value, a stale quote maximum in the prompt, zero-padded ids, duplicates accepted); limit mutants are also killed by *behavioral* tests, not only by the `get_limits` table check |
| Real GenVM interpreter | the pure logic at **maximum sizes** (SHA-256 of 250,000 bytes, the 272,098-character prompt, 20 x 1,000-character criteria, a 2,048-character URL, multibyte text, all forged-result checks) gives byte-identical results in GenVM's own CPython 3.13.1 (normal and deterministic builds, both runner generations) |
| Studio / real consensus | **not executed** (the 7 integration tests are written and collected; no Docker or Studio access where this was built) |

Run `gltest tests/integration/test_smart_agreement.py -v -s` on Studio (optional live test: `SMART_AGREEMENT_LIVE_URL=https://... gltest tests/integration/test_smart_agreement.py -k live -s`). `tools/run_glsim_nocache.py` starts the local GLSim simulator with a workaround for a deployment bug in GLSim 0.29.2; GLSim assigns the same sender to every transaction, so it cannot exercise client/worker flows - use Studio.

Tooling: `python tools/mutation_check.py` and `python tools/mutation_check_audit.py` (optionally with ids, e.g. `L01 L09`) weaken the contract and require the suite to fail; mutants live in a temporary directory loaded through `SMART_AGREEMENT_CONTRACT`, so the real contract is never modified. `python tools/port_to_next_sdk.py` regenerates the newer-SDK variant.

## Deploying

Deployment is intentionally not scripted. Use the official GenLayer CLI (`npm install -g genlayer`, then `genlayer deploy`), and then:

1. Compute the commitment of the source you deployed: `sha256sum contracts/smart_agreement.py`. This revision is `58fd38776f25d54f22a9d1560f78b348215ac13d3a863d4f7be00609b9355f57`.
2. Retrieve the deployed code with `genlayer code <address>` and `genlayer schema <address>` and confirm that its hash and schema (9 methods) match.
3. Call `get_limits()` on the deployment and confirm it returns the values in the table above.
4. Run the integration tests on Studio.

Only after (2) and (4) can that deployment be considered to match the audited source. A deployment of the earlier revision (`14d94364...`, file `contracts/workproof.py`) has the old limits and no `get_limits`.

## Known limitations

- **Larger limits are unmeasured on a network** (model context, calldata, gas, disagreement rate): see [Limits](#limits).
- Prompt-injection resistance is defense in depth, not immunity: a model fooled identically on every node can still approve a criterion that quotes a real sentence of the document.
- A quote proves presence in the document, not relevance to the criterion.
- Up to 300 characters per criterion of the deliverable (at most 20 quotes per receipt) are stored publicly on chain; the raw document is not.
- The contract cannot decide whether a DNS name resolves to a private address; node-level network policy must block that.
- The newer-SDK variant is pinned to runner `5jycge4q...` and has been linted and schema-validated but never executed.

See `docs/AUDIT_REPORT.md` for the complete list.
