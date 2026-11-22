import json

from interval.solver import IntervalEvidence


def build_interval_body(evidence: IntervalEvidence) -> bytes:
    body = {
        'run_id': evidence.run_id,
        'revision_id': evidence.revision_id,
        'main': list(evidence.public_main_ids),
        'secondary': list(evidence.secondary_ids),
        'main_count': evidence.main_count,
        'public_positions': list(evidence.public_positions),
        'arcs': [[arc.main_index, arc.secondary_index, arc.difference] for arc in evidence.arcs],
    }
    return json.dumps(body, separators=(',', ':'), sort_keys=True).encode('utf-8')
