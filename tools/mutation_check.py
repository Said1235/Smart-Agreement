"""Mutation check: weaken contracts/smart_agreement.py in 15 ways and require the test suite to fail.

Run from the repository root (mutants live in a temporary directory; the real contract is never
modified):
    python tools/mutation_check.py
"""
import os
import subprocess
import sys
import tempfile

SRC = "contracts/smart_agreement.py"
# Mutants live in a temporary directory (the file stem must stay "smart_agreement"); the real
# contract is never modified.
MUTANT = os.path.join(tempfile.mkdtemp(prefix="smart_agreement_mut_"), "smart_agreement.py")
orig = open(SRC, encoding="utf-8").read()

def sub(old, new):
    assert old in orig, "pattern not found: " + old[:60]
    return orig.replace(old, new, 1)

MUTATIONS = {
 "M01 validator ignores independent evidence hash":
   sub('                if proposed["evidence_hash"] != my_hash:\n                    return False\n', ''),
 "M02 validator ignores evidence size":
   sub('                if proposed["evidence_size"] != my_size:\n                    return False\n', ''),
 # M03 is a known EQUIVALENT mutant: reason_code is 1:1 with status and is compared as well, so
 # removing only the status comparison cannot change behavior (see docs/AUDIT_REPORT.md, I6).
 "M03 validator never compares per-criterion status":
   sub('                    if theirs["status"] != mine["status"]:\n                        return False\n', ''),
 "M04 validator never verifies quotes against its own evidence":
   sub('''                    if not _excerpt_is_acceptable(
                        item["status"], item["evidence_excerpt"], my_content_norm
                    ):
                        return False''', '                    pass'),
 "M05 validator skips structural verification":
   sub('                if not _check_verdict_structure(proposed, ctx):\n                    return False\n', ''),
 "M06 validator always accepts":
   sub('            try:\n                proposed = leader_result.calldata\n', '            return True\n            try:\n                proposed = leader_result.calldata\n'),
 "M07 reason_code not checked":
   sub('        if reason != REASON_FOR_STATUS[status]:\n            return False\n', ''),
 "M08 overall_status not re-derived":
   sub('    if data["overall_status"] != _derive_overall(statuses):\n        return False\n', ''),
 "M09 criteria count not enforced":
   sub('    if not isinstance(results, list) or len(results) != len(criteria):\n        return False\n', '    if not isinstance(results, list):\n        return False\n'),
 "M10 receipt_id binding dropped":
   sub('    if data["receipt_id"] != ctx["receipt_id"]:\n        return False\n', ''),
 "M11 agreement_hash binding dropped":
   sub('    if data["agreement_hash"] != ctx["agreement_hash"]:\n        return False\n', ''),
 "M12 MET without literal quote is kept (no downgrade)":
   sub('        if status == CRIT_MET and not _quote_is_substantive(excerpt, content_norm):', '        if False:'),
 "M13 criterion order/ids not enforced":
   sub('        if cid != expected["id"]:\n            return False\n', ''),
 "M14 validator trusts leader error without re-running":
   sub('                    return _validator_agrees_on_error(leader_result.message, leader_fn)', '                    return True'),
 "M15 UNVERIFIABLE does not make the job INCONCLUSIVE":
   sub('    if any(s == CRIT_UNVERIFIABLE for s in statuses):\n        return JOB_INCONCLUSIVE\n', ''),
}

selected = sys.argv[1:]  # optional ids, e.g. `python tools/mutation_check.py M03 M07`
survivors = []
for name, src in MUTATIONS.items():
    if selected and name.split()[0] not in selected:
        continue
    open(MUTANT, "w", encoding="utf-8").write(src)
    r = subprocess.run(
        ["pytest", "tests/direct/test_smart_agreement_functional.py", "tests/direct/test_smart_agreement_equivalence.py",
         "tests/direct/test_smart_agreement_limits.py", "-q", "-x", "-p", "no:cacheprovider"], capture_output=True, text=True, env={**os.environ, "SMART_AGREEMENT_CONTRACT": MUTANT})
    killed = r.returncode != 0
    tail = [l for l in r.stdout.splitlines() if l.startswith("FAILED")][:1]
    print(("KILLED  " if killed else "SURVIVED"), name, "->", (tail[0][:110] if tail else ""))
    if not killed: survivors.append(name)
print("\nsurvivors:", survivors)
