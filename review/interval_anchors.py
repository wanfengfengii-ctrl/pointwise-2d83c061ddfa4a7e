from collections.abc import Iterable
from dataclasses import dataclass

from interval.solver import IntervalEvidence


@dataclass(frozen=True, slots=True)
class IntervalAnchor:
    main_index: int
    secondary_index: int


def normalize_interval_anchors(
    evidence: IntervalEvidence, anchors: Iterable[tuple[str, str]],
) -> tuple[IntervalAnchor, ...]:
    """Validate tickets against the public directories and order them."""
    main = {
        sample_id: position
        for sample_id, position in zip(evidence.public_main_ids, evidence.public_positions)
    }
    secondary = {sample_id: index for index, sample_id in enumerate(evidence.secondary_ids)}
    try:
        items = iter(anchors)
    except TypeError as error:
        raise ValueError('anchors must be a sequence of pairs') from error
    selected = {}
    for item in items:
        if len(selected) == 2:
            raise ValueError('at most two anchors are allowed')
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError('each anchor requires two sample ids')
        main_id, secondary_id = item
        if type(main_id) is not str or main_id not in main:
            raise ValueError(f'unknown public main sample {main_id!r}')
        main_index = main[main_id]
        if type(secondary_id) is not str or secondary_id not in secondary:
            raise ValueError(f'unknown secondary sample {secondary_id!r}')
        if main_index in selected:
            raise ValueError(f'repeated main sample {main_id!r}')
        selected[main_index] = IntervalAnchor(main_index, secondary[secondary_id])
    return tuple(selected[index] for index in sorted(selected))
