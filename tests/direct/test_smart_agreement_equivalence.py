"""Smart Agreement - negative equivalence-principle tests (direct mode).

Every attack forges the result a malicious (or faulty) leader could propose and
checks two things:

  1. the validator REJECTS it;
  2. nothing reaches storage (the receipt stays PENDING).

See ``smart_agreement_support`` for how the consensus rule "a result is only applied
when validators accept it" is modelled in direct mode.
"""

import copy
import hashlib

import pytest

from tests.direct.smart_agreement_support import (
    BODY,
    BODY_SHA256,
    LIMITS,
    QUOTES,
    URL,
    URL_2,
    all_met,
    assert_unapplied,
    capture_leader,
    in_progress,
    item,
    mock_world,
    module,
    validator_accepts,
)

REASON = {
    "MET": "MEETS_CRITERION",
    "NOT_MET": "REQUIREMENT_NOT_MET",
    "UNVERIFIABLE": "INSUFFICIENT_EVIDENCE",
}


def derive(statuses):
    """Independent re-implementation of the overall-status rule."""
    if "UNVERIFIABLE" in statuses:
        return "INCONCLUSIVE"
    if "NOT_MET" in statuses:
        return "NEEDS_REVISION"
    return "VERIFIED"


def set_result(proposal, index, status, quote=""):
    """Change one criterion consistently (reason code and overall status follow),
    so that only the targeted rule can reject the forgery."""
    entry = proposal["results"][index]
    entry["status"] = status
    entry["reason_code"] = REASON[status]
    entry["evidence_excerpt"] = quote
    proposal["overall_status"] = derive([r["status"] for r in proposal["results"]])


@pytest.fixture
def scenario(direct_vm, direct_deploy, direct_alice, direct_bob):
    """An honest leader proposal for a pending delivery whose evidence is all-MET."""
    contract, job_id, receipt_id = in_progress(direct_vm, direct_deploy, direct_alice, direct_bob)
    mock_world(direct_vm)
    proposal = capture_leader(direct_vm, contract, direct_alice, receipt_id)
    return contract, job_id, receipt_id, proposal


def assert_rejected(direct_vm, scenario_tuple, forged):
    contract, job_id, receipt_id, _ = scenario_tuple
    assert validator_accepts(direct_vm, forged) is False
    assert_unapplied(contract, job_id, receipt_id)


# --------------------------------------------------------------------------- #
# Baselines
# --------------------------------------------------------------------------- #


def test_honest_leader_result_is_accepted_and_stored_verbatim(
    direct_vm, scenario, direct_alice
):
    contract, job_id, receipt_id, proposal = scenario
    assert validator_accepts(direct_vm, proposal) is True
    assert proposal["evidence_hash"] == BODY_SHA256
    assert proposal["overall_status"] == "VERIFIED"

    # Now let the protocol apply the (validated) leader execution.
    direct_vm.sender = direct_alice
    contract.evaluate_delivery(receipt_id)
    stored = contract.get_receipt(receipt_id)
    # What is persisted is exactly the proposed and validated result.
    assert stored["results"] == proposal["results"]
    assert stored["evidence_hash"] == proposal["evidence_hash"]
    assert stored["evidence_size"] == proposal["evidence_size"]
    assert stored["overall_status"] == proposal["overall_status"]
    assert stored["receipt_id"] == proposal["receipt_id"]
    assert stored["job_id"] == proposal["job_id"]
    assert stored["delivery_index"] == proposal["delivery_index"]
    assert stored["agreement_hash"] == proposal["agreement_hash"]


def test_leader_result_survives_calldata_roundtrip(direct_vm, scenario):
    """In GenVM the leader result crosses the consensus boundary as calldata."""
    from genlayer.py import calldata

    proposal = scenario[3]
    assert calldata.decode(calldata.encode(proposal)) == proposal
    assert validator_accepts(direct_vm, calldata.decode(calldata.encode(proposal))) is True


def test_leader_fields_are_fully_bound(scenario):
    proposal = scenario[3]
    assert set(proposal) == {
        "job_id",
        "receipt_id",
        "delivery_index",
        "agreement_hash",
        "evidence_hash",
        "evidence_size",
        "results",
        "overall_status",
    }
    assert [r["criterion_id"] for r in proposal["results"]] == ["C1", "C2", "C3"]
    assert all(set(r) == {"criterion_id", "status", "reason_code", "evidence_excerpt"} for r in proposal["results"])


# --------------------------------------------------------------------------- #
# Forgeries the validator must reject by itself (structure and bindings)
# --------------------------------------------------------------------------- #


def forge_verified_with_unmet(p):
    p["results"][1].update(status="NOT_MET", reason_code=REASON["NOT_MET"], evidence_excerpt="")
    # overall_status deliberately left as VERIFIED


def forge_fewer_criteria(p):
    p["results"] = p["results"][:2]  # overall (VERIFIED) is still "consistent" with what remains


def forge_extra_criterion(p):
    p["results"].append(
        {"criterion_id": "C4", "status": "MET", "reason_code": REASON["MET"], "evidence_excerpt": QUOTES["C3"]}
    )


def forge_duplicate_criterion(p):
    p["results"][1] = copy.deepcopy(p["results"][0])  # C1 twice, C2 missing


def forge_swapped_ids(p):
    p["results"][0]["criterion_id"], p["results"][1]["criterion_id"] = "C2", "C1"


def forge_reordered(p):
    p["results"].reverse()


def forge_renamed_id(p):
    p["results"][2]["criterion_id"] = "C9"


def forge_overall(value):
    def _forge(p):
        p["overall_status"] = value

    return _forge


def forge_reason_contradicts_status(p):
    p["results"][0]["reason_code"] = REASON["NOT_MET"]  # status MET, reason says not met


def forge_unknown_reason(p):
    p["results"][0]["reason_code"] = "TRUST_ME"


def forge_unknown_status(p):
    p["results"][0]["status"] = "PERFECT"


def forge_field(key, value):
    def _forge(p):
        p[key] = value

    return _forge


def forge_result_field(key, value):
    def _forge(p):
        p["results"][0][key] = value

    return _forge


def forge_drop_key(key):
    def _forge(p):
        del p[key]

    return _forge


def forge_extra_top_key(p):
    p["note"] = "approved by leader"


def forge_extra_result_key(p):
    p["results"][0]["confidence"] = 99


def forge_met_without_quote(p):
    set_result(p, 0, "MET", "")


def forge_unverifiable_with_quote(p):
    set_result(p, 0, "UNVERIFIABLE", QUOTES["C1"])


def forge_non_canonical_quote(p):
    set_result(p, 0, "MET", "exposes  a GET /users endpoint")  # double space


def forge_overlong_quote(p):
    set_result(p, 0, "NOT_MET", "a" * (LIMITS["max_excerpt_len"] + 1))


STRUCTURAL_FORGERIES = {
    "verified_with_one_unmet_criterion": forge_verified_with_unmet,
    "fewer_criteria_than_the_agreement": forge_fewer_criteria,
    "extra_criterion": forge_extra_criterion,
    "duplicated_criterion": forge_duplicate_criterion,
    "swapped_criterion_ids": forge_swapped_ids,
    "reordered_criteria": forge_reordered,
    "renamed_criterion_id": forge_renamed_id,
    "overall_needs_revision_but_all_met": forge_overall("NEEDS_REVISION"),
    "overall_inconclusive_but_all_met": forge_overall("INCONCLUSIVE"),
    "overall_unknown_value": forge_overall("APPROVED"),
    "overall_wrong_type": forge_overall(1),
    "reason_contradicts_status": forge_reason_contradicts_status,
    "unknown_reason_code": forge_unknown_reason,
    "unknown_status": forge_unknown_status,
    "different_job_id": forge_field("job_id", "job-2"),
    "different_receipt_id": forge_field("receipt_id", "rcpt-2"),
    "different_delivery_index": forge_field("delivery_index", 2),
    "delivery_index_is_a_bool": forge_field("delivery_index", True),
    "different_agreement_hash": forge_field("agreement_hash", "0" * 64),
    "empty_agreement_hash": forge_field("agreement_hash", ""),
    "wrong_evidence_hash": forge_field("evidence_hash", hashlib.sha256(b"something else").hexdigest()),
    "short_evidence_hash": forge_field("evidence_hash", "abc123"),
    "uppercase_evidence_hash": forge_field("evidence_hash", BODY_SHA256.upper()),
    "empty_evidence_hash": forge_field("evidence_hash", ""),
    "evidence_hash_wrong_type": forge_field("evidence_hash", 12345),
    "wrong_evidence_size": forge_field("evidence_size", len(BODY.encode()) + 1),
    "zero_evidence_size": forge_field("evidence_size", 0),
    "evidence_size_is_a_bool": forge_field("evidence_size", True),
    "results_is_a_dict": forge_field("results", {"C1": "MET"}),
    "results_is_a_string": forge_field("results", "all good"),
    "results_is_empty": forge_field("results", []),
    "result_entry_is_not_a_dict": forge_field("results", ["MET", "MET", "MET"]),
    "status_wrong_type": forge_result_field("status", 1),
    "excerpt_wrong_type": forge_result_field("evidence_excerpt", ["quote"]),
    "criterion_id_wrong_type": forge_result_field("criterion_id", 1),
    "missing_overall_status": forge_drop_key("overall_status"),
    "missing_evidence_hash": forge_drop_key("evidence_hash"),
    "missing_results": forge_drop_key("results"),
    "extra_top_level_key": forge_extra_top_key,
    "extra_result_key": forge_extra_result_key,
    "met_without_any_quote": forge_met_without_quote,
    "unverifiable_carrying_a_quote": forge_unverifiable_with_quote,
    "non_canonical_whitespace_in_quote": forge_non_canonical_quote,
    "overlong_quote": forge_overlong_quote,
}


@pytest.mark.parametrize("name", sorted(STRUCTURAL_FORGERIES))
def test_forged_result_is_rejected_and_not_stored(direct_vm, scenario, name):
    forged = copy.deepcopy(scenario[3])
    STRUCTURAL_FORGERIES[name](forged)
    # repr(), because in Python True == 1 and a bool/int swap must still count as a mutation
    assert repr(forged) != repr(scenario[3])
    assert_rejected(direct_vm, scenario, forged)


@pytest.mark.parametrize(
    "garbage",
    [None, "VERIFIED", 42, True, [], [1, 2, 3], {}, {"overall_status": "VERIFIED"}, b"bytes"],
)
def test_malformed_leader_result_is_rejected_without_raising(direct_vm, scenario, garbage):
    assert_rejected(direct_vm, scenario, garbage)


# --------------------------------------------------------------------------- #
# Forgeries only independent evidence can expose
# --------------------------------------------------------------------------- #


def test_fabricated_quote_that_is_not_in_the_source_is_rejected(direct_vm, scenario):
    forged = copy.deepcopy(scenario[3])
    set_result(forged, 0, "MET", "The worker also deployed it to production")
    assert_rejected(direct_vm, scenario, forged)


def test_fabricated_quote_on_a_not_met_criterion_is_rejected(direct_vm, scenario):
    forged = copy.deepcopy(scenario[3])
    set_result(forged, 1, "NOT_MET", "Coverage was reported as 12%")
    assert_rejected(direct_vm, scenario, forged)


def test_leader_claims_met_but_independent_validator_finds_not_met(direct_vm, scenario):
    """The leader (all MET) is contradicted by the validator's own evaluation."""
    results = all_met()
    results[1] = item("C2", "NOT_MET")
    mock_world(direct_vm, llm=results)  # what the validator's model concludes
    assert_rejected(direct_vm, scenario, scenario[3])


def test_leader_claims_not_met_but_validator_finds_met(direct_vm, scenario):
    forged = copy.deepcopy(scenario[3])
    set_result(forged, 1, "NOT_MET", "")
    assert_rejected(direct_vm, scenario, forged)


def test_same_overall_status_but_one_individual_result_differs(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """Both sides conclude NEEDS_REVISION, yet they disagree on WHICH criterion failed."""
    contract, job_id, receipt_id = in_progress(direct_vm, direct_deploy, direct_alice, direct_bob)
    leader_view = [item("C1", "MET", QUOTES["C1"]), item("C2", "NOT_MET"), item("C3", "MET", QUOTES["C3"])]
    mock_world(direct_vm, llm=leader_view)
    proposal = capture_leader(direct_vm, contract, direct_alice, receipt_id)
    assert proposal["overall_status"] == "NEEDS_REVISION"

    validator_view = [item("C1", "NOT_MET"), item("C2", "MET", QUOTES["C2"]), item("C3", "MET", QUOTES["C3"])]
    mock_world(direct_vm, llm=validator_view)
    assert_rejected(direct_vm, (contract, job_id, receipt_id, proposal), proposal)


@pytest.mark.parametrize(
    "leader_status,validator_status",
    [("NOT_MET", "UNVERIFIABLE"), ("UNVERIFIABLE", "NOT_MET"), ("MET", "UNVERIFIABLE")],
)
def test_statuses_must_match_exactly_even_when_close(
    direct_vm, direct_deploy, direct_alice, direct_bob, leader_status, validator_status
):
    contract, job_id, receipt_id = in_progress(direct_vm, direct_deploy, direct_alice, direct_bob)

    def view(status):
        quote = QUOTES["C2"] if status == "MET" else ""
        return [item("C1", "MET", QUOTES["C1"]), item("C2", status, quote), item("C3", "MET", QUOTES["C3"])]

    mock_world(direct_vm, llm=view(leader_status))
    proposal = capture_leader(direct_vm, contract, direct_alice, receipt_id)
    mock_world(direct_vm, llm=view(validator_status))
    assert_rejected(direct_vm, (contract, job_id, receipt_id, proposal), proposal)


def test_source_changed_between_leader_and_validator(direct_vm, scenario):
    """Different bytes => different SHA-256 => no approval receipt."""
    mock_world(direct_vm, body=BODY + "\n<!-- edited after the leader fetched it -->\n")
    assert_rejected(direct_vm, scenario, scenario[3])


def test_source_changed_in_a_single_byte(direct_vm, scenario):
    mock_world(direct_vm, body=BODY.replace("95%", "96%"))
    assert_rejected(direct_vm, scenario, scenario[3])


def test_hash_of_another_valid_document_is_not_accepted(direct_vm, scenario):
    other = "Another document.\nLicense: MIT\n"
    forged = copy.deepcopy(scenario[3])
    forged["evidence_hash"] = hashlib.sha256(other.encode()).hexdigest()
    forged["evidence_size"] = len(other.encode())
    assert_rejected(direct_vm, scenario, forged)


def test_honest_hash_but_size_lie_is_rejected(direct_vm, scenario):
    forged = copy.deepcopy(scenario[3])
    forged["evidence_size"] = forged["evidence_size"] + 10
    assert_rejected(direct_vm, scenario, forged)


def test_different_valid_quotes_are_accepted_when_decisions_agree(direct_vm, direct_deploy, direct_alice, direct_bob):
    """Excerpts may differ between evaluators as long as both are real quotes."""
    contract, job_id, receipt_id = in_progress(direct_vm, direct_deploy, direct_alice, direct_bob)
    mock_world(direct_vm, llm=all_met())
    proposal = capture_leader(direct_vm, contract, direct_alice, receipt_id)

    other_quotes = [
        item("C1", "MET", "GET /users endpoint returning JSON"),
        item("C2", "MET", "95% across the whole suite"),
        item("C3", "MET", "sample deliverable"),  # >= 8 chars: a 3-char "MIT" is no longer evidence (audit F1)
    ]
    mock_world(direct_vm, llm=other_quotes)  # the validator's model quotes differently
    assert validator_accepts(direct_vm, proposal) is True


# --------------------------------------------------------------------------- #
# Validator-side failures and leader errors
# --------------------------------------------------------------------------- #


def test_validator_web_failure_rejects_instead_of_approving(direct_vm, scenario):
    mock_world(direct_vm, status=404)
    assert_rejected(direct_vm, scenario, scenario[3])


def test_validator_transient_failure_rejects(direct_vm, scenario):
    mock_world(direct_vm, status=503)
    assert_rejected(direct_vm, scenario, scenario[3])


def test_validator_without_web_access_rejects(direct_vm, scenario):
    direct_vm.clear_mocks()
    assert_rejected(direct_vm, scenario, scenario[3])


def test_validator_model_failure_rejects(direct_vm, scenario):
    direct_vm.clear_mocks()
    direct_vm.mock_web(r"example\.com", {"status": 200, "body": BODY})
    direct_vm.mock_llm(r".*", "I am sorry, I cannot help with that.")
    assert_rejected(direct_vm, scenario, scenario[3])


def test_validator_exception_never_propagates(direct_vm, scenario):
    """An unexpected exception inside the validator is a Disagree, not a crash."""
    direct_vm.clear_mocks()
    direct_vm.mock_web(r"example\.com", {"status": 200, "body": BODY})
    direct_vm.mock_llm(r".*", '{"results": [null, null, null]}')
    assert direct_vm.run_validator(leader_result=scenario[3]) is False


def test_validator_with_invalid_utf8_source_rejects(direct_vm, scenario):
    mock_world(direct_vm, body=b"\xff\xfe\xfa invalid")
    assert_rejected(direct_vm, scenario, scenario[3])


@pytest.mark.parametrize(
    "leader_message,validator_status,expected",
    [
        # deterministic external error: the validator must reproduce it exactly
        ("[EXTERNAL] unexpected http status 404", 404, True),
        ("[EXTERNAL] unexpected http status 404", 403, False),
        ("[EXTERNAL] unexpected http status 404", 200, False),
        # transient errors: both failing transiently is agreement
        ("[TRANSIENT] source temporarily unavailable", 503, True),
        ("[TRANSIENT] source temporarily unavailable", 500, True),
        ("[TRANSIENT] source temporarily unavailable", 200, False),
        # a leader that "failed" while the source works is not trusted
        ("[EXPECTED] receipt is not pending evaluation", 200, False),
    ],
)
def test_leader_error_handling(
    direct_vm, scenario, leader_message, validator_status, expected
):
    mock_world(direct_vm, status=validator_status)
    assert direct_vm.run_validator(leader_error=Exception(leader_message)) is expected


def test_llm_errors_always_force_a_retry(direct_vm, scenario):
    mock_world(direct_vm)
    assert direct_vm.run_validator(leader_error=Exception("[LLM_ERROR] model call failed")) is False
    direct_vm.clear_mocks()
    direct_vm.mock_web(r"example\.com", {"status": 200, "body": BODY})
    direct_vm.mock_llm(r".*", "garbage")  # the validator's model fails too
    assert direct_vm.run_validator(leader_error=Exception("[LLM_ERROR] model call failed")) is False


def test_vm_level_leader_error_is_not_trusted(direct_vm, scenario):
    import genlayer.gl.vm as gl_vm

    validator_fn = direct_vm._captured_validators[-1][2]
    assert validator_fn(gl_vm.VMError("exit_code 1")) is False


# --------------------------------------------------------------------------- #
# Cross-receipt replay
# --------------------------------------------------------------------------- #


def test_result_for_one_receipt_cannot_be_replayed_on_another(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    contract, job_a, rcpt_a = in_progress(direct_vm, direct_deploy, direct_alice, direct_bob)
    direct_vm.sender = direct_alice
    job_b = contract.create_job("Job B", "Task B", ["criterion one", "criterion two", "criterion three"])
    direct_vm.sender = direct_bob
    contract.accept_job(job_b)
    rcpt_b = contract.submit_delivery(job_b, URL)  # same worker, same URL

    mock_world(direct_vm)
    proposal_a = capture_leader(direct_vm, contract, direct_alice, rcpt_a)
    proposal_b = capture_leader(direct_vm, contract, direct_alice, rcpt_b)
    assert proposal_a != proposal_b

    # captured validators: index 0 belongs to receipt A, index 1 to receipt B
    assert direct_vm.run_validator(leader_result=proposal_a, index=0) is True
    assert direct_vm.run_validator(leader_result=proposal_b, index=1) is True
    assert direct_vm.run_validator(leader_result=proposal_a, index=1) is False
    assert direct_vm.run_validator(leader_result=proposal_b, index=0) is False
    assert contract.get_receipt(rcpt_a)["status"] == "PENDING"
    assert contract.get_receipt(rcpt_b)["status"] == "PENDING"


# --------------------------------------------------------------------------- #
# Why a "format-only" validator is not enough
# --------------------------------------------------------------------------- #


def test_a_naive_format_only_validator_would_accept_the_forgery(direct_vm, scenario):
    """Contrast test: documents the exact weakness this design closes."""

    def naive_validator(leader_result):
        data = leader_result.calldata
        return data["overall_status"] in ("VERIFIED", "NEEDS_REVISION", "INCONCLUSIVE")

    import genlayer.gl.vm as gl_vm

    forged = copy.deepcopy(scenario[3])
    forged["evidence_hash"] = "f" * 64
    for index in range(3):
        set_result(forged, index, "MET", "fabricated praise")
    assert naive_validator(gl_vm.Return(calldata=forged)) is True  # rubber stamp
    assert_rejected(direct_vm, scenario, forged)  # Smart Agreement's validator refuses


# --------------------------------------------------------------------------- #
# Pure helpers: the post-consensus guard and the deterministic rules
# --------------------------------------------------------------------------- #


def test_overall_status_rule_truth_table(direct_vm, scenario):
    mod = module(direct_vm)
    assert mod._derive_overall(["MET", "MET", "MET"]) == "VERIFIED"
    assert mod._derive_overall(["MET", "NOT_MET", "MET"]) == "NEEDS_REVISION"
    assert mod._derive_overall(["NOT_MET", "NOT_MET"]) == "NEEDS_REVISION"
    assert mod._derive_overall(["MET", "UNVERIFIABLE"]) == "INCONCLUSIVE"
    assert mod._derive_overall(["NOT_MET", "UNVERIFIABLE"]) == "INCONCLUSIVE"
    assert mod._derive_overall(["UNVERIFIABLE", "UNVERIFIABLE"]) == "INCONCLUSIVE"
    assert mod._derive_overall([]) == "INCONCLUSIVE"  # nothing evaluated is never VERIFIED


def _ctx(contract, job_id, receipt_id):
    job = contract.get_job(job_id)
    return {
        "job_id": job_id,
        "receipt_id": receipt_id,
        "delivery_index": 1,
        "agreement_hash": job["agreement_hash"],
        "criteria": [{"id": c["id"], "text": c["text"]} for c in job["criteria"]],
    }


@pytest.mark.parametrize("name", sorted(STRUCTURAL_FORGERIES))
def test_storage_guard_rejects_every_structural_forgery(direct_vm, scenario, name):
    """The check executed right before storing refuses the same forgeries."""
    contract, job_id, receipt_id, proposal = scenario
    mod = module(direct_vm)
    ctx = _ctx(contract, job_id, receipt_id)
    assert mod._check_verdict_structure(proposal, ctx) is True
    forged = copy.deepcopy(proposal)
    STRUCTURAL_FORGERIES[name](forged)
    # The storage guard is purely structural: it cannot know the evidence bytes.
    # The authenticity of evidence_hash / evidence_size is enforced by the
    # validators' independent fetch (see the dedicated tests above), not here.
    needs_evidence = name in {"wrong_evidence_hash", "wrong_evidence_size"}
    if needs_evidence:
        assert mod._check_verdict_structure(forged, ctx) is True
    else:
        assert mod._check_verdict_structure(forged, ctx) is False


def test_model_normalization_rules(direct_vm, scenario):
    mod = module(direct_vm)
    criteria = [{"id": "C1", "text": "a"}, {"id": "C2", "text": "b"}]
    content = "alpha beta gamma"

    ok = mod._normalize_model_results(
        {"results": [item("C1", "MET", "alpha beta"), item("C2", "NOT_MET", "")]}, criteria, content
    )
    assert [r["status"] for r in ok] == ["MET", "NOT_MET"]
    assert [r["reason_code"] for r in ok] == ["MEETS_CRITERION", "REQUIREMENT_NOT_MET"]

    downgraded = mod._normalize_model_results(
        {"results": [item("C1", "MET", "delta"), item("C2", "UNVERIFIABLE", "gamma")]}, criteria, content
    )
    assert downgraded[0] == {
        "criterion_id": "C1",
        "status": "UNVERIFIABLE",
        "reason_code": "INSUFFICIENT_EVIDENCE",
        "evidence_excerpt": "",
    }
    assert downgraded[1]["evidence_excerpt"] == ""  # UNVERIFIABLE never carries a quote

    long_quote = ("alpha " * 60).strip()
    truncated = mod._normalize_model_results(
        {"results": [item("C1", "MET", long_quote), item("C2", "NOT_MET")]}, criteria, "alpha " * 60
    )
    assert len(truncated[0]["evidence_excerpt"]) <= LIMITS["max_excerpt_len"]
    assert truncated[0]["status"] == "MET"
