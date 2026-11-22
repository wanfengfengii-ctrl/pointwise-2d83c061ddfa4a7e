"""Derived interval evidence for V2 packets.

The isolated station never receives readings or sampling coordinates.  It only
needs, for every feasible window pair, the raw reading gap
``main_reading - secondary_reading`` and the two track positions.  The
candidate window itself stays hidden: the arc list is the only description of
feasibility.  Hidden main beat ids remain at the source station.
"""
from alignment.candidates import build_candidates
from interval.solver import IntervalArc, IntervalEvidence
from issue.disclosure import Disclosure


def build_interval_evidence(revision, disclosure: Disclosure) -> IntervalEvidence:
    arcs = []
    seen = set()
    for layer in build_candidates(revision):
        for candidate in layer:
            key = (candidate.main_index, candidate.secondary_index)
            if key in seen:
                raise ValueError('duplicate interval evidence arc')
            seen.add(key)
            difference = (
                revision.main[candidate.main_index].reading
                - revision.secondary[candidate.secondary_index].reading
            )
            arcs.append(IntervalArc(candidate.main_index, candidate.secondary_index, difference))
    return IntervalEvidence(
        run_id=revision.run_id,
        revision_id=revision.revision_id,
        public_main_ids=disclosure.main_ids,
        secondary_ids=tuple(sample.sample_id for sample in revision.secondary),
        main_count=len(revision.main),
        public_positions=disclosure.main_indices,
        arcs=tuple(arcs),
    )
