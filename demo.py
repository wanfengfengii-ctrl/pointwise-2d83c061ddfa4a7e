import json
import sqlite3
from contextlib import closing
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory

from alignment.reference import preview
from intake.receipts import ReceiptStore
from intake.receive import receive_packet
from issue.envelope import V2
from issue.register import get_issue_registration, initialize_issue_store, issue_packet
from recording.import_run import import_run
from recording.revision_store import RevisionStore
from recording.revisions import freeze_run
from review.interval_publish import sign_interval_review
from review.publish import sign_review
from review.report_store import ReportStore
from review.retrieve import get_report


SAMPLES = Path(__file__).resolve().parent / 'samples'


def load_recording(name):
    return json.loads((SAMPLES / f'{name}.json').read_text(encoding='utf-8'))


def prepare_packet(store, data, *, corrects=None, secondary=None, version=1):
    if secondary is None:
        secondary = data['secondary']
    draft = import_run(store, data['run_id'], data['main'], secondary, corrects=corrects)
    revision = freeze_run(store, draft)
    reference = preview(store, revision)
    if reference is None:
        raise RuntimeError('the demo recording must have an unconditional path')
    packet, registration = issue_packet(store, revision, data['public'], version=version)
    saved_registration = get_issue_registration(store.connection, registration.issue_id)
    if saved_registration != registration:
        raise RuntimeError('the issued packet registration was not retained')
    label = 'V1' if version == 1 else 'V2'
    print(f'{label} {data["run_id"]} revision {revision} online cost {reference.total_cost}, '
          f'issue {registration.issue_id}')
    return revision, (packet, saved_registration)


def result(report):
    return {'total_cost': report.total_cost, 'pairs': report.pairs}


def show_scenario(case_id, inputs, expected, actual, saved, oracle):
    if result(actual) != expected or saved != actual:
        raise RuntimeError(f'{case_id} gave an unexpected result or stored snapshot')
    payload = {
        'case_id': case_id,
        'input': inputs,
        'expected': expected,
        'actual': result(actual),
        'downstream_expected': {'receipt_id': actual.receipt_id, 'result': expected},
        'downstream_actual': {'receipt_id': saved.receipt_id, 'result': result(saved)},
        'code_paths': ['issue/evidence.py', 'intake/receive.py', 'review/reconcile.py',
                       'review/report_store.py', 'review/retrieve.py'],
        'oracle': oracle,
    }
    print('POINTWISE_SCENARIO ' + json.dumps(payload, ensure_ascii=False, sort_keys=True))


def show_interval_scenario(case_id, actual, saved, receipt_id, registration):
    if saved != actual:
        raise RuntimeError(f'{case_id} stored snapshot disagrees with the signed report')
    payload = {
        'case_id': case_id,
        'receipt_id': receipt_id,
        'issue_id': registration.issue_id,
        'body_sha256': registration.body_sha256,
        'interval': [str(actual.interval[0]), str(actual.interval[1])],
        'anchors': list(actual.anchors),
        'total_cost': None if actual.total_cost is None else str(actual.total_cost),
        'correction': None if actual.correction is None else str(actual.correction),
        'pairs': list(actual.pairs) if actual.pairs is not None else None,
        'reopened_report_id': saved.report_id,
        'code_paths': ['interval/solver.py', 'interval/plfuncs.py', 'issue/interval_evidence.py',
                       'intake/verify_interval.py', 'review/interval_publish.py',
                       'review/report_store.py', 'review/retrieve.py'],
    }
    print('POINTWISE_INTERVAL ' + json.dumps(payload, ensure_ascii=False, sort_keys=True))


def main():
    data = load_recording('windowed_run')
    tied = load_recording('tied_run')
    zero_cases = json.loads((SAMPLES / 'zero_interval.json').read_text(encoding='utf-8'))
    anchors = (('A', '2'), ('B', '4'))
    reduced = [row for row in data['secondary'] if row[0] != data['remove_secondary']]
    with TemporaryDirectory(prefix='vibration-archive-') as directory:
        root = Path(directory)
        with closing(sqlite3.connect(root / 'station.sqlite')) as source:
            store = RevisionStore(source)
            store.initialize()
            initialize_issue_store(source)
            original, first = prepare_packet(store, data)
            _, second = prepare_packet(store, data, corrects=original, secondary=reduced)
            _, third = prepare_packet(store, tied)
            # V2 interval packets for the windowed run and a fractional sample.
            _, interval_packet = prepare_packet(store, data, version=V2)
            _, reduced_interval = prepare_packet(
                store, data, corrects=original, secondary=reduced, version=V2)
            fractional = zero_cases['fractional_switch']
            draft = import_run(store, fractional['run_id'], fractional['main'],
                               fractional['secondary'], window=fractional['window'])
            fractional_revision = freeze_run(store, draft)
            packet, fractional_registration = issue_packet(
                store, fractional_revision, fractional['public'], version=V2)
            fractional_packet = (packet, fractional_registration)

        # The source database is closed from here on: the night shift only has
        # the received bytes and the original issue registrations.
        with closing(sqlite3.connect(root / 'night.sqlite')) as night:
            receipts, reports = ReceiptStore(night), ReportStore(night)
            receipts.initialize()
            reports.initialize()
            first_id = receive_packet(receipts, *first)
            single_a = sign_review(receipts, reports, first_id, anchors[:1])
            single_b = sign_review(receipts, reports, first_id, anchors[1:])
            if (single_a.total_cost, single_b.total_cost) != (0, 0):
                raise RuntimeError('the two individual tickets must each admit a zero-cost path')
            print(f'receipt {first_id} single-anchor costs {single_a.total_cost}, {single_b.total_cost}')
            original_report = sign_review(receipts, reports, first_id, anchors)
            revised_report = sign_review(receipts, reports, receive_packet(receipts, *second), anchors)
            tied_report = sign_review(receipts, reports, receive_packet(receipts, *third))

            interval_receipt = receive_packet(receipts, *interval_packet)
            interval_report = sign_interval_review(
                receipts, reports, interval_receipt, Fraction(0), Fraction(1), anchors)
            reduced_receipt = receive_packet(receipts, *reduced_interval)
            no_solution = sign_interval_review(
                receipts, reports, reduced_receipt, Fraction(0), Fraction(1), anchors)
            fractional_receipt = receive_packet(
                receipts, fractional_packet[0], fractional_registration)
            fractional_report = sign_interval_review(
                receipts, reports, fractional_receipt, Fraction(-3), Fraction(0))
            single_point = sign_interval_review(
                receipts, reports, fractional_receipt, Fraction(-1, 3), Fraction(-1, 3))

        # Reopen the isolated archive without the source database and reclaim
        # every signed conclusion purely by its report number.
        with closing(sqlite3.connect(root / 'night.sqlite')) as night:
            reports = ReportStore(night)
            reports.initialize()
            show_scenario('v5', {'main': data['main'], 'secondary': data['secondary'], 'anchors': anchors},
                          {'total_cost': 5, 'pairs': anchors}, original_report,
                          get_report(reports, original_report.report_id),
                          'Between secondary positions 2 and 4 only sample 3 is available for H. Its difference is 5.')
            show_scenario('v6', {'main': data['main'], 'secondary': reduced, 'anchors': anchors},
                          {'total_cost': None, 'pairs': None}, revised_report,
                          get_report(reports, revised_report.report_id),
                          'Removing sample 3 leaves no secondary position strictly between the two anchors.')
            show_scenario('v8', {'main': tied['main'], 'secondary': tied['secondary'], 'public': tied['public']},
                          {'total_cost': 5, 'pairs': (('A', 'S2'), ('B', 'S11'))}, tied_report,
                          get_report(reports, tied_report.report_id),
                          'Hidden endpoints cost 2 and 3. S2 precedes the equally priced S10 in recorder order.')

            for case_id, signed, receipt_id, registration in (
                ('v2-windowed-anchored', interval_report, interval_receipt, interval_packet[1]),
                ('v2-removed-secondary', no_solution, reduced_receipt, reduced_interval[1]),
                ('v2-fractional-switch', fractional_report, fractional_receipt, fractional_registration),
                ('v2-single-point', single_point, fractional_receipt, fractional_registration),
            ):
                show_interval_scenario(
                    case_id, signed, get_report(reports, signed.report_id), receipt_id, registration)


if __name__ == '__main__':
    main()
