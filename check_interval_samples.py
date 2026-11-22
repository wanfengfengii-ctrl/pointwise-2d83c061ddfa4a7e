"""Check the sample recordings against the unchanged fixed-zero workflow."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from intake.receipts import ReceiptStore
from intake.receive import receive_packet
from issue.register import initialize_issue_store, issue_packet
from recording.import_run import import_run
from recording.revision_store import RevisionStore
from recording.revisions import freeze_run
from review.publish import sign_review
from review.report_store import ReportStore
from tests.support import brute_force


def issue_v1(case):
    with closing(sqlite3.connect(":memory:")) as connection:
        store = RevisionStore(connection)
        store.initialize()
        initialize_issue_store(connection)
        draft = import_run(store, case["run_id"], case["main"], case["secondary"], window=case["window"])
        revision = freeze_run(store, draft)
        return issue_packet(store, revision, case["public"])


def verify(cases):
    packets = {name: issue_v1(case) for name, case in cases.items()}
    if packets["switch_hidden"] != packets["opposite_hidden"]:
        raise RuntimeError("the hidden recordings must have identical V1 packets and registrations")

    with closing(sqlite3.connect(":memory:")) as connection:
        receipts, reports = ReceiptStore(connection), ReportStore(connection)
        receipts.initialize()
        reports.initialize()
        for name, case in cases.items():
            receipt_id = receive_packet(receipts, *packets[name])
            for query in case["queries"]:
                anchors = tuple(tuple(pair) for pair in query["anchors"])
                expected = brute_force(case["main"], case["secondary"], case["public"], anchors, case["window"])
                report = sign_review(receipts, reports, receipt_id, anchors)
                if (report.total_cost, report.pairs) != expected:
                    raise RuntimeError(f"{name} with {anchors!r} disagrees with the fixed-zero reference")
                registration = packets[name][1]
                if report.receipt_id != receipt_id or report.body_sha256 != registration.body_sha256:
                    raise RuntimeError(f"{name} report has the wrong receipt or digest")


def main():
    path = Path(__file__).with_name("samples") / "zero_interval.json"
    cases = json.loads(path.read_text(encoding="utf-8"))
    verify(cases)
    print(f"Checked {len(cases)} recordings against the V1 reference. The two hidden cases have identical V1 packets.")


if __name__ == "__main__":
    main()
