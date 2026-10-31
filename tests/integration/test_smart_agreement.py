"""Smart Agreement - integration tests (leader + validators consensus).

Run against a GenLayer network that provides consensus:

    gltest tests/integration/test_smart_agreement.py -v -s

Targets:
  * GenLayer Studio - the reference environment.
  * GLSim - local simulator that runs leader + N validators with majority voting and
    leader rotation. GLSim 0.29.2 breaks every deployment after the first one of the
    same file (it also breaks the boilerplate's FootballBets), so start it with the
    launcher that disables its class cache:

        python tools/run_glsim_nocache.py --port 4000 --validators 5 --no-browser

The tests use MOCK validators so the web evidence and the model answer are
deterministic; consensus itself (leader proposal, every validator running the
independent verification, majority vote, state applied only after acceptance) is
real. A final, opt-in live test uses real LLM validators and a real public URL:

    SMART_AGREEMENT_LIVE_URL=https://... gltest tests/integration/test_smart_agreement.py -k live -s

API note: with genlayer-test 0.29.x a contract method is invoked as
``contract.method(args=[...]).call()`` (reads) or ``.transact(...)`` (writes).
"""

import hashlib
import json
import os

import pytest
from gltest import create_accounts, get_contract_factory, get_validator_factory
from gltest.assertions import tx_execution_succeeded
from gltest.exceptions import DeploymentError

URL = "https://example.com/deliverable.md"
BODY = (
    "Smart Agreement sample deliverable\n\n"
    "The service exposes a GET /users endpoint returning JSON.\n"
    "Test coverage reached 95% across the whole suite.\n"
    "License: MIT\n"
)
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
VALIDATORS = 5
WAIT = {"wait_interval": 10000, "wait_retries": 30}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _llm(*items):
    return json.dumps({"results": list(items)})


def _met(cid):
    return {"criterion_id": cid, "status": "MET", "evidence_excerpt": QUOTES[cid]}


def _unmet(cid):
    return {"criterion_id": cid, "status": "NOT_MET", "evidence_excerpt": ""}


def _context(body=BODY, llm=None, status=200):
    """Validators that all see the same web evidence and answer the model prompt alike."""
    llm = llm if llm is not None else _llm(_met("C1"), _met("C2"), _met("C3"))
    validators = get_validator_factory().batch_create_mock_validators(
        count=VALIDATORS,
        mock_llm_response={"nondet_exec_prompt": {"DELIVERABLE_BEGIN": llm}},
        mock_web_response={
            "nondet_web_request": {
                URL: {"method": "GET", "status": status, "body": body},
            }
        },
    )
    return {"validators": [v.to_dict() for v in validators]}


def _tx(contract, account, method, args, context=None, **wait):
    """State-changing call signed by ``account``; returns the transaction receipt."""
    bound = contract.connect(account)
    return getattr(bound, method)(args=args).transact(
        transaction_context=context, **(wait or WAIT)
    )


def _read(contract, method, args):
    return getattr(contract, method)(args=args).call()


@pytest.fixture
def parties():
    accounts = create_accounts(n_accounts=3)
    return {"client": accounts[0], "worker": accounts[1], "stranger": accounts[2]}


@pytest.fixture
def agreement():
    """A freshly deployed Smart Agreement.

    GLSim 0.29.2 fails every other deployment (also for the boilerplate's own
    FootballBets), so the deployment is retried; on Studio the first attempt wins.
    """
    last = None
    for _ in range(4):
        try:
            return get_contract_factory("SmartAgreement").deploy(args=[])
        except DeploymentError as error:
            last = error
    raise last


def _ready_job(agreement, parties):
    """create_job -> accept_job -> submit_delivery; returns (job_id, receipt_id)."""
    assert tx_execution_succeeded(
        _tx(agreement, parties["client"], "create_job", ["API task", "Build an API", CRITERIA])
    )
    job_id = "job-1"
    assert tx_execution_succeeded(_tx(agreement, parties["worker"], "accept_job", [job_id]))
    assert tx_execution_succeeded(
        _tx(agreement, parties["worker"], "submit_delivery", [job_id, URL])
    )
    receipt_id = _read(agreement, "get_receipt_id", [job_id, 1])
    assert receipt_id == "rcpt-1"
    return job_id, receipt_id


def _evaluate(agreement, account, receipt_id, context=None):
    return _tx(agreement, account, "evaluate_delivery", [receipt_id], context or _context())


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


@pytest.mark.integration
def test_full_flow_reaches_consensus_and_stores_a_verifiable_receipt(agreement, parties):
    job_id, receipt_id = _ready_job(agreement, parties)

    job = _read(agreement, "get_job", [job_id])
    assert job["status"] == "IN_PROGRESS"
    assert job["worker"].lower() == parties["worker"].address.lower()
    assert len(job["agreement_hash"]) == 64

    assert tx_execution_succeeded(_evaluate(agreement, parties["client"], receipt_id))

    receipt = _read(agreement, "get_receipt", [receipt_id])
    assert receipt["status"] == "EVALUATED"
    assert receipt["overall_status"] == "VERIFIED"
    # Hash and size are computed by the contract from the exact bytes it fetched.
    assert receipt["evidence_hash"] == hashlib.sha256(BODY.encode()).hexdigest()
    assert receipt["evidence_size"] == len(BODY.encode())
    assert [r["criterion_id"] for r in receipt["results"]] == ["C1", "C2", "C3"]
    assert all(
        r["status"] == "MET" and r["reason_code"] == "MEETS_CRITERION" for r in receipt["results"]
    )
    normalized = " ".join(BODY.split())
    assert all(r["evidence_excerpt"] in normalized for r in receipt["results"])
    # The receipt is bound to the committed agreement.
    assert receipt["agreement_hash"] == job["agreement_hash"]
    assert receipt["job_id"] == job_id and receipt["delivery_index"] == 1
    assert _read(agreement, "get_job", [job_id])["status"] == "VERIFIED"
    assert _read(agreement, "get_receipt_id", [job_id, 1]) == receipt_id


@pytest.mark.integration
def test_unmet_criterion_needs_revision_and_corrective_delivery(agreement, parties):
    job_id, first = _ready_job(agreement, parties)
    unmet = _context(llm=_llm(_unmet("C1"), _met("C2"), _met("C3")))
    assert tx_execution_succeeded(_evaluate(agreement, parties["client"], first, unmet))

    receipt = _read(agreement, "get_receipt", [first])
    assert receipt["overall_status"] == "NEEDS_REVISION"
    assert [r["status"] for r in receipt["results"]] == ["NOT_MET", "MET", "MET"]
    assert _read(agreement, "get_job", [job_id])["status"] == "NEEDS_REVISION"

    assert tx_execution_succeeded(
        _tx(agreement, parties["worker"], "submit_delivery", [job_id, URL])
    )
    second = _read(agreement, "get_receipt_id", [job_id, 2])
    assert second == "rcpt-2" and second != first
    assert tx_execution_succeeded(_evaluate(agreement, parties["client"], second))
    assert _read(agreement, "get_receipt", [second])["overall_status"] == "VERIFIED"
    # The first receipt keeps its own history.
    assert _read(agreement, "get_receipt", [first])["overall_status"] == "NEEDS_REVISION"
    listed = _read(agreement, "get_job_receipts", [job_id])
    assert [r["receipt_id"] for r in listed] == [first, second]


@pytest.mark.integration
def test_fabricated_approval_cannot_become_verified(agreement, parties):
    """Every validator's own model 'approves' with an invented quote => never VERIFIED."""
    job_id, receipt_id = _ready_job(agreement, parties)
    invented = _llm(
        *[
            {"criterion_id": c, "status": "MET", "evidence_excerpt": "Everything is perfect"}
            for c in ("C1", "C2", "C3")
        ]
    )
    result = _evaluate(agreement, parties["client"], receipt_id, _context(llm=invented))
    assert tx_execution_succeeded(result)
    receipt = _read(agreement, "get_receipt", [receipt_id])
    assert receipt["overall_status"] == "INCONCLUSIVE"
    assert all(r["status"] == "UNVERIFIABLE" for r in receipt["results"])
    assert _read(agreement, "get_job", [job_id])["status"] == "INCONCLUSIVE"


@pytest.mark.integration
def test_unreachable_source_leaves_no_receipt_result(agreement, parties):
    """HTTP 404 is an execution error, not a business decision: nothing is stored."""
    job_id, receipt_id = _ready_job(agreement, parties)
    result = _evaluate(agreement, parties["client"], receipt_id, _context(status=404))
    assert not tx_execution_succeeded(result)
    receipt = _read(agreement, "get_receipt", [receipt_id])
    assert receipt["status"] == "PENDING"
    assert receipt["results"] == [] and receipt["overall_status"] == ""
    assert _read(agreement, "get_job", [job_id])["status"] == "IN_PROGRESS"


@pytest.mark.integration
def test_access_control_and_replay_are_enforced_on_chain(agreement, parties):
    job_id, receipt_id = _ready_job(agreement, parties)

    assert not tx_execution_succeeded(_evaluate(agreement, parties["stranger"], receipt_id))
    assert _read(agreement, "get_receipt", [receipt_id])["status"] == "PENDING"

    # The client cannot deliver on the worker's behalf.
    assert not tx_execution_succeeded(
        _tx(agreement, parties["client"], "submit_delivery", [job_id, URL])
    )

    assert tx_execution_succeeded(_evaluate(agreement, parties["worker"], receipt_id))
    stored = _read(agreement, "get_receipt", [receipt_id])
    assert stored["evaluation_requested_by"].lower() == parties["worker"].address.lower()

    # A second evaluation (even with hostile evidence) cannot overwrite the stored result.
    hostile = _context(llm=_llm(_unmet("C1"), _unmet("C2"), _unmet("C3")))
    assert not tx_execution_succeeded(
        _evaluate(agreement, parties["client"], receipt_id, hostile)
    )
    assert _read(agreement, "get_receipt", [receipt_id]) == stored


@pytest.mark.integration
def test_two_jobs_same_worker_same_url_keep_receipts_apart(agreement, parties):
    for title in ("Job A", "Job B"):
        assert tx_execution_succeeded(
            _tx(agreement, parties["client"], "create_job", [title, "Task", CRITERIA])
        )
    for job_id in ("job-1", "job-2"):
        assert tx_execution_succeeded(_tx(agreement, parties["worker"], "accept_job", [job_id]))
    for job_id in ("job-1", "job-2"):
        assert tx_execution_succeeded(
            _tx(agreement, parties["worker"], "submit_delivery", [job_id, URL])
        )

    rcpt_a = _read(agreement, "get_receipt_id", ["job-1", 1])
    rcpt_b = _read(agreement, "get_receipt_id", ["job-2", 1])
    assert rcpt_a != rcpt_b
    assert _read(agreement, "get_receipt", [rcpt_a])["job_id"] == "job-1"
    assert _read(agreement, "get_receipt", [rcpt_b])["job_id"] == "job-2"

    assert tx_execution_succeeded(_evaluate(agreement, parties["client"], rcpt_b))
    assert _read(agreement, "get_job", ["job-2"])["status"] == "VERIFIED"
    assert _read(agreement, "get_job", ["job-1"])["status"] == "IN_PROGRESS"
    assert _read(agreement, "get_receipt", [rcpt_a])["status"] == "PENDING"


@pytest.mark.integration
@pytest.mark.skipif(
    not os.environ.get("SMART_AGREEMENT_LIVE_URL"),
    reason="set SMART_AGREEMENT_LIVE_URL (public https page) to run against real LLM validators",
)
def test_live_validators_reach_consensus_on_a_real_page(agreement, parties):
    """Opt-in: real web fetch + real LLM validators (no mocks)."""
    live_url = os.environ["SMART_AGREEMENT_LIVE_URL"]
    criteria = json.loads(os.environ.get("SMART_AGREEMENT_LIVE_CRITERIA", "null")) or [
        "The page contains a title",
        "The page contains at least one paragraph of text",
    ]
    assert tx_execution_succeeded(
        _tx(agreement, parties["client"], "create_job", ["Live", "Live check", criteria])
    )
    assert tx_execution_succeeded(_tx(agreement, parties["worker"], "accept_job", ["job-1"]))
    assert tx_execution_succeeded(
        _tx(agreement, parties["worker"], "submit_delivery", ["job-1", live_url])
    )
    result = _tx(
        agreement, parties["client"], "evaluate_delivery", ["rcpt-1"],
        wait_interval=15000, wait_retries=60,
    )
    assert tx_execution_succeeded(result)
    receipt = _read(agreement, "get_receipt", ["rcpt-1"])
    assert receipt["status"] == "EVALUATED" and len(receipt["evidence_hash"]) == 64
    ids = [r["criterion_id"] for r in receipt["results"]]
    assert ids == [f"C{i + 1}" for i in range(len(criteria))]
