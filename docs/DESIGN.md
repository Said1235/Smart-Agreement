# Smart Agreement - Design

This document describes the contract exactly as implemented in `contracts/smart_agreement.py` (class `SmartAgreement`; see `AUDIT_REPORT.md` for the security review). "Smart Agreement" is the product name; in the public API an agreement is called a *job* (`create_job`, `job-<n>`) and those names are kept stable for API/SDK clients.

## 1. Scope

One Intelligent Contract that records digital work agreements, assigns a worker, accepts a public HTTPS deliverable and evaluates it against acceptance criteria that were fixed before work started.

Out of scope by design: payments, tokens, escrow, fees, reputation, arbitration, agreement amendments. No method is `payable` and no code path moves value.

## 2. Lifecycle

```
create_job -> OPEN --accept_job--> IN_PROGRESS --submit_delivery--> (receipt PENDING)
                                        |
                              evaluate_delivery (consensus)
                                        |
                  +---------------------+----------------------+
              VERIFIED              NEEDS_REVISION          INCONCLUSIVE
              (terminal)           (corrective delivery      (corrective delivery
                                    allowed if count < 2)     allowed if count < 2)
```

| Job status | Meaning |
|---|---|
| `OPEN` | created, waiting for a worker |
| `IN_PROGRESS` | accepted; waiting for a delivery or for its evaluation |
| `VERIFIED` | every criterion `MET` (terminal) |
| `NEEDS_REVISION` | at least one criterion `NOT_MET` and none `UNVERIFIABLE` |
| `INCONCLUSIVE` | at least one criterion `UNVERIFIABLE` (not a proven failure) |

| Receipt status | Meaning |
|---|---|
| `PENDING` | submitted, not evaluated yet (always the job's latest delivery) |
| `EVALUATED` | result stored; immutable afterwards |
| `SUPERSEDED` | replaced by a newer delivery before it was evaluated; kept as history |

The overall status is always derived by code from the per-criterion statuses (all `MET` -> `VERIFIED`; any `UNVERIFIABLE` -> `INCONCLUSIVE`; otherwise any `NOT_MET` -> `NEEDS_REVISION`). There is no score, threshold or free-text summary.

## 3. Operations

All write methods revert with a `UserError` whose message starts with a class prefix: `[EXPECTED]` (invalid call), `[EXTERNAL]` (source problem), `[TRANSIENT]` (source temporarily unavailable), `[LLM_ERROR]` (unusable model output).

| Method | Arguments | Returns | Rules |
|---|---|---|---|
| `create_job` | `title: str`, `description: str`, `criteria: list[str]` | `job_id` (`"job-<n>"`) | caller becomes the client; 1-20 distinct criteria; limits in section 6; criteria get ids `C1..Cn`; terms are immutable afterwards |
| `accept_job` | `job_id` | - | job `OPEN`; caller must not be the client; first caller becomes the worker |
| `submit_delivery` | `job_id`, `url` | `receipt_id` (`"rcpt-<n>"`) | only the worker; job in `IN_PROGRESS`, `NEEDS_REVISION` or `INCONCLUSIVE`; at most `max_deliveries` (2) deliveries; URL rules in section 6; an unevaluated previous receipt becomes `SUPERSEDED` |
| `evaluate_delivery` | `receipt_id` | - | caller is client or worker; receipt `PENDING` and is the job's latest delivery; job `IN_PROGRESS`; receipt and job agree on worker and agreement hash. **No other argument exists:** criteria, hashes and parties always come from storage |

Views (an absent record returns `found: false`, never an error):

| Method | Returns |
|---|---|
| `get_job(job_id)` | `found, id, client, worker ("" while OPEN), title, description, criteria[{id,text}], terms_hash, agreement_hash, status, delivery_count, receipt_ids[]` |
| `get_receipt(receipt_id)` | `found, receipt_id, job_id, delivery_index, worker, url, status, agreement_hash, evidence_hash, evidence_size, overall_status, evaluation_requested_by, results[{criterion_id, status, reason_code, evidence_excerpt}]` |
| `get_receipt_id(job_id, delivery_index)` | the receipt id of delivery N (1-based), or `""` |
| `get_job_receipts(job_id)` | the receipt dicts of a job, in delivery order (at most `max_deliveries`) |
| `get_limits()` | every limit this contract enforces, as integers (see section 6); an API/SDK should read it instead of hard-coding numbers |

### Integration rules

- A write's return value is part of the transaction result. Receipt ids are never inferred from wallet history; after `submit_delivery`, `get_receipt_id(job_id, n)` is the authoritative lookup.
- A receipt carries a valid evaluation only when `status == "EVALUATED"`. A reverted, pending, cancelled or undetermined transaction means **no evaluation yet**, which is different from the business result `INCONCLUSIVE`.
- `SUPERSEDED` receipts are history and cannot be evaluated.
- The GenLayer transaction id, the `receipt_id` and the `evidence_hash` are three different identifiers; the contract never reads the transaction id.

## 4. Storage

Only SDK storage types are used.

```
jobs:             TreeMap[str, Job]        key "job-<n>"
receipts:         TreeMap[str, Receipt]    key "rcpt-<n>"
job_counter:      u256
receipt_counter:  u256
```

- `Job`: `id, client, worker, title, description, criteria: DynArray[Criterion{id,text}], terms_hash, agreement_hash, status, delivery_count: u32, receipt_ids: DynArray[str]` (index `i` is delivery `i+1`).
- `Receipt`: `receipt_id, job_id, delivery_index: u32, worker, url, status, requested_by, agreement_hash, evidence_hash, evidence_size: u32, overall_status, results: DynArray[CriterionResult{criterion_id, status, reason_code, evidence_excerpt}]`.

Receipts are keyed by their exact id. Delivery `n` of a job is `job.receipt_ids[n-1]`. Evaluated receipts are never rewritten. The raw deliverable is not stored (only its hash, size and short quoted excerpts).

## 5. Hashes and canonical serialization

Canonical bytes: `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")`; hash: SHA-256, lowercase hex.

- `terms_hash` (at creation): `{"v":1, "title", "description", "criteria":[{"id","text"}]}`.
- `agreement_hash` (at acceptance): `{"v":1, "job_id", "client", "worker", "terms_hash"}`, addresses as lowercase hex of the 20 raw bytes.
- `evidence_hash`: SHA-256 of the exact response bytes, computed by the contract during evaluation; never supplied by a caller.

## 6. Limits and input rules

All limits are application-level choices, **not GenLayer protocol limits**, defined in one block at the top of the contract and returned by `get_limits()`. Text limits count **characters** (Unicode code points; a 5,000-character description of emoji is 20,000 bytes of calldata); the deliverable limit counts **bytes** of the fetched response.

| Limit | `get_limits()` key | Revision 1 | Now | Unit |
|---|---|---|---|---|
| Criteria per agreement | `min_criteria` / `max_criteria` | 2 / 5 | **1 / 20** | count (distinct, case-insensitive) |
| Title | `max_title_len` | 120 | **200** | characters |
| Description (newlines and tabs allowed) | `max_description_len` | 1,000 | **5,000** | characters |
| Each criterion | `max_criterion_len` | 300 | **1,000** | characters |
| Delivery URL | `max_url_len` | 500 | **2,048** | characters |
| Deliverable | `max_content_bytes` | 100,000 | **250,000** | bytes of UTF-8 |
| Evidence quote, maximum | `max_excerpt_len` | 200 | **300** | characters |
| Evidence quote, minimum | `min_excerpt_len` | 8 | 8 (unchanged) | characters |
| Deliveries per agreement | `max_deliveries` | 2 | 2 (unchanged) | count |

No control characters are allowed in any text; a delivery needs a public HTTPS URL (rules below). Criteria ids are `C1..C20`; results always follow the criteria **list order**, never text order (`C10` is not sorted before `C2`).

URL: `https` only; no credentials, fragment, whitespace or control characters; only the default port (443); the host must be a lowercase ASCII DNS name (punycode for IDN) with valid labels, at least two labels, no trailing dot, a purely alphabetic (or `xn--`) top-level domain that is not reserved (`localhost, local, internal, localdomain, lan, home, corp, intranet, invalid, test, example`), and never an IP literal. String checks cannot prove that a DNS name resolves to a public address; blocking private ranges is the job of the node's web module.

## 7. How a result is verified

`evaluate_delivery` runs a leader function and a validator function through `gl.vm.run_nondet_unsafe`.

**Leader:** fetches the stored URL (`200` only; `408/429/5xx` -> `[TRANSIENT]`; other statuses, empty/oversized/non-UTF-8 content -> `[EXTERNAL]`), hashes the bytes, builds a fixed prompt (criteria as JSON data; the deliverable wrapped in delimiters with delimiter look-alikes neutralized), asks for JSON, and normalizes the answer in code:

- exactly one result per original criterion, in order, with the original ids (anything else is `[LLM_ERROR]`);
- the model contributes only `status` and a quote; `reason_code` is fixed by the status;
- a `MET` without a **substantive literal quote** is downgraded to `UNVERIFIABLE`; a non-substantive `NOT_MET` quote is dropped; `UNVERIFIABLE` carries no quote;
- a quote is substantive when it appears literally (whitespace-normalized) in the deliverable and has at least 8 characters, or equals the whole (shorter) document; quotes are capped at the maximum quote length;
- the overall status is derived by code.

**Validator** (any exception counts as disagreement):

1. the leader result is a `Return` with exactly the expected keys and types, and `job_id`, `receipt_id`, `delivery_index`, `agreement_hash` equal the values read from storage;
2. results cover exactly `C1..Cn` in order; each status and reason code is valid and consistent; quotes are canonical; the overall status matches the derivation;
3. it fetches the same URL itself and requires equal `evidence_hash` and `evidence_size`;
4. every leader quote is substantive and literal in the validator's own copy of the evidence;
5. it runs its own model evaluation and requires the same `status` and `reason_code` for every criterion. Only the quote wording may differ between evaluators.

If the leader failed, the validator redoes the work: `[EXPECTED]`/`[EXTERNAL]` errors must reproduce exactly, `[TRANSIENT]` agrees with any transient failure, `[LLM_ERROR]` and VM-level errors never agree.

After consensus the structural checks run again on the accepted result and only then is anything written: the stored receipt is the accepted result verbatim, and the job status follows it. A failed or undetermined transaction leaves the receipt `PENDING` and retryable.

## 8. Toolchain and SDK generations

- Contract target: runner `py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6` (the boilerplate generation: `from genlayer import *`, `gl.Contract`, `gl.vm.run_nondet_unsafe`).
- A newer SDK generation exists (`import genlayer as gl`, `gl.contract.Contract`, `gl.vm.run_nondet`, `UserError.data`). Its runner hash changes between GenVM releases. `variants/next-sdk/smart_agreement_next_sdk.py` is generated mechanically by `tools/port_to_next_sdk.py` and pinned to runner `py-genlayer:5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng`, the newer-generation runner it was validated against; it passes lint and schema validation (9 methods) only, its behavior is not covered by tests, and a newer runner must be re-pinned and re-validated.
- Verified tool versions: `genlayer-test` 0.29.2, `genvm-linter` 0.11.1-rc.2, `genlayer-py` 0.18.0 (pinned by commit in `requirements.txt`).

## 9. Decisions worth knowing

1. A delivery is allowed from `IN_PROGRESS`, `NEEDS_REVISION` and `INCONCLUSIVE` so corrective deliveries work; an unevaluated receipt (for example a dead URL) is marked `SUPERSEDED` instead of blocking the job, and still counts toward the limit of 2.
2. `reason_code` is fixed 1:1 per status: `MET` -> `MEETS_CRITERION`, `NOT_MET` -> `REQUIREMENT_NOT_MET`, `UNVERIFIABLE` -> `INSUFFICIENT_EVIDENCE`.
3. Ids are counter-based strings (`job-1`, `rcpt-1`), never derived from wallets.
4. Infrastructure and format failures abort the transaction (nothing stored); only a completed evaluation can produce `INCONCLUSIVE`.
5. Statuses must match exactly across validators, favoring safety over liveness: an unstable model on a borderline criterion causes disagreement and leader rotation.

## 10. Known limitations

- Prompt-injection resistance rests on delimiters plus code-level constraints; a model fooled identically on every node can still approve a criterion that quotes a real sentence of the document.
- A quote proves presence, not relevance.
- Up to the maximum quote length (300 characters) per criterion of the deliverable is stored publicly on chain (at most 20 quotes per receipt); the raw document is not.
- Either party can trigger an evaluation at a moment of their choosing; the stored `evidence_hash` proves which bytes were judged.
- After `max_deliveries` deliveries a job cannot progress further.

## 11. What really bounds the limits (read before raising them or building a client)

The numbers above are ceilings that were **tested for logic**, not measured on a live network:

| Concern | What is known | What is not |
|---|---|---|
| Prompt size | The whole deliverable and **every criterion** go into one prompt per node. At the maximum (250,000-byte deliverable, 20 criteria of 1,000 characters) the prompt is **272,098 characters** (measured in tests) | The context window of the validators' models is not documented in the sources consulted and was not measured. A model with a smaller window fails with `[LLM_ERROR]` and the transaction is retried by the protocol, so oversize inputs cost liveness, not safety |
| Liveness | Every criterion must match **exactly** on every validator. More criteria means more chances of one borderline disagreement (leader rotation, or an undetermined transaction) | The disagreement rate of real models as a function of the number of criteria was not measured |
| Transaction input | `create_job` can carry about 26 KB of characters at the maximum (up to ~4x more bytes with non-ASCII text) | No GenLayer calldata/transaction size limit was found in the documentation; acceptance of such transactions was not tested on a network |
| Stored and returned data | Worst-case `get_job` payload about 26 KB, `get_receipt` about 5.8 KB (measured); both bounded and calldata-serializable | Storage cost/gas was not measured |
| Hashing and text handling | SHA-256 of 250,000 bytes, whitespace normalization, the prompt builder, all validations and the verdict checks produce identical results in GenVM's own CPython (normal and deterministic builds, both runner generations) | - |

Practical guidance for an API/SDK: read `get_limits()` at start-up and validate client-side; **start with lower soft limits than the ceilings** (for example the previous values: 5 criteria, 100,000 bytes) and raise them as you measure real validators; prefer short, objective, independently checkable criteria. The ceilings are one constant each, so they can be lowered in the contract without touching logic.
