from collections.abc import Iterable
from fractions import Fraction

from intake.receipts import ReceiptStore
from interval.solver import solve_interval
from issue.envelope import V2
from review.interval_anchors import normalize_interval_anchors
from review.interval_endpoint import validate_endpoint
from review.report_store import ReportStore, IntervalSignedReport


def sign_interval_review(
    receipts: ReceiptStore,
    reports: ReportStore,
    receipt_id: int,
    lower,
    upper,
    anchors: Iterable[tuple[str, str]] = (),
) -> IntervalSignedReport:
    receipt = receipts.get_receipt(receipt_id)
    if receipt is None:
        raise LookupError(f'unknown receipt {receipt_id}')
    if receipt.version != V2:
        raise ValueError('interval reviews require a version 2 receipt')
    low = validate_endpoint(lower, 'interval lower bound')
    high = validate_endpoint(upper, 'interval upper bound')
    if low > high:
        raise ValueError('the interval lower bound must not exceed its upper bound')

    evidence = receipt.evidence
    fixed_anchors = normalize_interval_anchors(evidence, anchors)
    fixed = {anchor.main_index: anchor.secondary_index for anchor in fixed_anchors}
    result = solve_interval(evidence, low, high, fixed)
    position_to_public = {
        position: sample_id
        for sample_id, position in zip(evidence.public_main_ids, evidence.public_positions)
    }
    recorded_anchors = tuple(
        (position_to_public[anchor.main_index], evidence.secondary_ids[anchor.secondary_index])
        for anchor in fixed_anchors
    )
    origin = receipt.registration
    return reports.commit_interval_report(
        receipt_id=receipt_id,
        issue_id=origin.issue_id,
        run_id=origin.run_id,
        revision_id=origin.revision_id,
        body_sha256=origin.body_sha256,
        anchors=recorded_anchors,
        interval=(low, high),
        total_cost=result.total_cost,
        correction=result.correction,
        pairs=result.pairs,
    )
