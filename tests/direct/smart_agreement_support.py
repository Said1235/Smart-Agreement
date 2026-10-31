"""Shared helpers for the Smart Agreement direct-mode tests.

Consensus model used by the attack tests
-----------------------------------------
Direct mode only executes the LEADER path; the validator is captured and has to
be invoked explicitly (direct_vm.run_validator). To model the real protocol:

  1. the leader executes and proposes a result (``capture_leader``);
  2. the state is reverted: in the protocol, the leader's result is NOT applied
     until the validators accept it;
  3. a FORGED result is submitted to the validator, which must reject it;
  4. the receipt must still be PENDING (nothing was stored).
"""

import copy
import hashlib
import json
import os
import sys

# Mutation/audit tooling points the whole suite at a mutated COPY via this variable, so the
# real contract file is never modified.
CONTRACT = os.environ.get("SMART_AGREEMENT_CONTRACT", "contracts/smart_agreement.py")
URL = "https://example.com/deliverable.md"
URL_2 = "https://example.com/deliverable-v2.md"

BODY = (
    "Smart Agreement sample deliverable\n"
    "\n"
    "The service exposes a GET /users endpoint returning JSON.\n"
    "Test coverage reached 95% across the whole suite.\n"
    "License: MIT\n"
)
BODY_BYTES = BODY.encode("utf-8")
BODY_SHA256 = hashlib.sha256(BODY_BYTES).hexdigest()

CRITERIA = [
    "The deliverable documents a GET /users endpoint",
    "Test coverage is above 90%",
    "The deliverable declares the MIT license",
]

QUOTES = {
    "C1": "exposes a GET /users endpoint",
    "C2": "Test coverage reached 95%",
    "C3": "License: MIT",
}


def hex_of(address) -> str:
    """Checksummed hex of a fixture address (fixtures are raw 20-byte values)."""
    if hasattr(address, "as_hex"):
        return address.as_hex
    from genlayer.py.types import Address  # importable once a contract was deployed

    return Address(address).as_hex


# Single source of truth for the limits the tests expect. A test (test_smart_agreement_limits.py) asserts
# that the contract's constants and get_limits() equal this table, so a changed limit cannot leave
# stale numbers behind in the suite.
LIMITS = {
    "min_criteria": 1,
    "max_criteria": 20,
    "max_deliveries": 2,
    "max_title_len": 200,
    "max_description_len": 5000,
    "max_criterion_len": 1000,
    "max_url_len": 2048,
    "max_content_bytes": 250000,
    "min_excerpt_len": 8,
    "max_excerpt_len": 300,
}


def numbered_criteria(count: int, length: int = 60) -> list:
    """``count`` distinct criteria of exactly ``length`` characters."""
    out = []
    for k in range(1, count + 1):
        prefix = f"Requirement {k}: "
        out.append(prefix + "x" * (length - len(prefix)))
    return out


def requirement_quote(k: int) -> str:
    return f"Requirement {k} is fully satisfied by this deliverable."


def document_for(count: int, total_bytes: int = 0) -> str:
    """A deliverable that states every requirement once; padded with filler to EXACTLY
    ``total_bytes`` bytes (ASCII) when given."""
    body = "Large deliverable.\n" + "\n".join(requirement_quote(k) for k in range(1, count + 1)) + "\n"
    if total_bytes:
        missing = total_bytes - len(body.encode("utf-8"))
        assert missing >= 0
        filler = ("lorem ipsum dolor sit amet " * (missing // 27 + 1))[:missing]
        body += filler
        assert len(body.encode("utf-8")) == total_bytes
    return body


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def item(cid, status, quote=""):
    return {"criterion_id": cid, "status": status, "evidence_excerpt": quote}


def llm_payload(*items) -> str:
    return json.dumps({"results": list(items)})


def all_met():
    return [item(cid, "MET", QUOTES[cid]) for cid in ("C1", "C2", "C3")]


def mock_world(vm, body=BODY, llm=None, status=200):
    """Reset the mocks: the web returns ``body`` and the LLM returns the ``llm`` list."""
    vm.clear_mocks()
    if isinstance(body, str):
        body = body.encode("utf-8")
    vm.mock_web(r"example\.com", {"status": status, "body": body})
    vm.mock_llm(r".*", llm_payload(*(llm if llm is not None else all_met())))


def module(vm):
    """Python module of the contract loaded in direct mode (pure functions)."""
    return sys.modules["_contract_smart_agreement"]


def new_job(vm, deploy, client, criteria=None, title="API task", description="Build an API"):
    contract = deploy(CONTRACT)
    vm.sender = client
    job_id = contract.create_job(title, description, criteria if criteria is not None else CRITERIA)
    return contract, job_id


def in_progress(vm, deploy, client, worker, url=URL, criteria=None):
    """Accepted job with one submitted delivery. Returns (contract, job_id, receipt_id)."""
    contract, job_id = new_job(vm, deploy, client, criteria=criteria)
    vm.sender = worker
    contract.accept_job(job_id)
    receipt_id = contract.submit_delivery(job_id, url)
    return contract, job_id, receipt_id


def capture_leader(vm, contract, sender, receipt_id):
    """The leader proposes; the state is reverted until a validator accepts."""
    snap = vm.snapshot()
    vm.sender = sender
    contract.evaluate_delivery(receipt_id)
    captured = vm._captured_validators[-1]
    proposal = copy.deepcopy(captured[0])
    vm.revert(snap)
    vm._captured_validators.append(captured)
    return proposal


def validator_accepts(vm, proposal) -> bool:
    """Validator vote on a proposed (possibly forged) result."""
    return vm.run_validator(leader_result=proposal) is True


def assert_unapplied(contract, job_id, receipt_id):
    """A rejected result never reaches storage."""
    receipt = contract.get_receipt(receipt_id)
    assert receipt["status"] == "PENDING"
    assert receipt["overall_status"] == ""
    assert receipt["evidence_hash"] == ""
    assert receipt["results"] == []
    assert contract.get_job(job_id)["status"] == "IN_PROGRESS"
