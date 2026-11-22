import sqlite3
from fractions import Fraction
from itertools import combinations

from intake.receive import receive_packet
from issue.envelope import V1, V2, read_envelope, seal_envelope
from issue.register import issue_packet
from interval.plfuncs import PLFunction, add_absolute, min_envelope
from interval.solver import IntervalArc, IntervalEvidence, solve_interval
from review.interval_anchors import normalize_interval_anchors
from review.interval_publish import sign_interval_review
from review.publish import sign_review
from review.report_store import IntervalSignedReport, SignedReport
from review.retrieve import get_report
from tests.support import ArchiveCase, freeze_sample, reject_commit, sample


def interval_oracle(case, lower, upper, anchors):
    """Independent exact oracle over every strictly increasing full path."""
    main, secondary = case['main'], case['secondary']
    left, right = case['window']
    fixed = dict(anchors)
    public_positions = [index for index, row in enumerate(main) if row[0] in case['public']]
    paths = []
    for indices in combinations(range(len(secondary)), len(main)):
        feasible = True
        for (main_id, sequence, _), index in zip(main, indices):
            secondary_id, time, _ = secondary[index]
            if not sequence - left <= time <= sequence + right:
                feasible = False
                break
            if main_id in fixed and fixed[main_id] != secondary_id:
                feasible = False
                break
        if not feasible:
            continue
        gaps = [main[i][2] - secondary[j][2] for i, j in enumerate(indices)]
        projected = tuple(indices[i] for i in public_positions)
        function = PLFunction.constant(Fraction(0), lower, upper)
        for difference in gaps:
            function = add_absolute(function, difference)
        paths.append((function, projected))
    if not paths:
        return None
    envelope = min_envelope([function for function, _ in paths])
    correction, total = envelope.maximum()
    projected = min(key for function, key in paths if function.evaluate(correction) == total)
    pairs = tuple((main[public_positions[r]][0], secondary[projected[r]][0])
                  for r in range(len(public_positions)))
    return total, correction, pairs


class IntervalIssueTests(ArchiveCase):
    def test_v2_packet_hides_per_beat_sources_and_keeps_directories(self):
        data = sample()
        with self.source() as store:
            revision = freeze_sample(store, data)
            wire, registration = issue_packet(store, revision, data['public'], version=V2)
            v1_wire, _ = issue_packet(store, revision, data['public'])
        envelope = read_envelope(wire)
        self.assertEqual(envelope.version, V2)
        self.assertEqual(read_envelope(v1_wire).version, V1)
        self.assertNotIn(b'"reading"', envelope.body)
        self.assertNotIn(b'"sequence"', envelope.body)
        self.assertNotIn(b'"H"', envelope.body)
        self.assertNotIn(b'"evidence"', envelope.body)
        self.assertIn(b'"arcs"', envelope.body)
        with self.night() as (receipts, reports):
            receipt_id = receive_packet(receipts, wire, registration)
            receipt = receipts.get_receipt(receipt_id)
            self.assertEqual(receipt.version, V2)
            evidence = receipt.evidence
            self.assertEqual(evidence.public_main_ids, ('A', 'B'))
            self.assertEqual(evidence.secondary_ids, tuple(row[0] for row in data['secondary']))
            self.assertEqual(evidence.main_count, 3)
            self.assertEqual(evidence.public_positions, (0, 2))
            # Position-only gaps are derived data, never raw readings.
            for arc in evidence.arcs:
                self.assertIsInstance(arc.difference, int)

    def test_envelope_version_alone_cannot_cross_the_receipt_check(self):
        data = sample()
        with self.source() as store:
            revision = freeze_sample(store, data)
            v2_wire, registration = issue_packet(store, revision, data['public'], version=V2)
            v1_wire, v1_registration = issue_packet(store, revision, data['public'])
        with self.night() as (receipts, _):
            downgraded = seal_envelope(registration.issue_id, read_envelope(v2_wire).body, version=V1)
            with self.assertRaises(ValueError):
                receive_packet(receipts, downgraded, registration)
            upgraded = seal_envelope(v1_registration.issue_id, read_envelope(v1_wire).body, version=V2)
            with self.assertRaises(ValueError):
                receive_packet(receipts, upgraded, v1_registration)
            self.assertEqual(
                receipts.connection.execute('SELECT count(*) FROM intake_receipts').fetchone()[0], 0)

    def test_unknown_packet_version_is_rejected_at_issue(self):
        data = sample()
        with self.source() as store:
            revision = freeze_sample(store, data)
            with self.assertRaises(ValueError):
                issue_packet(store, revision, data['public'], version=3)


class IntervalReviewTests(ArchiveCase):
    def _issue_v2(self, data, *, secondary=None, corrects=None):
        if secondary is None:
            secondary = data['secondary']
        with self.source() as store:
            revision = freeze_sample(store, data, secondary=secondary, corrects=corrects)
            return issue_packet(store, revision, data['public'], version=V2)

    def test_sample_queries_match_exact_oracle_and_reopen_returns_the_snapshot(self):
        import json
        from pathlib import Path
        cases = json.loads((Path(__file__).resolve().parent.parent / 'samples' / 'zero_interval.json')
                           .read_text(encoding='utf-8'))
        for name, case in cases.items():
            packet, registration = self._issue_v2(case)
            with self.night() as (receipts, reports):
                receipt_id = receive_packet(receipts, packet, registration)
                for query in case['queries']:
                    lower, upper = Fraction(query['interval'][0]), Fraction(query['interval'][1])
                    anchors = tuple(tuple(pair) for pair in query['anchors'])
                    report = sign_interval_review(receipts, reports, receipt_id, lower, upper, anchors)
                    oracle = interval_oracle(case, lower, upper, anchors)
                    self.assertIsInstance(report, IntervalSignedReport)
                    self.assertEqual(report.interval, (lower, upper))
                    self.assertEqual(report.anchors, anchors)
                    self.assertEqual(
                        (report.total_cost, report.correction, report.pairs),
                        (Fraction(query['total_cost']), Fraction(query['correction']),
                         tuple(tuple(pair) for pair in query['pairs'])),
                    )
                    self.assertEqual(
                        (report.total_cost, report.correction, report.pairs), oracle)

    def test_file_based_reopen_reclaims_interval_anchors_and_origin(self):
        import json
        from pathlib import Path
        cases = json.loads((Path(__file__).resolve().parent.parent / 'samples' / 'zero_interval.json')
                           .read_text(encoding='utf-8'))
        case = cases['tie_at_peak']
        packet, registration = self._issue_v2(case)
        anchors = (('A', 'P0'),)
        with self.night() as (receipts, reports):
            receipt_id = receive_packet(receipts, packet, registration)
            signed = sign_interval_review(
                receipts, reports, receipt_id, Fraction(-1), Fraction(1), anchors)
        with sqlite3.connect(self.intake_path) as reopened:
            from review.report_store import ReportStore
            reports = ReportStore(reopened)
            reports.initialize()
            reloaded = get_report(reports, signed.report_id)
        self.assertEqual(reloaded, signed)
        self.assertEqual(reloaded.interval, (Fraction(-1), Fraction(1)))
        self.assertEqual(reloaded.anchors, anchors)
        self.assertEqual(reloaded.issue_id, registration.issue_id)
        self.assertEqual(reloaded.body_sha256, registration.body_sha256)
        self.assertEqual(reloaded.run_id, case['run_id'])
        self.assertEqual(reloaded.receipt_id, receipt_id)
        self.assertIsNone(get_report(reports, signed.report_id + 1))

    def test_windowed_run_double_anchor_and_legal_no_solution(self):
        from tests.support import corrected_secondary
        data = sample()
        original = self._issue_v2(data)
        with self.source() as store:
            revision = freeze_sample(store, data, secondary=corrected_secondary(data))
            reduced = issue_packet(store, revision, data['public'], version=V2)
        anchors = (('A', '2'), ('B', '4'))
        with self.night() as (receipts, reports):
            receipt_id = receive_packet(receipts, *original)
            report = sign_interval_review(receipts, reports, receipt_id, Fraction(0), Fraction(1), anchors)
            self.assertEqual((report.total_cost, report.correction, report.pairs),
                             (Fraction(8), Fraction(1), anchors))
            dead_id = receive_packet(receipts, *reduced)
            dead = sign_interval_review(receipts, reports, dead_id, Fraction(0), Fraction(1), anchors)
            self.assertEqual((dead.total_cost, dead.correction, dead.pairs), (None, None, None))
            self.assertIsInstance(get_report(reports, dead.report_id), IntervalSignedReport)

    def test_conflicting_anchor_placements_are_a_legal_no_solution(self):
        data = sample()
        packet = self._issue_v2(data)
        with self.night() as (receipts, reports):
            receipt_id = receive_packet(receipts, *packet)
            conflict = sign_interval_review(
                receipts, reports, receipt_id, Fraction(-2), Fraction(2),
                (('A', '2'), ('B', '2')))
            self.assertEqual((conflict.total_cost, conflict.correction, conflict.pairs),
                             (None, None, None))
            rows = reports.connection.execute('SELECT count(*) FROM signed_reports').fetchone()[0]
            self.assertEqual(rows, 1)

    def test_v1_receipt_reversed_interval_bad_endpoints_and_illegal_anchors_rejected(self):
        data = sample()
        v2_packet = self._issue_v2(data)
        with self.source() as store:
            revision = freeze_sample(store, data)
            v1_packet = issue_packet(store, revision, data['public'])
        with self.night() as (receipts, reports):
            v2_receipt = receive_packet(receipts, *v2_packet)
            v1_receipt = receive_packet(receipts, *v1_packet)
            illegal_anchors = (
                (('H', '3'),),
                (('Z', '2'),),
                (('A', '9'),),
                (('A', '2'), ('A', '4')),
                (('A', '1'), ('B', '4'), ('A', '2')),
                (('A',),),
            )
            with self.assertRaises(ValueError):
                sign_interval_review(receipts, reports, v1_receipt, Fraction(0), Fraction(0))
            with self.assertRaises(ValueError):
                sign_interval_review(receipts, reports, v2_receipt, Fraction(1), Fraction(0))
            for value in (True, False, 1.5, '0', None):
                with self.assertRaises(ValueError):
                    sign_interval_review(receipts, reports, v2_receipt, value, Fraction(0))
            for anchors in illegal_anchors:
                with self.assertRaises(ValueError):
                    sign_interval_review(receipts, reports, v2_receipt,
                                         Fraction(0), Fraction(0), anchors)
            with self.assertRaises(LookupError):
                sign_interval_review(receipts, reports, v2_receipt + 99, Fraction(0), Fraction(0))
            self.assertEqual(
                reports.connection.execute('SELECT count(*) FROM signed_reports').fetchone()[0], 0)

    def test_commit_refusal_leaves_no_interval_report(self):
        data = sample()
        packet = self._issue_v2(data)
        with self.night() as (receipts, reports):
            receipt_id = receive_packet(receipts, *packet)
            with reject_commit(reports.connection) as refused:
                with self.assertRaises(sqlite3.DatabaseError):
                    sign_interval_review(receipts, reports, receipt_id, Fraction(-2), Fraction(2))
            self.assertEqual(refused, ['COMMIT'])
            self.assertEqual(
                reports.connection.execute('SELECT count(*) FROM signed_reports').fetchone()[0], 0)
            signed = sign_interval_review(receipts, reports, receipt_id, Fraction(-2), Fraction(2))
            self.assertEqual(get_report(reports, signed.report_id), signed)

    def test_v1_and_interval_reports_share_unique_numbers_and_keep_their_kind(self):
        data = sample()
        with self.source() as store:
            revision = freeze_sample(store, data)
            v1_packet = issue_packet(store, revision, data['public'])
            v2_packet = issue_packet(store, revision, data['public'], version=V2)
        with self.night() as (receipts, reports):
            v1_receipt = receive_packet(receipts, *v1_packet)
            v2_receipt = receive_packet(receipts, *v2_packet)
            legacy = sign_review(receipts, reports, v1_receipt)
            interval = sign_interval_review(receipts, reports, v2_receipt, Fraction(-1), Fraction(1))
            self.assertNotEqual(legacy.report_id, interval.report_id)
            self.assertIsInstance(get_report(reports, legacy.report_id), SignedReport)
            self.assertIsInstance(get_report(reports, interval.report_id), IntervalSignedReport)

    def test_empty_main_path_costs_zero_at_the_lower_bound(self):
        case = {'run_id': 'EMPTY', 'main': [], 'secondary': [], 'public': [], 'window': (2, 2)}
        packet = self._issue_v2(case)
        with self.night() as (receipts, reports):
            receipt_id = receive_packet(receipts, *packet)
            report = sign_interval_review(receipts, reports, receipt_id, Fraction(-3), Fraction(2))
            self.assertEqual((report.total_cost, report.correction, report.pairs),
                             (Fraction(0), Fraction(-3), ()))

    def test_anchor_normalization_orders_by_public_main_position(self):
        evidence = IntervalEvidence(
            'R', 1, ('A', 'B'), ('1', '2'), 2, (0, 1),
            (IntervalArc(0, 0, 0), IntervalArc(1, 1, 0)))
        normalized = normalize_interval_anchors(evidence, (('B', '2'), ('A', '1')))
        self.assertEqual([anchor.main_index for anchor in normalized], [0, 1])
        self.assertEqual([anchor.secondary_index for anchor in normalized], [0, 1])


class IntervalSolverFuzzTests(ArchiveCase):
    def test_randomized_solver_agrees_with_path_enumeration_oracle(self):
        import random
        rng = random.Random(7311)
        for _ in range(120):
            n_main = rng.randint(0, 4)
            n_sec = rng.randint(0, 5)
            main_seq = sorted(rng.sample(range(-6, 8), n_main)) if n_main else []
            sec_seq = sorted(rng.sample(range(-8, 10), n_sec)) if n_sec else []
            main = [(f'M{i}', main_seq[i], rng.randint(-5, 9)) for i in range(n_main)]
            secondary = [(f'S{j}', sec_seq[j], rng.randint(-5, 9)) for j in range(n_sec)]
            left, right = rng.randint(0, 3), rng.randint(0, 3)
            arcs, layers_map = [], {}
            for i, (_, sequence, _) in enumerate(main):
                eligible = tuple(j for j, (_, time, _) in enumerate(secondary)
                                 if sequence - left <= time <= sequence + right)
                layers_map[i] = eligible
                arcs.extend(IntervalArc(i, j, main[i][2] - secondary[j][2]) for j in eligible)
            public_positions = tuple(sorted(rng.sample(range(n_main), rng.randint(0, n_main))))
            evidence = IntervalEvidence(
                'RUN', 1, tuple(main[i][0] for i in public_positions),
                tuple(row[0] for row in secondary), n_main, public_positions, tuple(arcs))
            lower = Fraction(rng.randint(-7, 4), rng.randint(1, 4))
            upper = lower + Fraction(rng.randint(0, 9), rng.randint(1, 4))
            anchors = {}
            pool = [(i, j) for i in public_positions for j in range(n_sec)]
            rng.shuffle(pool)
            for i, j in pool[:rng.randint(0, min(2, len(pool)))]:
                anchors.setdefault(i, j)
            case = {'main': main, 'secondary': secondary, 'window': (left, right),
                    'public': [main[i][0] for i in public_positions]}
            expected = interval_oracle(case, lower, upper,
                                       tuple((main[i][0], secondary[j][0]) for i, j in anchors.items()))
            result = solve_interval(evidence, lower, upper, anchors)
            if expected is None:
                self.assertIsNone(result.total_cost)
            else:
                total, correction, pairs = expected
                self.assertEqual((result.total_cost, result.correction, result.pairs),
                                 (total, correction, pairs))
