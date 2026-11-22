import json
import sqlite3
from dataclasses import dataclass
from fractions import Fraction


Pairs = tuple[tuple[str, str], ...]

KIND_V1 = 'v1'
KIND_INTERVAL = 'interval'


@dataclass(frozen=True, slots=True)
class SignedReport:
    report_id: int
    receipt_id: int
    issue_id: int
    run_id: str
    revision_id: int
    body_sha256: str
    anchors: Pairs
    total_cost: int | None
    pairs: Pairs | None


@dataclass(frozen=True, slots=True)
class IntervalSignedReport:
    report_id: int
    receipt_id: int
    issue_id: int
    run_id: str
    revision_id: int
    body_sha256: str
    anchors: Pairs
    interval: tuple[Fraction, Fraction]
    total_cost: Fraction | None
    correction: Fraction | None
    pairs: Pairs | None


class ReportStore:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def initialize(self) -> None:
        # One shared table keeps report ids unique across versions and means a
        # signed conclusion is always a single committed row.
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS signed_reports (
                report_id INTEGER PRIMARY KEY,
                kind TEXT NOT NULL CHECK (kind IN ('v1', 'interval')),
                receipt_id INTEGER NOT NULL,
                snapshot TEXT NOT NULL
            )
        ''')
        self._migrate_legacy_table()

    def _migrate_legacy_table(self) -> None:
        """Tag an archive created before the kind column existed."""
        columns = {
            row[1] for row in self.connection.execute('PRAGMA table_info(signed_reports)')
        }
        if 'kind' not in columns:
            with self.connection:
                self.connection.execute('ALTER TABLE signed_reports RENAME TO signed_reports_legacy')
                self.connection.execute('''
                    CREATE TABLE signed_reports (
                        report_id INTEGER PRIMARY KEY,
                        kind TEXT NOT NULL CHECK (kind IN ('v1', 'interval')),
                        receipt_id INTEGER NOT NULL,
                        snapshot TEXT NOT NULL
                    )
                ''')
                self.connection.execute('''
                    INSERT INTO signed_reports (report_id, kind, receipt_id, snapshot)
                    SELECT report_id, 'v1', receipt_id, snapshot FROM signed_reports_legacy
                ''')
                self.connection.execute('DROP TABLE signed_reports_legacy')

    def commit_report(
        self, *, receipt_id: int, issue_id: int, run_id: str, revision_id: int,
        body_sha256: str, anchors: Pairs, total_cost: int | None, pairs: Pairs | None,
    ) -> SignedReport:
        if (total_cost is None) != (pairs is None):
            raise ValueError('the report cost and pairings must describe the same result')
        snapshot = {
            'issue_id': issue_id,
            'run_id': run_id,
            'revision_id': revision_id,
            'body_sha256': body_sha256,
            'anchors': anchors,
            'total_cost': total_cost,
            'pairs': pairs,
        }
        payload = json.dumps(snapshot, separators=(',', ':'), sort_keys=True)
        if self.connection.in_transaction:
            raise sqlite3.ProgrammingError('report signing requires an idle connection')
        with self.connection:
            self.connection.execute('BEGIN IMMEDIATE')
            cursor = self.connection.execute('''
                INSERT INTO signed_reports (kind, receipt_id, snapshot) VALUES (?, ?, ?)
            ''', (KIND_V1, receipt_id, payload))
            report_id = cursor.lastrowid
        return SignedReport(report_id, receipt_id, issue_id, run_id, revision_id, body_sha256, anchors, total_cost, pairs)

    def commit_interval_report(
        self, *, receipt_id: int, issue_id: int, run_id: str, revision_id: int,
        body_sha256: str, anchors: Pairs, interval: tuple[Fraction, Fraction],
        total_cost: Fraction | None, correction: Fraction | None, pairs: Pairs | None,
    ) -> IntervalSignedReport:
        if (total_cost is None) != (correction is None) or (total_cost is None) != (pairs is None):
            raise ValueError('the interval result fields must describe the same outcome')
        snapshot = {
            'kind': KIND_INTERVAL,
            'issue_id': issue_id,
            'run_id': run_id,
            'revision_id': revision_id,
            'body_sha256': body_sha256,
            'anchors': anchors,
            'interval': [str(interval[0]), str(interval[1])],
            'total_cost': None if total_cost is None else str(total_cost),
            'correction': None if correction is None else str(correction),
            'pairs': pairs,
        }
        payload = json.dumps(snapshot, separators=(',', ':'), sort_keys=True)
        if self.connection.in_transaction:
            raise sqlite3.ProgrammingError('report signing requires an idle connection')
        with self.connection:
            self.connection.execute('BEGIN IMMEDIATE')
            cursor = self.connection.execute('''
                INSERT INTO signed_reports (kind, receipt_id, snapshot) VALUES (?, ?, ?)
            ''', (KIND_INTERVAL, receipt_id, payload))
            report_id = cursor.lastrowid
        return IntervalSignedReport(
            report_id, receipt_id, issue_id, run_id, revision_id, body_sha256,
            anchors, (interval[0], interval[1]), total_cost, correction, pairs,
        )

    def load_report(self, report_id: int):
        if type(report_id) is not int or not 0 < report_id < 2**63:
            raise ValueError('invalid report id')
        row = self.connection.execute('''
            SELECT kind, receipt_id, snapshot FROM signed_reports WHERE report_id = ?
        ''', (report_id,)).fetchone()
        if row is None:
            return None
        kind, receipt_id, payload = row
        if kind == KIND_V1:
            return self._decode_legacy(report_id, receipt_id, payload)
        if kind == KIND_INTERVAL:
            return self._decode_interval(report_id, receipt_id, payload)
        raise sqlite3.DatabaseError(f'unknown report kind {kind!r}')

    @staticmethod
    def _decode_legacy(report_id: int, receipt_id: int, payload: str) -> SignedReport:
        data = json.loads(payload)
        anchors = tuple(tuple(pair) for pair in data['anchors'])
        pairs = None if data['pairs'] is None else tuple(tuple(pair) for pair in data['pairs'])
        return SignedReport(
            report_id, receipt_id, data['issue_id'], data['run_id'], data['revision_id'],
            data['body_sha256'], anchors, data['total_cost'], pairs,
        )

    @staticmethod
    def _decode_interval(report_id: int, receipt_id: int, payload: str) -> IntervalSignedReport:
        data = json.loads(payload)
        anchors = tuple(tuple(pair) for pair in data['anchors'])
        pairs = None if data['pairs'] is None else tuple(tuple(pair) for pair in data['pairs'])
        interval = (Fraction(data['interval'][0]), Fraction(data['interval'][1]))
        total_cost = None if data['total_cost'] is None else Fraction(data['total_cost'])
        correction = None if data['correction'] is None else Fraction(data['correction'])
        return IntervalSignedReport(
            report_id, receipt_id, data['issue_id'], data['run_id'], data['revision_id'],
            data['body_sha256'], anchors, interval, total_cost, correction, pairs,
        )
