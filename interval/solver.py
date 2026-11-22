"""Exact min-max interval alignment for the V2 handoff.

For each rational correction ``t`` the secondary readings are shifted by the
same value ``t``.  A full path matches every main beat to one secondary beat
inside its original sampling window, with strictly increasing secondary
positions, and costs ``sum |main_reading - (secondary_reading + t)|``.

The lowest feasible total over a closed interval is a continuous
piecewise-linear function of ``t``.  The night shift signs the maximum of that
function, the smallest correction attaining it, and the lexicographically
smallest public pairing at that correction.
"""
from dataclasses import dataclass
from fractions import Fraction

from interval.plfuncs import PLFunction, add_absolute, min_envelope


@dataclass(frozen=True, slots=True)
class IntervalArc:
    """One feasible (main position, secondary position) pair with its raw gap."""

    main_index: int
    secondary_index: int
    difference: int


@dataclass(frozen=True, slots=True)
class IntervalEvidence:
    """Position-only evidence carried by a V2 packet.

    Hidden main beats never expose their beat ids: only the public main ids
    travel, together with the full main-beat count so every derived arc can be
    addressed by its original track position.
    """

    run_id: str
    revision_id: int
    public_main_ids: tuple[str, ...]
    secondary_ids: tuple[str, ...]
    main_count: int
    public_positions: tuple[int, ...]
    arcs: tuple[IntervalArc, ...]


@dataclass(frozen=True, slots=True)
class IntervalResult:
    total_cost: Fraction | None
    correction: Fraction | None
    pairs: tuple[tuple[str, str], ...] | None


def build_layers(evidence: IntervalEvidence) -> tuple[tuple[IntervalArc, ...], ...]:
    layers = [[] for _ in range(evidence.main_count)]
    for arc in evidence.arcs:
        layers[arc.main_index].append(arc)
    return tuple(tuple(sorted(layer, key=lambda arc: arc.secondary_index)) for layer in layers)


def solve_interval(
    evidence: IntervalEvidence,
    lower: Fraction,
    upper: Fraction,
    anchors: dict[int, int],
) -> IntervalResult:
    """Compute the signed min-max answer for one closed interval."""
    layers = build_layers(evidence)
    fixed = dict(anchors)
    states = {-1: PLFunction.constant(Fraction(0), lower, upper)}
    for main_index, layer in enumerate(layers):
        following = {}
        for arc in layer:
            index = arc.secondary_index
            if fixed.get(main_index, index) != index:
                continue
            prefixes = [function for end, function in states.items() if end < index]
            if not prefixes:
                continue
            shifted = add_absolute(min_envelope(prefixes), arc.difference)
            current = following.get(index)
            if current is None:
                following[index] = shifted
            else:
                following[index] = min_envelope((current, shifted))
        if not following:
            return IntervalResult(None, None, None)
        states = following

    envelope = min_envelope(tuple(states.values()))
    correction, total_cost = envelope.maximum()
    public_positions = evidence.public_positions
    public_keys = _best_public_key(layers, fixed, correction, public_positions)
    pairs = tuple(
        (evidence.public_main_ids[rank], evidence.secondary_ids[public_keys[rank]])
        for rank, _ in enumerate(public_positions)
    )
    return IntervalResult(total_cost, correction, pairs)


def _best_public_key(layers, fixed, correction, public_positions):
    """Fixed-correction DP, breaking ties on the public secondary positions."""
    public_rank = {main_index: rank for rank, main_index in enumerate(public_positions)}
    states = {-1: (Fraction(0), ())}
    for main_index, layer in enumerate(layers):
        following = {}
        rank = public_rank.get(main_index)
        for arc in layer:
            index = arc.secondary_index
            if fixed.get(main_index, index) != index:
                continue
            prefix = min((state for end, state in states.items() if end < index), default=None)
            if prefix is None:
                continue
            cost = prefix[0] + abs(Fraction(arc.difference) - correction)
            key = prefix[1] + ((index,) if rank is not None else ())
            score = (cost, key)
            current = following.get(index)
            if current is None or score < current:
                following[index] = score
        if not following:
            raise ValueError('the correction lost a feasible path')
        states = following
    return min(states.values())[1]
