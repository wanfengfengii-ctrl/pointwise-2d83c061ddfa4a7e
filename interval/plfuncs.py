"""Exact continuous piecewise-linear functions over a closed rational domain.

The interval review scores a family of alignments whose per-pair cost is
``|t - d|`` for an integer difference ``d`` and a rational correction ``t``.
Dynamic programming over candidate paths therefore only needs continuous
piecewise-linear functions with rational breakpoints, combined by adding
absolute values and taking pointwise minima.  All arithmetic uses
``fractions.Fraction`` so the signed maxima stay exact.
"""
from bisect import bisect_right
from fractions import Fraction

Number = Fraction


class PLFunction:
    """A continuous piecewise-linear function on ``[xs[0], xs[-1]]``.

    ``xs`` and ``ys`` hold the compacted vertices; ``slopes[i]`` is the slope
    of the segment from ``xs[i]`` to ``xs[i + 1]``.  A single-point domain has
    no slopes.
    """

    __slots__ = ('xs', 'ys', 'slopes')

    def __init__(self, xs, ys, slopes):
        if len(xs) != len(ys) or len(slopes) != len(xs) - 1 or not xs:
            raise ValueError('invalid piecewise-linear function')
        self.xs = tuple(xs)
        self.ys = tuple(ys)
        self.slopes = tuple(slopes)

    @staticmethod
    def constant(value, lower, upper):
        if lower > upper:
            raise ValueError('an empty domain is not representable')
        return PLFunction((lower,), (value,), ()) if lower == upper else PLFunction(
            (lower, upper), (value, value), (Fraction(0),),
        )

    @staticmethod
    def from_points(points):
        """Build a function from vertices, merging collinear middle points."""
        ordered = sorted(points)
        xs = [ordered[0][0]]
        ys = [ordered[0][1]]
        for x, y in ordered[1:]:
            if x == xs[-1]:
                if y != ys[-1]:
                    raise ValueError('a piecewise-linear function must be single valued')
                continue
            while len(xs) >= 2:
                previous_slope = (ys[-1] - ys[-2]) / (xs[-1] - xs[-2])
                current_slope = (y - ys[-1]) / (x - xs[-1])
                if previous_slope != current_slope:
                    break
                xs.pop()
                ys.pop()
            xs.append(x)
            ys.append(y)
        slopes = tuple((ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i]) for i in range(len(xs) - 1))
        return PLFunction(xs, ys, slopes)

    @property
    def lower(self):
        return self.xs[0]

    @property
    def upper(self):
        return self.xs[-1]

    def evaluate(self, x):
        if not self.xs[0] <= x <= self.xs[-1]:
            raise ValueError('evaluation outside the function domain')
        segment = bisect_right(self.xs, x) - 1
        if segment == len(self.slopes):
            return self.ys[-1]
        return self.ys[segment] + self.slopes[segment] * (x - self.xs[segment])

    def maximum(self):
        """Return ``(smallest x, y)`` where the largest value is attained."""
        best_x, best_y = self.xs[0], self.ys[0]
        for x, y in zip(self.xs[1:], self.ys[1:]):
            if y > best_y:
                best_x, best_y = x, y
        return best_x, best_y


def add_absolute(function, difference):
    """Return ``function(t) + |t - difference|`` on the same domain."""
    points = [(x, function.evaluate(x) + abs(x - difference)) for x in function.xs]
    if function.lower < difference < function.upper and difference not in function.xs:
        points.append((difference, function.evaluate(difference)))
    return PLFunction.from_points(points)


def min_envelope(functions):
    """Pointwise minimum of functions sharing one domain, exactly."""
    if not functions:
        raise ValueError('the minimum envelope requires at least one function')
    domain = (functions[0].lower, functions[0].upper)
    for function in functions[1:]:
        if (function.lower, function.upper) != domain:
            raise ValueError('the envelope functions must share a domain')
    breaks = sorted({x for function in functions for x in function.xs})
    extras = []
    for left, right in zip(breaks, breaks[1:]):
        width = right - left
        lines = [(function.slopes[bisect_right(function.xs, left) - 1],
                  function.evaluate(left)) for function in functions]
        for index, (slope_a, value_a) in enumerate(lines):
            for slope_b, value_b in lines[index + 1:]:
                delta_slope = slope_a - slope_b
                if delta_slope == 0:
                    continue
                crossing = (value_b - value_a) / delta_slope
                if 0 < crossing < width:
                    extras.append(left + crossing)
    xs = sorted(set(breaks + extras))
    return PLFunction.from_points((x, min(function.evaluate(x) for function in functions)) for x in xs)
