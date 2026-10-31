"""Second mutation round (audit): state machine, receipt identity and the audit fixes.

Run from the repository root (mutants live in a temporary directory; the real contract is never
modified):
    python tools/mutation_check_audit.py
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
    assert orig.count(old) == 1, (orig.count(old), old[:70])
    return orig.replace(old, new)


M = {
 "N01 client may accept its own job": sub('        if sender == job.client:\n            _fail(ERR_EXPECTED, "client cannot accept its own job")\n', ''),
 "N02 replay: evaluated receipt can be evaluated again": sub('        if receipt.status != RCPT_PENDING:\n            _fail(ERR_EXPECTED, "receipt is not pending evaluation")\n', ''),
 "N03 evaluate ignores job state": sub('        if job.status != JOB_IN_PROGRESS:\n            _fail(ERR_EXPECTED, "job is not in progress")\n', ''),
 "N04 anyone can submit a delivery": sub('        if sender != job.worker:\n            _fail(ERR_EXPECTED, "only the assigned worker can submit a delivery")\n', ''),
 "N05 delivery limit not enforced": sub('        if int(job.delivery_count) >= MAX_DELIVERIES:\n            _fail(ERR_EXPECTED, "delivery limit reached")\n', ''),
 "N06 previous pending receipt not superseded": sub('                previous.status = RCPT_SUPERSEDED\n', '                pass\n'),
 "N07 receipt id reused across jobs (delivery-index based)": sub('        receipt_id = "rcpt-" + str(number)\n', '        receipt_id = "rcpt-" + str(int(job.delivery_count) + 1)\n'),
 "N08 any stranger may request an evaluation": sub('        if sender != job.client and sender != job.worker:\n            _fail(ERR_EXPECTED, "only the client or the worker can request an evaluation")\n', ''),
 "N09 job always ends VERIFIED": sub('        job.status = verdict["overall_status"]\n', '        job.status = JOB_VERIFIED\n'),
 "N10 receipt not bound to latest delivery": sub('''        if (
            delivery_index < 1
            or delivery_index > len(job.receipt_ids)
            or job.receipt_ids[delivery_index - 1] != receipt_id
        ):
            _fail(ERR_EXPECTED, "receipt does not match the job delivery index")
''', ''),
 "N11 F1 reverted: 1-char quotes accepted": sub('MIN_EXCERPT_LEN = 8\n', 'MIN_EXCERPT_LEN = 1\n'),
 "N12 F2 reverted: any port accepted": sub('    if port is not None and port != 443:\n', '    if False:\n'),
 "N13 F2 reverted: TLD shape not checked": sub('    if not tld_ok:\n', '    if False:\n'),
 "N14 F2 reverted: reserved TLDs allowed": sub('    if tld in RESERVED_TLDS:\n', '    if False:\n'),
 "N15 F3 reverted: receipt/job agreement not compared": sub('        if str(receipt.agreement_hash) != str(job.agreement_hash):\n', '        if False:\n'),
 "N18 receipt worker not compared with job worker": sub('        if receipt.worker != job.worker:\n            _fail(ERR_EXPECTED, "receipt worker does not match the job worker")\n', ''),
 "N16 terms hash not committed at creation": sub('        job.terms_hash = _terms_hash(clean_title, clean_description, clean_criteria)\n', '        job.terms_hash = ""\n'),
 "N17 evaluated receipt leaves worker unset on job accept": sub('        job.worker = sender\n', '        pass\n'),
 # ---- expanded limits: every constant, its boundary comparison and its public mirror ----------
 "L01 MAX_CRITERIA one too high": sub('MAX_CRITERIA = 20\n', 'MAX_CRITERIA = 21\n'),
 "L02 MAX_CRITERIA one too low": sub('MAX_CRITERIA = 20\n', 'MAX_CRITERIA = 19\n'),
 "L03 MIN_CRITERIA one too high": sub('MIN_CRITERIA = 1\n', 'MIN_CRITERIA = 2\n'),
 "L04 an agreement without criteria is accepted": sub('MIN_CRITERIA = 1\n', 'MIN_CRITERIA = 0\n'),
 "L05 MAX_TITLE_LEN one too high": sub('MAX_TITLE_LEN = 200\n', 'MAX_TITLE_LEN = 201\n'),
 "L06 MAX_DESCRIPTION_LEN one too high": sub('MAX_DESCRIPTION_LEN = 5000\n', 'MAX_DESCRIPTION_LEN = 5001\n'),
 "L07 MAX_CRITERION_LEN one too high": sub('MAX_CRITERION_LEN = 1000\n', 'MAX_CRITERION_LEN = 1001\n'),
 "L08 MAX_URL_LEN one too high": sub('MAX_URL_LEN = 2048\n', 'MAX_URL_LEN = 2049\n'),
 "L09 MAX_CONTENT_BYTES one too high": sub('MAX_CONTENT_BYTES = 250000\n', 'MAX_CONTENT_BYTES = 250001\n'),
 "L10 MAX_CONTENT_BYTES one too low": sub('MAX_CONTENT_BYTES = 250000\n', 'MAX_CONTENT_BYTES = 249999\n'),
 "L11 MAX_EXCERPT_LEN one too high": sub('MAX_EXCERPT_LEN = 300\n', 'MAX_EXCERPT_LEN = 301\n'),
 "L12 MAX_EXCERPT_LEN one too low": sub('MAX_EXCERPT_LEN = 300\n', 'MAX_EXCERPT_LEN = 299\n'),
 "L13 content limit is exclusive (>= instead of >)": sub('    if len(body) > MAX_CONTENT_BYTES:\n', '    if len(body) >= MAX_CONTENT_BYTES:\n'),
 "L14 get_limits reports a wrong maximum": sub('            "max_criteria": MAX_CRITERIA,\n', '            "max_criteria": MAX_CRITERIA - 1,\n'),
 "L15 prompt states a stale quote maximum": sub('        + str(MAX_EXCERPT_LEN)\n', '        + "200"\n'),
 "L16 duplicate criteria accepted": sub('            if key in seen:\n                _fail(ERR_EXPECTED, "criteria must be distinct")\n', ''),
 "L17 criterion ids are zero-padded": sub('    return "C" + str(position + 1)\n', '    return "C%02d" % (position + 1)\n'),
 "L18 quotes are not cut to the maximum": sub('        excerpt = _norm_ws(excerpt)[:MAX_EXCERPT_LEN].rstrip()\n', '        excerpt = _norm_ws(excerpt).rstrip()\n'),
}
selected = sys.argv[1:]  # optional ids, e.g. `python tools/mutation_check_audit.py L01 N03`
survivors = []
for name, src in M.items():
    if selected and name.split()[0] not in selected:
        continue
    open(MUTANT, "w", encoding="utf-8").write(src)
    r = subprocess.run(["pytest", "tests/direct/test_smart_agreement_functional.py", "tests/direct/test_smart_agreement_equivalence.py",
                        "tests/direct/test_smart_agreement_audit.py", "tests/direct/test_smart_agreement_limits.py", "-q", "-x", "-p", "no:cacheprovider"], capture_output=True, text=True, env={**os.environ, "SMART_AGREEMENT_CONTRACT": MUTANT})
    f = [l for l in r.stdout.splitlines() if l.startswith("FAILED")][:1]
    killed = r.returncode != 0
    print(("KILLED  " if killed else "SURVIVED"), name, "->", (f[0].split("::", 1)[1][:90] if f else ""))
    if not killed:
        survivors.append(name)
print("\nsurvivors:", survivors)
