from fractions import Fraction


def validate_endpoint(value, field: str) -> Fraction:
    """Accept a real int (never bool) or a Fraction and return an exact value."""
    if isinstance(value, Fraction):
        return value
    if type(value) is int:
        return Fraction(value)
    raise ValueError(f'{field} must be an integer or a Fraction')
