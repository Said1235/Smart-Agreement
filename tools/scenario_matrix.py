"""Build the audit scenario matrix from a REAL verbose pytest run.

    pytest tests/direct -p no:cacheprovider -v | grep -E " (PASSED|FAILED|ERROR)" > pytest-verbose.txt
    python tools/scenario_matrix.py pytest-verbose.txt

Each audit scenario is mapped to the tests (by id fragment) that attack it; the table reports
how many of those tests ran and passed. Nothing here is hand-written: a scenario with no
matching test aborts the script.
"""
import re
import sys

res = {}
for line in open(sys.argv[1]).read().splitlines():
    m = re.match(r"^(.+?) (PASSED|FAILED|ERROR)\b", line)
    if m:
        res[m.group(1)] = m.group(2)

S = {
 1: ("Leader VERIFIED while a criterion is NOT_MET", ["verified_with_one_unmet", "claims_met_but_independent", "claims_not_met_but_validator"]),
 2: ("Leader omits a required criterion", ["fewer_criteria_than"]),
 3: ("Leader duplicates a criterion", ["duplicated_criterion"]),
 4: ("Leader introduces an unknown criterion", ["extra_criterion", "renamed_criterion_id"]),
 5: ("Leader changes a criterion ID / order", ["swapped_criterion_ids", "reordered_criteria", "criterion_id_wrong_type"]),
 6: ("Reason code contradicts status", ["reason_contradicts_status", "unknown_reason_code"]),
 7: ("Summary contradicts findings (no free-text summary exists)", ["overall_needs_revision_but_all_met", "overall_inconclusive_but_all_met", "overall_unknown_value", "extra_top_level_key", "no_free_text_or_score"]),
 8: ("Evidence excerpt not in the source / trivial", ["fabricated_quote", "non_canonical_whitespace", "overlong_quote", "met_without_any_quote", "probe_trivial_quotes", "short_quote"]),
 9: ("Evidence hash does not match the source bytes", ["wrong_evidence_hash", "hash_of_another_valid", "source_changed", "honest_hash_but_size_lie", "wrong_evidence_size"]),
 10: ("Same global status, conflicting material finding", ["same_overall_status_but_one_individual"]),
 11: ("Materially different result, same category / near score", ["statuses_must_match_exactly", "differential_fuzz"]),
 12: ("Leader returns malformed / unexpected type", ["malformed_leader_result", "status_wrong_type", "results_is_a_dict", "missing_results"]),
 13: ("Validator meets malformed evidence / model output", ["validator_with_invalid_utf8", "validator_model_failure", "validator_exception_never", "validator_web_failure", "validator_without_web"]),
 14: ("Agreement / receipt ID differs from requested record", ["different_job_id", "different_receipt_id", "different_delivery_index", "different_agreement_hash", "cannot_be_replayed_on_another", "corrupted_state"]),
 15: ("Several deliveries from one wallet -> distinct receipts", ["two_deliveries_by_one_worker", "unevaluated_delivery_is_superseded"]),
 16: ("Overlapping / interleaved submissions", ["stateful_fuzz", "two_jobs_never_share", "receipt_counter_is_not_advanced"]),
 17: ("Two agreements cannot share a receipt", ["two_jobs_never_share", "two_jobs_same"]),
 18: ("Second evaluation cannot overwrite earlier receipt", ["second_evaluation_does_not_overwrite", "evaluation_cannot_be_repeated", "corrective_delivery_after_needs_revision"]),
 19: ("Failed attempt leaves no partial success record", ["http_errors_fail_safely", "bad_content_fails_safely", "malformed_model_output", "failed_evaluation_can_be_retried", "stateful_fuzz", "unreachable_source"]),
 20: ("Receipt queries return the exact requested id", ["receipt_id_lookup_boundaries", "get_job_receipts_lists", "absent_records", "calldata_roundtrip", "submit_delivery_creates_unique"]),
 21: ("Unauthorized submit / evaluate", ["only_the_assigned_worker", "only_client_or_worker", "cannot_accept_own_job"]),
 22: ("Invalid state transition", ["cannot_deliver_before_acceptance", "can_only_be_accepted_once", "verified_job_is_final", "accept_unknown_job"]),
 23: ("Repeated evaluation of an evaluated receipt", ["evaluation_cannot_be_repeated", "evaluate_unknown_receipt"]),
 24: ("Maximum delivery count reached", ["delivery_limit_is_two"]),
 25: ("Empty / inaccessible / oversized evidence", ["bad_content_fails_safely", "http_errors_fail_safely", "unreachable_source"]),
 26: ("Prompt injection in source content", ["prompt_treats_deliverable", "hostile_deliverable_cannot", "trivial_quotes_cannot", "prompt_embeds_criteria"]),
 27: ("Temporary HTTP error", ["http_errors_fail_safely", "validator_transient_failure", "leader_error_handling", "failed_evaluation_can_be_retried"]),
 28: ("Conflicting / changing source content", ["source_changed_between", "source_changed_in_a_single_byte", "hash_of_another_valid"]),
 29: ("Inconclusive evaluation", ["unverifiable_criterion_is_inconclusive", "mixed_unmet_and_unverifiable", "inconclusive_job_accepts"]),
 30: ("Execution error is never a business outcome", ["fail_safely", "llm_errors_always_force", "vm_level_leader_error", "failed_evaluation_can_be_retried", "leader_error_handling"]),
}
print("| # | Scenario | Tests | Passed | Failed | Result |\n|---|---|---|---|---|---|")
bad = 0
for k, (title, keys) in S.items():
    ids = [i for i in res if any(x in i for x in keys)]
    assert ids, f"scenario {k} has no matching test"
    passed = sum(res[i] == "PASSED" for i in ids)
    failed = len(ids) - passed
    bad += failed
    print(f"| {k} | {title} | {len(ids)} | {passed} | {failed} | {'PASS' if failed == 0 else 'FAIL'} |")
print(f"\nTotal tests in run: {len(res)}; passed: {sum(v == 'PASSED' for v in res.values())}; not passed: {sum(v != 'PASSED' for v in res.values())}")
sys.exit(1 if bad else 0)
