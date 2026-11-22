"""Structural verification of V2 interval packet bodies."""
from intake.verify_envelope import VerifiedEnvelope
from interval.solver import IntervalArc, IntervalEvidence
from issue.envelope import read_json_object


def _directory(value, field: str) -> tuple[str, ...]:
    if type(value) is not list or any(type(item) is not str or not item.strip() for item in value):
        raise ValueError(f'invalid {field} directory')
    if len(set(value)) != len(value):
        raise ValueError(f'duplicate ids in {field} directory')
    return tuple(value)


def verify_interval_evidence(verified: VerifiedEnvelope) -> IntervalEvidence:
    body = read_json_object(verified.body)
    if body.keys() != {
        'run_id', 'revision_id', 'main', 'secondary',
        'main_count', 'public_positions', 'arcs',
    }:
        raise ValueError('invalid interval packet body fields')
    registration = verified.registration
    run_id = body['run_id']
    revision_id = body['revision_id']
    if type(run_id) is not str or run_id != registration.run_id:
        raise ValueError('body recording id does not match its registration')
    if type(revision_id) is not int or not 0 < revision_id < 2**63 or revision_id != registration.revision_id:
        raise ValueError('body revision id does not match its registration')

    main = _directory(body['main'], 'main')
    secondary = _directory(body['secondary'], 'secondary')
    main_count = body['main_count']
    if type(main_count) is not int or main_count < len(main) or main_count < 0:
        raise ValueError('invalid main beat count')

    positions = body['public_positions']
    if type(positions) is not list or len(positions) != len(main):
        raise ValueError('public positions must align with the public main directory')
    public_positions = []
    previous = -1
    for position in positions:
        if type(position) is not int or not 0 <= position < main_count or position <= previous:
            raise ValueError('public positions must be increasing main track indices')
        public_positions.append(position)
        previous = position

    raw_arcs = body['arcs']
    if type(raw_arcs) is not list:
        raise ValueError('invalid interval arc records')
    arcs = []
    seen = set()
    for raw in raw_arcs:
        if type(raw) is not list or len(raw) != 3:
            raise ValueError('an interval arc requires a main index, a secondary index and a gap')
        main_index, secondary_index, difference = raw
        if type(main_index) is not int or not 0 <= main_index < main_count:
            raise ValueError('an interval arc references an unknown main beat')
        if type(secondary_index) is not int or not 0 <= secondary_index < len(secondary):
            raise ValueError('an interval arc references an unknown secondary sample')
        if type(difference) is not int:
            raise ValueError('interval reading gaps must be integer ticks')
        key = (main_index, secondary_index)
        if key in seen:
            raise ValueError('duplicate interval arc')
        seen.add(key)
        arcs.append(IntervalArc(main_index, secondary_index, difference))
    return IntervalEvidence(
        run_id=run_id,
        revision_id=revision_id,
        public_main_ids=main,
        secondary_ids=secondary,
        main_count=main_count,
        public_positions=tuple(public_positions),
        arcs=tuple(arcs),
    )
