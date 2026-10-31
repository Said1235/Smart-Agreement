"""Smart Agreement - expanded limits (direct mode).

Covers the larger criteria count (1-20), text sizes and deliverable size: exact boundaries,
characters versus bytes, the worst-case end-to-end flow, ordering with more than nine criteria,
equivalence attacks and differential fuzzing at scale, and the ``get_limits`` view an SDK/API
uses to validate input before sending a transaction.
"""

import copy
import json
import random
import time

import pytest

from tests.direct import test_smart_agreement_audit as audit
from tests.direct.test_smart_agreement_equivalence import derive, set_result
from tests.direct.smart_agreement_support import (
    CONTRACT,
    LIMITS,
    URL,
    assert_unapplied,
    capture_leader,
    document_for,
    in_progress,
    item,
    mock_world,
    module,
    new_job,
    numbered_criteria,
    requirement_quote,
    sha256_hex,
    validator_accepts,
)

MAX_C = LIMITS["max_criteria"]


def all_met_for(count):
    return [item(f"C{k}", "MET", requirement_quote(k)) for k in range(1, count + 1)]


# --------------------------------------------------------------------------- #
# get_limits: the SDK contract
# --------------------------------------------------------------------------- #


def test_get_limits_is_exact_and_matches_the_contract_constants(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    limits = contract.get_limits()
    assert limits == {"schema_version": 1, **LIMITS}
    mod = module(direct_vm)
    constants = {
        "min_criteria": mod.MIN_CRITERIA, "max_criteria": mod.MAX_CRITERIA, "max_deliveries": mod.MAX_DELIVERIES,
        "max_title_len": mod.MAX_TITLE_LEN, "max_description_len": mod.MAX_DESCRIPTION_LEN,
        "max_criterion_len": mod.MAX_CRITERION_LEN, "max_url_len": mod.MAX_URL_LEN,
        "max_content_bytes": mod.MAX_CONTENT_BYTES, "min_excerpt_len": mod.MIN_EXCERPT_LEN,
        "max_excerpt_len": mod.MAX_EXCERPT_LEN,
    }
    assert constants == LIMITS  # a stale table in the tests fails here
    from genlayer.py import calldata  # importable once a contract has been deployed

    assert calldata.decode(calldata.encode(limits)) == limits
    assert all(type(v) is int for v in limits.values())


def test_get_limits_does_not_depend_on_state_or_caller(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract, job_id, _ = in_progress(direct_vm, direct_deploy, direct_alice, direct_bob)
    before = contract.get_limits()
    direct_vm.sender = direct_bob
    assert contract.get_limits() == before


def test_limits_are_self_consistent():
    assert 1 <= LIMITS["min_criteria"] <= LIMITS["max_criteria"]
    assert LIMITS["min_excerpt_len"] < LIMITS["max_excerpt_len"] <= LIMITS["max_content_bytes"]
    assert LIMITS["max_url_len"] > len("https://x.io/")


# --------------------------------------------------------------------------- #
# create_job boundaries (exact maximum accepted, one more rejected)
# --------------------------------------------------------------------------- #


def create(contract, vm, client, title="T", description="D", criteria=None):
    vm.sender = client
    return contract.create_job(title, description, criteria if criteria is not None else ["first criterion", "second criterion"])


@pytest.mark.parametrize(
    "field,max_key,message",
    [("title", "max_title_len", "title is too long"), ("description", "max_description_len", "description is too long")],
)
def test_text_fields_accept_exactly_the_maximum_and_reject_one_more(direct_vm, direct_deploy, direct_alice, field, max_key, message):
    contract = direct_deploy(CONTRACT)
    limit = LIMITS[max_key]
    job_id = create(contract, direct_vm, direct_alice, **{field: "x" * limit})
    assert len(contract.get_job(job_id)[field]) == limit
    with direct_vm.expect_revert(message):
        create(contract, direct_vm, direct_alice, **{field: "x" * (limit + 1)})


def test_criterion_accepts_exactly_the_maximum_and_rejects_one_more(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy(CONTRACT)
    limit = LIMITS["max_criterion_len"]
    job_id = create(contract, direct_vm, direct_alice, criteria=["a" * limit, "b" * limit])
    assert [len(c["text"]) for c in contract.get_job(job_id)["criteria"]] == [limit, limit]
    with direct_vm.expect_revert("criterion is too long"):
        create(contract, direct_vm, direct_alice, criteria=["a" * limit, "b" * (limit + 1)])


@pytest.mark.parametrize("count", [LIMITS["min_criteria"], 2, 9, 10, 11, LIMITS["max_criteria"]])
def test_every_criteria_count_in_range_is_accepted_with_stable_ids(direct_vm, direct_deploy, direct_alice, count):
    contract = direct_deploy(CONTRACT)
    job_id = create(contract, direct_vm, direct_alice, criteria=numbered_criteria(count))
    job = contract.get_job(job_id)
    assert [c["id"] for c in job["criteria"]] == [f"C{k}" for k in range(1, count + 1)]
    assert [c["text"] for c in job["criteria"]] == numbered_criteria(count)


@pytest.mark.parametrize("count", [0, MAX_C + 1, MAX_C + 5])
def test_criteria_counts_outside_the_range_are_rejected(direct_vm, direct_deploy, direct_alice, count):
    contract = direct_deploy(CONTRACT)
    with direct_vm.expect_revert("criteria count must be between"):
        create(contract, direct_vm, direct_alice, criteria=numbered_criteria(count))
    assert contract.get_job("job-1")["found"] is False


def test_duplicate_detection_still_works_at_twenty_criteria(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy(CONTRACT)
    criteria = numbered_criteria(MAX_C)
    criteria[MAX_C - 1] = criteria[0].upper()  # the last one repeats the first (case-insensitive)
    with direct_vm.expect_revert("criteria must be distinct"):
        create(contract, direct_vm, direct_alice, criteria=criteria)


def test_limits_count_characters_not_bytes(direct_vm, direct_deploy, direct_alice):
    """Multi-byte text up to the character limits is accepted (its UTF-8 size may be 4x larger)."""
    contract = direct_deploy(CONTRACT)
    job_id = create(
        contract, direct_vm, direct_alice,
        title="\u00e9" * LIMITS["max_title_len"],
        description="\U0001F600" * LIMITS["max_description_len"],
        criteria=["\u4e2d" * LIMITS["max_criterion_len"], "\u00fc" * LIMITS["max_criterion_len"]],
    )
    job = contract.get_job(job_id)
    assert len(job["title"]) == LIMITS["max_title_len"]
    assert len(job["description"].encode("utf-8")) == 4 * LIMITS["max_description_len"]
    with direct_vm.expect_revert("description is too long"):
        create(contract, direct_vm, direct_alice, description="\U0001F600" * (LIMITS["max_description_len"] + 1))


def test_control_characters_are_still_rejected_at_maximum_size(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy(CONTRACT)
    with direct_vm.expect_revert("control characters"):
        create(contract, direct_vm, direct_alice, description="x" * (LIMITS["max_description_len"] - 1) + "\x00")
    with direct_vm.expect_revert("control characters"):
        create(contract, direct_vm, direct_alice, criteria=["ok criterion", "y" * (LIMITS["max_criterion_len"] - 1) + "\x07"])


def test_url_accepts_exactly_the_maximum_length_and_rejects_one_more(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract, job_id = new_job(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_bob
    contract.accept_job(job_id)
    prefix = "https://example.com/"
    exact = prefix + "a" * (LIMITS["max_url_len"] - len(prefix))
    assert len(exact) == LIMITS["max_url_len"]
    receipt_id = contract.submit_delivery(job_id, exact)
    assert contract.get_receipt(receipt_id)["url"] == exact
    with direct_vm.expect_revert("url length"):
        contract.submit_delivery(job_id, exact + "a")


# --------------------------------------------------------------------------- #
# Deliverable size: the exact byte boundary through the full evaluation
# --------------------------------------------------------------------------- #


def evaluate(contract, vm, sender, receipt_id):
    vm.sender = sender
    contract.evaluate_delivery(receipt_id)
    return contract.get_receipt(receipt_id)


def test_a_deliverable_of_exactly_the_maximum_size_is_evaluated_and_hashed(direct_vm, direct_deploy, direct_alice, direct_bob):
    count = 3
    contract, job_id, rid = in_progress(direct_vm, direct_deploy, direct_alice, direct_bob, criteria=numbered_criteria(count))
    body = document_for(count, LIMITS["max_content_bytes"])
    mock_world(direct_vm, body=body, llm=all_met_for(count))
    receipt = evaluate(contract, direct_vm, direct_alice, rid)
    assert receipt["overall_status"] == "VERIFIED"
    assert receipt["evidence_size"] == LIMITS["max_content_bytes"] == len(body.encode())
    assert receipt["evidence_hash"] == sha256_hex(body.encode())  # independent hash of the exact bytes


def test_one_byte_more_is_rejected_safely(direct_vm, direct_deploy, direct_alice, direct_bob):
    count = 3
    contract, job_id, rid = in_progress(direct_vm, direct_deploy, direct_alice, direct_bob, criteria=numbered_criteria(count))
    mock_world(direct_vm, body=document_for(count, LIMITS["max_content_bytes"] + 1), llm=all_met_for(count))
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("[EXTERNAL] content exceeds size limit"):
        contract.evaluate_delivery(rid)
    assert_unapplied(contract, job_id, rid)


def test_the_size_limit_counts_bytes_not_characters(direct_vm, direct_deploy, direct_alice, direct_bob):
    count = 2
    base = document_for(count)
    pad = (LIMITS["max_content_bytes"] - len(base.encode())) // 2
    exact = base + "\u00e9" * pad  # 2 bytes per character
    exact += "z" * (LIMITS["max_content_bytes"] - len(exact.encode()))
    assert len(exact.encode()) == LIMITS["max_content_bytes"] and len(exact) < LIMITS["max_content_bytes"]
    contract, job_id, rid = in_progress(direct_vm, direct_deploy, direct_alice, direct_bob, criteria=numbered_criteria(count))
    mock_world(direct_vm, body=exact, llm=all_met_for(count))
    assert evaluate(contract, direct_vm, direct_alice, rid)["overall_status"] == "VERIFIED"

    # a second agreement on the same contract: one byte more than the maximum
    direct_vm.sender = direct_alice
    job_2 = contract.create_job("T2", "D2", numbered_criteria(count))
    direct_vm.sender = direct_bob
    contract.accept_job(job_2)
    rid_2 = contract.submit_delivery(job_2, URL)
    mock_world(direct_vm, body=exact + "z", llm=all_met_for(count))
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("[EXTERNAL] content exceeds size limit"):
        contract.evaluate_delivery(rid_2)
    assert_unapplied(contract, job_2, rid_2)


def test_invalid_utf8_is_still_rejected_at_the_maximum_size(direct_vm, direct_deploy, direct_alice, direct_bob):
    count = 2
    contract, job_id, rid = in_progress(direct_vm, direct_deploy, direct_alice, direct_bob, criteria=numbered_criteria(count))
    body = document_for(count, LIMITS["max_content_bytes"] - 2).encode() + b"\xff\xfe"
    assert len(body) == LIMITS["max_content_bytes"]
    mock_world(direct_vm, body=body, llm=all_met_for(count))
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("[EXTERNAL] content is not valid utf-8"):
        contract.evaluate_delivery(rid)
    assert_unapplied(contract, job_id, rid)


# --------------------------------------------------------------------------- #
# Worst case end to end: everything at its maximum at once
# --------------------------------------------------------------------------- #


def long_quote_document(count):
    """Requirement 1 is backed by a quote of exactly the maximum quote length."""
    long_quote = "a" * LIMITS["max_excerpt_len"]
    return long_quote, document_for(count, LIMITS["max_content_bytes"] - len(long_quote) - 1) + " " + long_quote


def test_worst_case_everything_at_its_maximum_end_to_end(direct_vm, direct_deploy, direct_alice, direct_bob):
    criteria = numbered_criteria(MAX_C, LIMITS["max_criterion_len"])
    title, description = "T" * LIMITS["max_title_len"], "d" * LIMITS["max_description_len"]
    url = "https://example.com/" + "u" * (LIMITS["max_url_len"] - len("https://example.com/"))
    contract = direct_deploy(CONTRACT)
    from genlayer.py import calldata  # importable once a contract has been deployed

    direct_vm.sender = direct_alice
    job_id = contract.create_job(title, description, criteria)
    direct_vm.sender = direct_bob
    contract.accept_job(job_id)
    receipt_id = contract.submit_delivery(job_id, url)

    long_quote, body = long_quote_document(MAX_C)
    assert len(body.encode()) == LIMITS["max_content_bytes"]
    results = all_met_for(MAX_C)
    results[0] = item("C1", "MET", long_quote)

    prompts = []
    direct_vm.clear_mocks()
    direct_vm.mock_web(r"example\.com", {"status": 200, "body": body})
    direct_vm._live_llm_handler = lambda data: (prompts.append(data["prompt"]) or {"ok": {"results": results}})

    started = time.time()
    receipt = evaluate(contract, direct_vm, direct_alice, receipt_id)
    elapsed = time.time() - started

    assert receipt["overall_status"] == "VERIFIED" and len(receipt["results"]) == MAX_C
    assert [r["criterion_id"] for r in receipt["results"]] == [f"C{k}" for k in range(1, MAX_C + 1)]
    assert receipt["results"][0]["evidence_excerpt"] == long_quote and len(long_quote) == LIMITS["max_excerpt_len"]
    assert receipt["evidence_hash"] == sha256_hex(body.encode()) and receipt["url"] == url
    assert contract.get_job(job_id)["status"] == "VERIFIED"

    # the model saw every criterion (from storage) and the whole deliverable
    prompt = prompts[0]
    assert all(c in prompt for c in criteria) and prompt.rstrip().endswith("<<<DELIVERABLE_END>>>")
    assert len(prompt) < LIMITS["max_content_bytes"] + MAX_C * (LIMITS["max_criterion_len"] + 60) + 5000
    assert "at least 8 and at most 300 characters" in prompt  # the prompt states the enforced quote bounds
    assert elapsed < 30, f"worst-case evaluation took {elapsed:.1f}s in direct mode"

    # the payloads an API/SDK will receive are bounded and serializable
    job_payload, receipt_payload = contract.get_job(job_id), contract.get_receipt(receipt_id)
    for value in (job_payload, receipt_payload, contract.get_job_receipts(job_id)):
        assert calldata.decode(calldata.encode(value)) == value
    assert len(json.dumps(job_payload)) < 40000 and len(json.dumps(receipt_payload)) < 20000
    print(f"WORST CASE: prompt={len(prompt)} chars, job={len(json.dumps(job_payload))} B, "
          f"receipt={len(json.dumps(receipt_payload))} B, direct-mode {elapsed:.2f}s")


def test_a_quote_longer_than_the_maximum_is_cut_to_a_literal_prefix_not_rejected(direct_vm, direct_deploy, direct_alice, direct_bob):
    count = 2
    contract, job_id, rid = in_progress(direct_vm, direct_deploy, direct_alice, direct_bob, criteria=numbered_criteria(count))
    too_long = "b" * (LIMITS["max_excerpt_len"] + 1)
    body = document_for(count) + " " + too_long
    results = all_met_for(count)
    results[0] = item("C1", "MET", too_long)
    mock_world(direct_vm, body=body, llm=results)
    receipt = evaluate(contract, direct_vm, direct_alice, rid)
    assert receipt["results"][0]["status"] == "MET"
    assert receipt["results"][0]["evidence_excerpt"] == "b" * LIMITS["max_excerpt_len"]


def test_the_quote_minimum_still_applies_at_scale(direct_vm, direct_deploy, direct_alice, direct_bob):
    count = 12
    contract, job_id, rid = in_progress(direct_vm, direct_deploy, direct_alice, direct_bob, criteria=numbered_criteria(count))
    results = all_met_for(count)
    results[7] = item("C8", "MET", "Requir")  # 6 characters: literal but not substantive
    mock_world(direct_vm, body=document_for(count), llm=results)
    receipt = evaluate(contract, direct_vm, direct_alice, rid)
    assert receipt["results"][7]["status"] == "UNVERIFIABLE" and receipt["overall_status"] == "INCONCLUSIVE"
    assert all(r["status"] == "MET" for i, r in enumerate(receipt["results"]) if i != 7)


# --------------------------------------------------------------------------- #
# Ordering with more than nine criteria (C10 sorts before C2 as text)
# --------------------------------------------------------------------------- #


def test_results_follow_criteria_order_not_text_order(direct_vm, direct_deploy, direct_alice, direct_bob):
    count = 12
    contract, job_id, rid = in_progress(direct_vm, direct_deploy, direct_alice, direct_bob, criteria=numbered_criteria(count))
    mock_world(direct_vm, body=document_for(count), llm=all_met_for(count))
    receipt = evaluate(contract, direct_vm, direct_alice, rid)
    assert [r["criterion_id"] for r in receipt["results"]] == [f"C{k}" for k in range(1, count + 1)]
    assert [r["criterion_id"] for r in receipt["results"]][:3] == ["C1", "C2", "C3"]  # not C1, C10, C11


def test_a_model_that_answers_in_text_order_is_rejected_not_accepted(direct_vm, direct_deploy, direct_alice, direct_bob):
    count = 12
    contract, job_id, rid = in_progress(direct_vm, direct_deploy, direct_alice, direct_bob, criteria=numbered_criteria(count))
    text_order = sorted(all_met_for(count), key=lambda r: r["criterion_id"])  # C1, C10, C11, C12, C2, ...
    mock_world(direct_vm, body=document_for(count), llm=text_order)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("[LLM_ERROR]"):
        contract.evaluate_delivery(rid)
    assert_unapplied(contract, job_id, rid)


# --------------------------------------------------------------------------- #
# A single criterion is a valid job
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "status,quote,overall",
    [("MET", requirement_quote(1), "VERIFIED"), ("NOT_MET", "", "NEEDS_REVISION"), ("UNVERIFIABLE", "", "INCONCLUSIVE")],
)
def test_single_criterion_jobs_resolve_correctly(direct_vm, direct_deploy, direct_alice, direct_bob, status, quote, overall):
    contract, job_id, rid = in_progress(direct_vm, direct_deploy, direct_alice, direct_bob, criteria=numbered_criteria(1))
    mock_world(direct_vm, body=document_for(1), llm=[item("C1", status, quote)])
    receipt = evaluate(contract, direct_vm, direct_alice, rid)
    assert receipt["overall_status"] == overall and len(receipt["results"]) == 1
    assert contract.get_job(job_id)["status"] == overall


def test_single_criterion_forgeries_are_rejected(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract, job_id, rid = in_progress(direct_vm, direct_deploy, direct_alice, direct_bob, criteria=numbered_criteria(1))
    mock_world(direct_vm, body=document_for(1), llm=all_met_for(1))
    proposal = capture_leader(direct_vm, contract, direct_alice, rid)
    for forge in (lambda p: p.update(results=[]), lambda p: p["results"].append(copy.deepcopy(p["results"][0])),
                  lambda p: p["results"][0].update(criterion_id="C2"), lambda p: p.update(overall_status="INCONCLUSIVE")):
        forged = copy.deepcopy(proposal)
        forge(forged)
        assert validator_accepts(direct_vm, forged) is False
        assert_unapplied(contract, job_id, rid)


# --------------------------------------------------------------------------- #
# Equivalence attacks at twenty criteria
# --------------------------------------------------------------------------- #


@pytest.fixture
def twenty(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract, job_id, rid = in_progress(direct_vm, direct_deploy, direct_alice, direct_bob, criteria=numbered_criteria(MAX_C))
    mock_world(direct_vm, body=document_for(MAX_C), llm=all_met_for(MAX_C))
    return contract, job_id, rid, capture_leader(direct_vm, contract, direct_alice, rid)


def test_the_honest_twenty_criteria_result_is_accepted(direct_vm, twenty):
    assert validator_accepts(direct_vm, twenty[3]) is True and len(twenty[3]["results"]) == MAX_C


def _swap(p, i, j):
    p["results"][i], p["results"][j] = p["results"][j], p["results"][i]


TWENTY_FORGERIES = {
    "drop_the_last_criterion": lambda p: p["results"].pop(),
    "drop_a_middle_criterion": lambda p: p["results"].pop(9),
    "duplicate_the_last_criterion": lambda p: p["results"].append(copy.deepcopy(p["results"][-1])),
    "replace_C20_by_a_copy_of_C19": lambda p: p["results"].__setitem__(19, copy.deepcopy(p["results"][18])),
    "extra_criterion_C21": lambda p: p["results"].append({**copy.deepcopy(p["results"][-1]), "criterion_id": "C21"}),
    "swap_C2_and_C10_text_order_trap": lambda p: _swap(p, 1, 9),
    "swap_C9_and_C10": lambda p: _swap(p, 8, 9),
    "swap_C19_and_C20": lambda p: _swap(p, 18, 19),
    "reverse_all": lambda p: p["results"].reverse(),
    "rename_C20_to_C21": lambda p: p["results"][19].update(criterion_id="C21"),
    "rename_C15_to_C015": lambda p: p["results"][14].update(criterion_id="C015"),
    "verified_with_one_unmet": lambda p: p["results"][14].update(status="NOT_MET", reason_code="REQUIREMENT_NOT_MET", evidence_excerpt=""),
    "reason_contradicts_status_at_C17": lambda p: p["results"][16].update(reason_code="INSUFFICIENT_EVIDENCE"),
    "fabricated_quote_at_C20": lambda p: set_result(p, 19, "MET", "this sentence is not in the deliverable at all"),
    "quote_over_the_maximum_at_C7": lambda p: set_result(p, 6, "NOT_MET", "a" * (LIMITS["max_excerpt_len"] + 1)),
    "evidence_size_above_the_maximum": lambda p: p.update(evidence_size=LIMITS["max_content_bytes"] + 1),
    "overall_mismatch": lambda p: p.update(overall_status="NEEDS_REVISION"),
}


@pytest.mark.parametrize("name", sorted(TWENTY_FORGERIES))
def test_forged_twenty_criteria_results_are_rejected_and_not_stored(direct_vm, twenty, name):
    contract, job_id, rid, proposal = twenty
    forged = copy.deepcopy(proposal)
    TWENTY_FORGERIES[name](forged)
    assert repr(forged) != repr(proposal)
    assert validator_accepts(direct_vm, forged) is False
    assert_unapplied(contract, job_id, rid)


def test_a_single_flipped_status_among_twenty_is_caught_by_the_independent_validator(direct_vm, twenty):
    contract, job_id, rid, proposal = twenty
    for index in (0, 9, 10, 19):
        flipped = all_met_for(MAX_C)
        flipped[index] = item(f"C{index + 1}", "NOT_MET", "")
        mock_world(direct_vm, body=document_for(MAX_C), llm=flipped)  # what the validator's own model concludes
        assert validator_accepts(direct_vm, proposal) is False
    assert_unapplied(contract, job_id, rid)


def test_statuses_agreeing_on_nineteen_of_twenty_still_reject(direct_vm, twenty):
    """Same overall category (everything else identical): one disagreeing criterion is enough."""
    contract, job_id, rid, proposal = twenty
    shifted = all_met_for(MAX_C)
    shifted[12] = item("C13", "UNVERIFIABLE", "")
    mock_world(direct_vm, body=document_for(MAX_C), llm=shifted)
    assert validator_accepts(direct_vm, proposal) is False


def test_different_but_real_quotes_are_accepted_at_scale(direct_vm, twenty):
    contract, job_id, rid, proposal = twenty
    other = [item(f"C{k}", "MET", f"Requirement {k} is fully satisfied") for k in range(1, MAX_C + 1)]  # real, shorter quotes
    mock_world(direct_vm, body=document_for(MAX_C), llm=other)
    assert validator_accepts(direct_vm, proposal) is True


# --------------------------------------------------------------------------- #
# Differential fuzz with many criteria against the independent oracle
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("count", [12, MAX_C])
@pytest.mark.parametrize("seed", [1, 2])
def test_differential_fuzz_with_many_criteria(direct_vm, direct_deploy, direct_alice, direct_bob, monkeypatch, count, seed):
    contract, job_id, rid = in_progress(direct_vm, direct_deploy, direct_alice, direct_bob, criteria=numbered_criteria(count))
    body = document_for(count)
    content = " ".join(body.split())
    mock_world(direct_vm, body=body, llm=all_met_for(count))
    honest = capture_leader(direct_vm, contract, direct_alice, rid)
    # quotes that are real for THIS document, so near-misses can actually be accepted
    monkeypatch.setattr(audit, "QUOTE_POOL", audit.QUOTE_POOL + [requirement_quote(k) for k in (1, count)] + ["Large deliverable.", "Requirement 3 is fully"])
    assert audit.oracle(honest, honest, content) and validator_accepts(direct_vm, honest)

    rng = random.Random(seed * 31337 + count)
    accepted, disagreements, total = 0, [], 300
    for _ in range(total):
        forged = audit.mutate(honest, rng)
        expected = audit.oracle(forged, honest, content)
        actual = validator_accepts(direct_vm, forged)
        accepted += expected
        if expected != actual:
            disagreements.append((expected, actual, json.dumps(forged, default=repr)[:300]))
    assert not disagreements, f"{len(disagreements)} disagreements; first: {disagreements[:2]}"
    assert 0 < accepted < total
