"""Smart Agreement - adversarial audit tests (direct mode).

Three kinds of evidence, none of which re-uses the contract's own helper functions as
its oracle:

  1. DIFFERENTIAL FUZZ: an independent re-implementation of the acceptance spec (the
     ``oracle``) is compared against the real validator on thousands of randomly forged
     leader results. Any disagreement, in either direction, is a defect.
  2. STATEFUL FUZZ: random sequences of operations (valid and invalid, from several
     senders, against several evidence "worlds") with global invariants re-checked after
     EVERY step - including steps whose call reverted, because direct mode does not roll
     state back, so any partial write made before a revert would show up as a violated
     invariant.
  3. TARGETED PROBES for specific weaknesses found while reading the implementation.
"""

import copy
import json
import random
import re

import pytest

from tests.direct.smart_agreement_support import (
    BODY,
    BODY_SHA256,
    CONTRACT,
    CRITERIA,
    LIMITS,
    QUOTES,
    URL,
    URL_2,
    all_met,
    canonical,
    capture_leader,
    hex_of,
    in_progress,
    item,
    mock_world,
    new_job,
    sha256_hex,
    validator_accepts,
)

CONTENT = " ".join(BODY.split())
KEYS = {
    "job_id", "receipt_id", "delivery_index", "agreement_hash",
    "evidence_hash", "evidence_size", "results", "overall_status",
}
ITEM_KEYS = {"criterion_id", "status", "reason_code", "evidence_excerpt"}
REASON = {"MET": "MEETS_CRITERION", "NOT_MET": "REQUIREMENT_NOT_MET", "UNVERIFIABLE": "INSUFFICIENT_EVIDENCE"}
MIN_QUOTE = 8  # spec: a non-empty quote must be at least this long, or be the whole content


def derive(statuses):
    if "UNVERIFIABLE" in statuses:
        return "INCONCLUSIVE"
    if "NOT_MET" in statuses:
        return "NEEDS_REVISION"
    return "VERIFIED"


def quote_ok(status, quote, content):
    if status == "UNVERIFIABLE":
        return quote == ""
    if quote == "":
        return status == "NOT_MET"
    return quote in content and (len(quote) >= MIN_QUOTE or quote == content)


def is_int(v):
    return type(v) is int


def oracle(x, honest, content=CONTENT):
    """Independent statement of exactly when a leader result must be accepted, given that the
    validator's own evaluation equals ``honest`` (same statuses, same evidence)."""
    if type(x) is not dict or set(x) != KEYS:
        return False
    for k in ("job_id", "receipt_id", "agreement_hash", "evidence_hash"):
        if not isinstance(x[k], str) or x[k] != honest[k]:
            return False
    for k in ("delivery_index", "evidence_size"):
        if not is_int(x[k]) or x[k] != honest[k]:
            return False
    res = x["results"]
    if not isinstance(res, list) or len(res) != len(honest["results"]):
        return False
    statuses = []
    for i, (r, h) in enumerate(zip(res, honest["results"])):
        if type(r) is not dict or set(r) != ITEM_KEYS:
            return False
        if not all(isinstance(r[k], str) for k in ITEM_KEYS):
            return False
        if r["criterion_id"] != f"C{i + 1}" or r["status"] != h["status"]:
            return False
        if r["reason_code"] != REASON[r["status"]]:
            return False
        q = r["evidence_excerpt"]
        if len(q) > LIMITS["max_excerpt_len"] or q != " ".join(q.split()) or not quote_ok(r["status"], q, content):
            return False
        statuses.append(r["status"])
    return isinstance(x["overall_status"], str) and x["overall_status"] == derive(statuses)


QUOTE_POOL = [
    "", "e", "a", "MIT", "License", "GET /users", "License: MIT", "exposes a GET /users endpoint",
    "Test coverage reached 95%", "  License:   MIT  ", "License: MIT\n", "License: GPL", "x" * (LIMITS["max_excerpt_len"] + 1),
    CONTENT, CONTENT[:200], "MIT license", "sample deliverable", "The service exposes",
]
VALUE_POOL = [
    None, "", " ", 0, 1, 2, -1, 2**40, True, False, 1.0, "MET", "NOT_MET", "UNVERIFIABLE", "VERIFIED",
    "NEEDS_REVISION", "INCONCLUSIVE", "C1", "C2", "C3", "C4", "job-1", "job-2", "rcpt-1", "rcpt-2",
    "0" * 64, BODY_SHA256, BODY_SHA256.upper(), sha256_hex(b"other"), "MEETS_CRITERION",
    "REQUIREMENT_NOT_MET", "INSUFFICIENT_EVIDENCE", "x", [], {}, [1], {"a": 1},
]


def all_paths(node, path=()):
    out = []
    if isinstance(node, dict):
        for k, v in node.items():
            out.append(path + (k,))
            out += all_paths(v, path + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.append(path + (i,))
            out += all_paths(v, path + (i,))
    return out


def get_at(node, path):
    for p in path:
        node = node[p]
    return node


def mutate(honest, rng):
    x = copy.deepcopy(honest)
    for _ in range(rng.choice([1, 1, 1, 2, 3])):
        op = rng.choice(["set", "set", "delete", "addkey", "swap", "dup", "drop", "reverse", "quote", "quote", "status"])
        results = x.get("results") if isinstance(x, dict) else None
        paths = all_paths(x) if isinstance(x, (dict, list)) else []
        try:
            if op == "set" and paths:
                path = rng.choice(paths)
                get_at(x, path[:-1])[path[-1]] = copy.deepcopy(rng.choice(VALUE_POOL))
            elif op == "delete" and paths:
                path = rng.choice(paths)
                del get_at(x, path[:-1])[path[-1]]
            elif op == "addkey":
                target = rng.choice([x] + [r for r in (results or []) if isinstance(r, dict)])
                target[rng.choice(["note", "score", "summary", "confidence"])] = rng.choice(VALUE_POOL)
            elif op == "swap" and isinstance(results, list) and len(results) > 1:
                i, j = rng.sample(range(len(results)), 2)
                results[i], results[j] = results[j], results[i]
            elif op == "dup" and isinstance(results, list) and results:
                results.append(copy.deepcopy(rng.choice(results)))
            elif op == "drop" and isinstance(results, list) and results:
                results.pop(rng.randrange(len(results)))
            elif op == "reverse" and isinstance(results, list):
                results.reverse()
            elif op in ("quote", "status") and isinstance(results, list) and results:
                r = rng.choice(results)
                if isinstance(r, dict):
                    if op == "status":
                        r["status"] = rng.choice(["MET", "NOT_MET", "UNVERIFIABLE"])
                        r["reason_code"] = REASON[r["status"]]
                    r["evidence_excerpt"] = rng.choice(QUOTE_POOL)
                    if rng.random() < 0.7:  # keep the overall status consistent: only the quote rule decides
                        x["overall_status"] = derive([q.get("status") for q in results if isinstance(q, dict)])
        except (KeyError, IndexError, TypeError):
            pass
    return x


BASES = {
    "all_met": all_met(),
    "mixed": [item("C1", "MET", QUOTES["C1"]), item("C2", "NOT_MET", ""), item("C3", "MET", QUOTES["C3"])],
    "inconclusive": [item("C1", "UNVERIFIABLE"), item("C2", "NOT_MET", QUOTES["C2"]), item("C3", "MET", QUOTES["C3"])],
}


@pytest.mark.parametrize("base", sorted(BASES))
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_differential_fuzz_validator_matches_independent_oracle(
    direct_vm, direct_deploy, direct_alice, direct_bob, base, seed
):
    contract, job_id, receipt_id = in_progress(direct_vm, direct_deploy, direct_alice, direct_bob)
    mock_world(direct_vm, llm=BASES[base])
    honest = capture_leader(direct_vm, contract, direct_alice, receipt_id)
    assert oracle(honest, honest) and validator_accepts(direct_vm, honest)

    rng = random.Random(seed * 7919 + len(base))
    accepted_by_oracle = 0
    disagreements = []
    N = 700
    for n in range(N):
        forged = mutate(honest, rng)
        expected = oracle(forged, honest)
        actual = validator_accepts(direct_vm, forged)
        accepted_by_oracle += expected
        if expected != actual:
            disagreements.append((expected, actual, json.dumps(forged, default=repr)[:400]))
    assert not disagreements, (
        f"{len(disagreements)} validator/oracle disagreements; first: {disagreements[:3]}"
    )
    # Not vacuous: the fuzz must produce both accepted near-misses and many rejections.
    assert 0 < accepted_by_oracle < N


# --------------------------------------------------------------------------- #
# Stateful fuzz with global invariants
# --------------------------------------------------------------------------- #

WORLDS = {
    "ok": dict(llm=all_met()),
    "mixed": dict(llm=[item("C1", "MET", QUOTES["C1"]), item("C2", "NOT_MET"), item("C3", "MET", QUOTES["C3"])]),
    "unverifiable": dict(llm=[item("C1", "UNVERIFIABLE"), item("C2", "UNVERIFIABLE"), item("C3", "UNVERIFIABLE")]),
    "hallucinated": dict(llm=[item(c, "MET", "completely invented evidence") for c in ("C1", "C2", "C3")]),
    "other_body": dict(body="Entirely different text.\nLicense: MIT\n", llm=[item("C1", "NOT_MET"), item("C2", "NOT_MET"), item("C3", "MET", "License: MIT")]),
    "http404": dict(status=404),
    "http503": dict(status=503),
}
URL_CHOICES = [URL, URL_2, URL, URL_2, "http://example.com/x", "https://localhost/x", "", "not a url"]


def addr_hex(address):
    return address[2:].lower()


def check_invariants(contract, ledger):
    """Global properties that must hold after ANY sequence of calls (even reverted ones)."""
    jobs, receipts = [], []
    n = 1
    while contract.get_job(f"job-{n}")["found"]:
        jobs.append(contract.get_job(f"job-{n}"))
        n += 1
    assert not contract.get_job(f"job-{n + 1}")["found"], "gap in job ids"
    n = 1
    while contract.get_receipt(f"rcpt-{n}")["found"]:
        receipts.append(contract.get_receipt(f"rcpt-{n}"))
        n += 1
    by_id = {r["receipt_id"]: r for r in receipts}
    assert len(by_id) == len(receipts)

    for job in jobs:
        jid = job["id"]
        # I1: terms commitment is recomputable from the stored terms and never changes
        terms = {"v": 1, "title": job["title"], "description": job["description"],
                 "criteria": [{"id": c["id"], "text": c["text"]} for c in job["criteria"]]}
        assert job["terms_hash"] == sha256_hex(canonical(terms)), f"{jid}: terms_hash mismatch"
        assert LIMITS["min_criteria"] <= len(job["criteria"]) <= LIMITS["max_criteria"]
        assert [c["id"] for c in job["criteria"]] == [f"C{i + 1}" for i in range(len(job["criteria"]))]
        before = ledger.setdefault(("terms", jid), (job["terms_hash"], job["client"], job["title"], job["description"], tuple(c["text"] for c in job["criteria"])))
        assert before == (job["terms_hash"], job["client"], job["title"], job["description"], tuple(c["text"] for c in job["criteria"])), f"{jid}: terms mutated"

        rids = job["receipt_ids"]
        assert job["delivery_count"] == len(rids) <= 2
        if job["status"] == "OPEN":
            assert job["worker"] == "" and job["agreement_hash"] == "" and rids == []
            continue
        # I2: accepted job binds terms, id and both parties; worker is never the client
        assert job["worker"] and job["worker"] != job["client"]
        agreement = {"v": 1, "job_id": jid, "client": addr_hex(job["client"]),
                     "worker": addr_hex(job["worker"]), "terms_hash": job["terms_hash"]}
        assert job["agreement_hash"] == sha256_hex(canonical(agreement)), f"{jid}: agreement_hash mismatch"
        before = ledger.setdefault(("worker", jid), job["worker"])
        assert before == job["worker"], f"{jid}: worker changed"

        for idx, rid in enumerate(rids, start=1):
            r = by_id[rid]
            assert r["job_id"] == jid and r["delivery_index"] == idx, f"{rid}: wrong binding"
            assert r["worker"] == job["worker"] and r["agreement_hash"] == job["agreement_hash"]
            assert contract.get_receipt_id(jid, idx) == rid
            is_last = idx == len(rids)
            if r["status"] == "PENDING":
                assert is_last, f"{rid}: pending receipt that is not the latest"
                assert r["evidence_hash"] == "" and r["results"] == [] and r["overall_status"] == ""
            elif r["status"] == "SUPERSEDED":
                assert not is_last, f"{rid}: latest receipt superseded"
                assert r["results"] == [] and r["overall_status"] == ""
            else:
                assert r["status"] == "EVALUATED"
                assert re.fullmatch(r"[0-9a-f]{64}", r["evidence_hash"]) and r["evidence_size"] > 0
                assert [x["criterion_id"] for x in r["results"]] == [c["id"] for c in job["criteria"]]
                for x in r["results"]:
                    assert x["status"] in REASON and x["reason_code"] == REASON[x["status"]]
                    assert len(x["evidence_excerpt"]) <= LIMITS["max_excerpt_len"]
                    assert x["evidence_excerpt"] == " ".join(x["evidence_excerpt"].split())
                    assert (x["evidence_excerpt"] == "") == (x["status"] == "UNVERIFIABLE") or x["status"] == "NOT_MET"
                assert r["overall_status"] == derive([x["status"] for x in r["results"]])
                assert r["evaluation_requested_by"] in (job["client"], job["worker"])
            # I3: an evaluated receipt is immutable (append-only history)
            if r["status"] == "EVALUATED":
                frozen = ledger.setdefault(("rcpt", rid), copy.deepcopy(r))
                assert frozen == r, f"{rid}: evaluated receipt mutated"
            else:
                prev = ledger.get(("rcpt", rid))
                assert prev is None, f"{rid}: left EVALUATED state"
        # I4: job status always follows its latest receipt
        if rids:
            last = by_id[rids[-1]]
            if last["status"] == "PENDING":
                assert job["status"] == "IN_PROGRESS"
            else:
                assert last["status"] == "EVALUATED" and job["status"] == last["overall_status"]
        else:
            assert job["status"] == "IN_PROGRESS"
        if job["status"] == "VERIFIED":
            assert all(x["status"] == "MET" for x in by_id[rids[-1]]["results"])
    for r in receipts:  # no orphan receipts
        assert any(r["receipt_id"] in j["receipt_ids"] for j in jobs)
    return jobs, receipts


@pytest.mark.parametrize("seed", range(12))
def test_stateful_fuzz_invariants_hold_after_every_step(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie, seed
):
    rng = random.Random(1000 + seed)
    contract = direct_deploy(CONTRACT)
    actors = [direct_alice, direct_bob, direct_charlie]
    ledger = {}
    stats = {"ok": 0, "reverted": 0}

    def pick(prefix, count_view):
        existing = []
        n = 1
        while count_view(f"{prefix}-{n}")["found"]:
            existing.append(f"{prefix}-{n}")
            n += 1
        if existing and rng.random() < 0.85:
            return rng.choice(existing)
        return f"{prefix}-{rng.randint(1, 6)}"

    for step in range(110):
        direct_vm.sender = rng.choice(actors)
        op = rng.choice(["create", "create", "accept", "accept", "submit", "submit", "evaluate", "evaluate", "evaluate", "read"])
        try:
            if op == "create":
                n = rng.choice([2, 2, 3, 5, 1, 0, LIMITS["max_criteria"], LIMITS["max_criteria"] + 1])
                contract.create_job(rng.choice(["Task", "Task", "", "T" * (LIMITS["max_title_len"] + 1)]), "Description",
                                    [f"criterion number {i}" for i in range(n)] if rng.random() < 0.9 else ["same", "same"])
            elif op == "accept":
                contract.accept_job(pick("job", contract.get_job))
            elif op == "submit":
                contract.submit_delivery(pick("job", contract.get_job), rng.choice(URL_CHOICES))
            elif op == "evaluate":
                mock_world(direct_vm, **WORLDS[rng.choice(sorted(WORLDS))])
                contract.evaluate_delivery(pick("rcpt", contract.get_receipt))
            else:
                contract.get_job(pick("job", contract.get_job))
            stats["ok"] += 1
        except Exception:
            stats["reverted"] += 1
        check_invariants(contract, ledger)
    assert stats["ok"] >= 15 and stats["reverted"] >= 15, stats  # both paths were really exercised


# --------------------------------------------------------------------------- #
# Targeted probes
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("quote", ["e", "a", ".", "MIT", "the"])
def test_probe_trivial_quotes_cannot_support_an_approval(
    direct_vm, direct_deploy, direct_alice, direct_bob, quote
):
    """A model (or an injection) that approves with a 1-3 character 'quote' must not get VERIFIED."""
    contract, job_id, receipt_id = in_progress(direct_vm, direct_deploy, direct_alice, direct_bob)
    assert quote in CONTENT  # literal, so the old rule 'quote is a substring' is satisfied
    mock_world(direct_vm, llm=[item(c, "MET", quote) for c in ("C1", "C2", "C3")])
    direct_vm.sender = direct_alice
    contract.evaluate_delivery(receipt_id)
    assert contract.get_receipt(receipt_id)["overall_status"] != "VERIFIED"


def test_probe_validator_rejects_a_trivially_short_quote(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract, job_id, receipt_id = in_progress(direct_vm, direct_deploy, direct_alice, direct_bob)
    mock_world(direct_vm)
    proposal = capture_leader(direct_vm, contract, direct_alice, receipt_id)
    forged = copy.deepcopy(proposal)
    forged["results"][0]["evidence_excerpt"] = "e"
    assert validator_accepts(direct_vm, forged) is False


def test_probe_short_whole_document_may_be_quoted_in_full(direct_vm, direct_deploy, direct_alice, direct_bob):
    """The minimum-length rule must not make tiny but genuine deliverables unverifiable."""
    contract, job_id, receipt_id = in_progress(direct_vm, direct_deploy, direct_alice, direct_bob)
    mock_world(direct_vm, body="MIT", llm=[item(c, "MET", "MIT") for c in ("C1", "C2", "C3")])
    direct_vm.sender = direct_alice
    contract.evaluate_delivery(receipt_id)
    assert contract.get_receipt(receipt_id)["overall_status"] == "VERIFIED"


@pytest.mark.parametrize(
    "url",
    [
        "https://localhost./x",          # trailing dot defeats the 'localhost' string compare
        "https://foo.localhost/x",       # *.localhost resolves to loopback
        "https://0x7f.0.0.1/x",          # hex IPv4 octet
        "https://0177.0.0.1/x",          # octal IPv4
        "https://127.1/x",               # shorthand IPv4
        "https://2130706433/x",          # decimal IPv4
        "https://metadata.google.internal/x",
        "https://router.lan/x",
        "https://example.com./x",        # trailing-dot FQDN
        "https://example.com:6379/x",    # non-HTTPS port (internal service probing)
        "https://example.com:0/x",
        "https://exa mple.com/x",
        "https://-bad.example.com/x",
        "https://bad_host.example.com/x",
        "https://ex\u00e4mple.com/x",    # non-ASCII (must be punycode)
        "https://example.c0m/x",         # TLD with digits
        "https://example.123/x",
        "https://a.b.c.d.e.f.0/x",
    ],
)
def test_probe_non_public_or_ambiguous_hosts_are_rejected(
    direct_vm, direct_deploy, direct_alice, direct_bob, url
):
    contract, job_id = new_job(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_bob
    contract.accept_job(job_id)
    with direct_vm.expect_revert("[EXPECTED]"):
        contract.submit_delivery(job_id, url)
    assert contract.get_job(job_id)["delivery_count"] == 0


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/a.md",
        "https://example.com/path/a%20b.md?x=1&y=2",
        "https://sub.domain.example.co.uk/a",
        "https://xn--mnchen-3ya.de/a",
        "https://example.com:443/a",
        "https://EXAMPLE.com/Path",
        "https://raw.githubusercontent.com/org/repo/main/README.md",
    ],
)
def test_probe_legitimate_public_urls_still_accepted(direct_vm, direct_deploy, direct_alice, direct_bob, url):
    contract, job_id = new_job(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_bob
    contract.accept_job(job_id)
    assert contract.submit_delivery(job_id, url) == "rcpt-1"


def test_probe_evaluation_refuses_inconsistent_stored_bindings(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """Defense in depth: if a receipt and its job ever disagree on the agreement hash,
    nothing may be evaluated or stored (the stored data are the only authority)."""
    contract, job_id, receipt_id = in_progress(direct_vm, direct_deploy, direct_alice, direct_bob)
    mock_world(direct_vm)
    contract._instance.receipts[receipt_id].agreement_hash = "0" * 64  # simulate corrupted state
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("[EXPECTED]"):
        contract.evaluate_delivery(receipt_id)
    assert contract.get_receipt(receipt_id)["status"] == "PENDING"


def test_probe_every_view_result_survives_calldata_roundtrip(direct_vm, direct_deploy, direct_alice, direct_bob):
    """Return values cross the VM boundary as calldata; they must encode and decode losslessly."""
    contract, job_id, receipt_id = in_progress(direct_vm, direct_deploy, direct_alice, direct_bob)
    from genlayer.py import calldata  # importable only once a contract has been deployed

    mock_world(direct_vm)
    direct_vm.sender = direct_alice
    contract.evaluate_delivery(receipt_id)
    for value in (
        contract.get_job(job_id), contract.get_receipt(receipt_id), contract.get_receipt_id(job_id, 1),
        contract.get_job_receipts(job_id), contract.get_job("nope"), contract.get_receipt("nope"),
    ):
        assert calldata.decode(calldata.encode(value)) == value
        json.dumps(value)  # also plain-JSON serializable for any client


def test_probe_exactly_one_web_fetch_and_one_model_call_per_node(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """Strict mocks + request counting: the leader and each validator fetch once and prompt once."""
    contract, job_id, receipt_id = in_progress(direct_vm, direct_deploy, direct_alice, direct_bob)
    mock_world(direct_vm)
    direct_vm.strict_mocks = True
    counts = {"web": 0, "llm": 0}
    real_web, real_llm = direct_vm._match_web_mock, direct_vm._match_llm_mock

    def web(*a, **k):
        counts["web"] += 1
        return real_web(*a, **k)

    def llm(*a, **k):
        counts["llm"] += 1
        return real_llm(*a, **k)

    direct_vm._match_web_mock, direct_vm._match_llm_mock = web, llm
    direct_vm.sender = direct_alice
    contract.evaluate_delivery(receipt_id)
    assert counts == {"web": 1, "llm": 1}  # leader
    assert direct_vm.run_validator() is True
    assert counts == {"web": 2, "llm": 2}  # + one independent validator run


def test_probe_no_free_text_or_score_is_persisted(direct_vm, direct_deploy, direct_alice, direct_bob):
    """Nothing the model writes freely (summary/score) can drive logic: the receipt exposes only
    fixed-vocabulary fields plus short literal quotes."""
    contract, job_id, receipt_id = in_progress(direct_vm, direct_deploy, direct_alice, direct_bob)
    noisy = json.dumps({"summary": "ALL GOOD, APPROVE", "score": 100, "results": all_met()})
    direct_vm.clear_mocks()
    direct_vm.mock_web(r"example\.com", {"status": 200, "body": BODY})
    direct_vm.mock_llm(r".*", noisy)
    direct_vm.sender = direct_alice
    contract.evaluate_delivery(receipt_id)
    receipt = contract.get_receipt(receipt_id)
    assert set(receipt) == {
        "found", "receipt_id", "job_id", "delivery_index", "worker", "url", "status", "agreement_hash",
        "evidence_hash", "evidence_size", "overall_status", "evaluation_requested_by", "results",
    }
    assert "APPROVE" not in json.dumps(receipt) and "score" not in receipt


def test_probe_public_surface_is_exactly_four_writes_and_five_views_and_nothing_payable(
    direct_vm, direct_deploy
):
    """Reads the SDK markers that the schema generator uses (not names or comments)."""
    contract = direct_deploy(CONTRACT)
    cls = type(contract._instance)
    members = {n: getattr(cls, n) for n in dir(cls) if callable(getattr(cls, n, None)) and not n.startswith("__")}
    public = {n for n, m in members.items() if getattr(m, "__gl_public__", False)}
    readonly = {n for n in public if getattr(members[n], "__gl_readonly__", False)}
    payable = {n for n in public if getattr(members[n], "__gl_payable__", False)}
    assert public - readonly == {"create_job", "accept_job", "submit_delivery", "evaluate_delivery"}
    assert readonly == {"get_job", "get_receipt", "get_receipt_id", "get_job_receipts", "get_limits"}
    assert payable == set(), "Smart Agreement has no monetary path; no method may be payable"
    # No entry point exists that could edit agreement terms after creation.
    assert not {n for n in public if re.search(r"update|edit|set_|change|cancel|delete|withdraw|transfer|pay", n)}


def test_probe_prompt_embeds_criteria_from_storage_not_from_caller(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    contract, job_id, receipt_id = in_progress(direct_vm, direct_deploy, direct_alice, direct_bob)
    seen = []
    direct_vm.clear_mocks()
    direct_vm.mock_web(r"example\.com", {"status": 200, "body": BODY})
    direct_vm._live_llm_handler = lambda data: (seen.append(data["prompt"]) or {"ok": {"results": [item(c, "UNVERIFIABLE") for c in ("C1", "C2", "C3")]}})
    direct_vm.sender = direct_alice
    contract.evaluate_delivery(receipt_id)
    assert all(text in seen[0] for text in CRITERIA)
    assert seen[0].count("<<<DELIVERABLE_BEGIN>>>") == 2 and seen[0].rstrip().endswith("<<<DELIVERABLE_END>>>")


# --------------------------------------------------------------------------- #
# Defense in depth: guards that are redundant for API-reachable states (the stateful fuzz
# shows PENDING => latest delivery => job IN_PROGRESS) but must still refuse corrupted state.
# --------------------------------------------------------------------------- #


def _corrupt_job_status(contract, job_id, receipt_id, types):
    contract._instance.jobs[job_id].status = "NEEDS_REVISION"


def _corrupt_delivery_index(contract, job_id, receipt_id, types):
    contract._instance.receipts[receipt_id].delivery_index = types.u32(2)


def _corrupt_receipt_worker(contract, job_id, receipt_id, types):
    contract._instance.receipts[receipt_id].worker = types.Address(b"\x07" * 20)


def _corrupt_job_agreement_hash(contract, job_id, receipt_id, types):
    contract._instance.jobs[job_id].agreement_hash = "f" * 64


@pytest.mark.parametrize(
    "corrupt",
    [_corrupt_job_status, _corrupt_delivery_index, _corrupt_receipt_worker, _corrupt_job_agreement_hash],
    ids=lambda f: f.__name__.replace("_corrupt_", ""),
)
def test_probe_evaluation_refuses_corrupted_state(direct_vm, direct_deploy, direct_alice, direct_bob, corrupt):
    contract, job_id, receipt_id = in_progress(direct_vm, direct_deploy, direct_alice, direct_bob)
    from genlayer.py import types  # importable once a contract has been deployed

    mock_world(direct_vm)
    corrupt(contract, job_id, receipt_id, types)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("[EXPECTED]"):
        contract.evaluate_delivery(receipt_id)
    assert contract._instance.receipts[receipt_id].status == "PENDING"
