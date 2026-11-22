"""Exercise the V2 zero-interval handoff against the frozen sample archive.

Each recording is issued from an isolated source store, received after the
source connection closes, signed on the night store, and reclaimed by report
number after reopening.  Rejections (V1 receipts, reversed intervals, illegal
anchors and cross-version envelopes) must leave no report behind.
"""
import json
import sqlite3
from contextlib import closing
from fractions import Fraction
from pathlib import Path

from intake.receive import receive_packet
from intake.receipts import ReceiptStore
from issue.envelope import V1, V2, read_envelope, seal_envelope
from issue.register import initialize_issue_store, issue_packet
from recording.import_run import import_run
from recording.revision_store import RevisionStore
from recording.revisions import freeze_run
from review.interval_publish import sign_interval_review
from review.publish import sign_review
from review.report_store import IntervalSignedReport, ReportStore
from review.retrieve import get_report
from tests.support import reject_commit


SAMPLES = Path(__file__).with_name('samples')


def issue_interval_case(case):
    with closing(sqlite3.connect(':memory:')) as source:
        store = RevisionStore(source)
        store.initialize()
        initialize_issue_store(source)
        draft = import_run(store, case['run_id'], case['main'], case['secondary'], window=case['window'])
        revision = freeze_run(store, draft)
        packet, registration = issue_packet(store, revision, case['public'], version=V2)
        envelope = read_envelope(packet)
        assert envelope.version == V2
        assert b'"reading"' not in envelope.body and b'"sequence"' not in envelope.body
    return packet, registration


def verify_samples(cases):
    for name, case in cases.items():
        packet, registration = issue_interval_case(case)
        with closing(sqlite3.connect(':memory:')) as night:
            receipts, reports = ReceiptStore(night), ReportStore(night)
            receipts.initialize()
            reports.initialize()
            receipt_id = receive_packet(receipts, packet, registration)
            for query in case['queries']:
                lower = Fraction(query['interval'][0])
                upper = Fraction(query['interval'][1])
                anchors = tuple(tuple(pair) for pair in query['anchors'])
                report = sign_interval_review(receipts, reports, receipt_id, lower, upper, anchors)
                expected_cost = None if query['total_cost'] is None else Fraction(query['total_cost'])
                expected_t = None if query['correction'] is None else Fraction(query['correction'])
                expected_pairs = tuple(tuple(pair) for pair in query['pairs'])
                assert isinstance(report, IntervalSignedReport)
                assert report.interval == (lower, upper)
                assert report.anchors == anchors
                assert (report.total_cost, report.correction, report.pairs) == (
                    expected_cost, expected_t, expected_pairs,
                )
                reloaded = get_report(reports, report.report_id)
                assert reloaded == report
                assert reloaded.receipt_id == receipt_id
                assert reloaded.body_sha256 == registration.body_sha256
    print(f'V2 samples: signed and reclaimed {sum(len(c["queries"]) for c in cases.values())} interval reports')


def verify_windowed():
    data = json.loads((SAMPLES / 'windowed_run.json').read_text(encoding='utf-8'))
    reduced = [row for row in data['secondary'] if row[0] != data['remove_secondary']]
    with closing(sqlite3.connect(':memory:')) as source:
        store = RevisionStore(source)
        store.initialize()
        initialize_issue_store(source)
        original = freeze_run(store, import_run(store, data['run_id'], data['main'], data['secondary']))
        packet, registration = issue_packet(store, original, data['public'], version=V2)
        correction = freeze_run(store, import_run(
            store, data['run_id'], data['main'], reduced, corrects=original))
        reduced_packet, reduced_registration = issue_packet(
            store, correction, data['public'], version=V2)
    with closing(sqlite3.connect(':memory:')) as night:
        receipts, reports = ReceiptStore(night), ReportStore(night)
        receipts.initialize()
        reports.initialize()
        receipt_id = receive_packet(receipts, packet, registration)
        anchors = (('A', '2'), ('B', '4'))
        report = sign_interval_review(receipts, reports, receipt_id, Fraction(0), Fraction(1), anchors)
        assert (report.total_cost, report.correction, report.pairs) == (
            Fraction(8), Fraction(1), anchors)
        dead_id = receive_packet(receipts, reduced_packet, reduced_registration)
        dead = sign_interval_review(receipts, reports, dead_id, Fraction(0), Fraction(1), anchors)
        assert (dead.total_cost, dead.correction, dead.pairs) == (None, None, None)
        print('V2 windowed run: anchored cost 8 at correction 1; reduced revision is a legal no-solution')


def verify_rejections(cases):
    packet, registration = issue_interval_case(next(iter(cases.values())))
    with closing(sqlite3.connect(':memory:')) as night:
        receipts, reports = ReceiptStore(night), ReportStore(night)
        receipts.initialize()
        reports.initialize()
        receipt_id = receive_packet(receipts, packet, registration)

        # A V1 receipt cannot drive an interval signing.
        v1_case = dict(next(iter(cases.values())))
        with closing(sqlite3.connect(':memory:')) as source:
            store = RevisionStore(source)
            store.initialize()
            initialize_issue_store(source)
            draft = import_run(store, v1_case['run_id'], v1_case['main'],
                               v1_case['secondary'], window=v1_case['window'])
            v1_packet, v1_registration = issue_packet(store, freeze_run(store, draft), v1_case['public'])
        v1_receipt = receive_packet(receipts, v1_packet, v1_registration)
        before = reports.connection.total_changes
        calls = (
            lambda: sign_interval_review(receipts, reports, v1_receipt, Fraction(0), Fraction(0)),
            lambda: sign_interval_review(receipts, reports, receipt_id, Fraction(1), Fraction(0)),
            lambda: sign_interval_review(receipts, reports, receipt_id, True, Fraction(0)),
            lambda: sign_interval_review(receipts, reports, receipt_id, Fraction(0), 1.5),
            lambda: sign_interval_review(receipts, reports, receipt_id, Fraction(0), Fraction(0),
                                         (('H', 'S2'),)),
            lambda: sign_interval_review(receipts, reports, receipt_id, Fraction(0), Fraction(0),
                                         (('A', 'S2'),)),
            lambda: sign_interval_review(receipts, reports, receipt_id, Fraction(0), Fraction(0),
                                         (('H', 'S2'), ('H', 'S10'))),
            lambda: sign_interval_review(receipts, reports, receipt_id, Fraction(0), Fraction(0),
                                         (('H', 'S2'), ('H', 'S10'), ('H', 'S2'))),
        )
        for number, call in enumerate(calls):
            try:
                call()
            except ValueError:
                continue
            raise AssertionError(f'illegal interval signing #{number} was accepted')
        assert reports.connection.total_changes == before
        assert get_report(reports, 1) is None

        # Unknown receipt numbers are lookup failures, not value errors.
        try:
            sign_interval_review(receipts, reports, receipt_id + 99, Fraction(0), Fraction(0))
        except LookupError:
            pass
        else:
            raise AssertionError('an unknown receipt must raise LookupError')

        # A version-2 envelope cannot pass the V1 evidence verifier.
        flipped = seal_envelope(registration.issue_id, read_envelope(packet).body, version=V1)
        try:
            receive_packet(receipts, flipped, registration)
        except ValueError:
            pass
        else:
            raise AssertionError('a V2 body must not pass the V1 receipt check')
        # A V1 envelope flipped to V2 must fail the interval structure check.
        flipped_v2 = seal_envelope(v1_registration.issue_id, read_envelope(v1_packet).body, version=V2)
        try:
            receive_packet(receipts, flipped_v2, v1_registration)
        except ValueError:
            pass
        else:
            raise AssertionError('a V1 body must not pass the V2 receipt check')
        count = receipts.connection.execute('SELECT count(*) FROM intake_receipts').fetchone()[0]
        assert count == 2  # only the two legitimate receipts

        # Commit refusal leaves no report.
        with reject_commit(reports.connection) as refused:
            try:
                sign_interval_review(receipts, reports, receipt_id, Fraction(-4), Fraction(0))
            except sqlite3.DatabaseError:
                pass
            else:
                raise AssertionError('the refused commit must surface')
        assert refused == ['COMMIT']
        assert reports.connection.execute('SELECT count(*) FROM signed_reports').fetchone()[0] == 0
        signed = sign_interval_review(receipts, reports, receipt_id, Fraction(-4), Fraction(0))
        assert get_report(reports, signed.report_id) == signed

        # V1 signing remains available on the same store and keeps a shared,
        # non-colliding report numbering.
        legacy = sign_review(receipts, reports, v1_receipt)
        assert legacy.report_id != signed.report_id
        assert isinstance(get_report(reports, legacy.report_id), type(legacy))
    print('V2 guards: V1 receipts, reversed intervals, illegal endpoints and anchors, '
          'cross-version bodies and commit refusals are all rejected without rows')


def main():
    cases = json.loads((SAMPLES / 'zero_interval.json').read_text(encoding='utf-8'))
    verify_samples(cases)
    verify_windowed()
    verify_rejections(cases)
    print('All V2 interval handoff checks passed.')


if __name__ == '__main__':
    main()
