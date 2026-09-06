"""Monetary formatting helpers — plain (non-scientific) Decimal-as-string.

DOC-008 § Financial Precision: every monetary value crossing a Capability
boundary must be a plain Decimal-as-string, never scientific notation (e.g.
``'1.7902901E+19'``) and never a native float. `str(Decimal)` can emit
scientific notation for very large or very small magnitudes (a reserve of
~1e19 renders as ``'1.7902901E+19'``), which is why **all** Decimal→str
serialization of amounts/prices in the platform must go through one helper
that forces fixed-point notation.

This module lives in `domain/` (the bottom layer) so that every Capability
(`analytics/`, `persistence/`) can call it without violating DOC-011's
import-linter contracts.

Example:
    decimal_to_plain_string(Decimal('1.7902901E+19'))  # -> '17902901000000000000'
"""

from decimal import Decimal, InvalidOperation


def decimal_to_plain_string(value: Decimal | str) -> str:
    """Serialize a `Decimal | str` as a fixed-point string, never scientific.

    Uses ``format(Decimal(value), 'f')`` which forces plain (non-exponent)
    notation. Integer Token Amounts stay integer strings
    (``'17902901000000000000'``); fractional amounts keep their fractional
    digits (``'0.0000000001'``). Accepts either a ``Decimal`` or an already-
    string value (ORM columns are typed ``str`` but carry ``Decimal`` at
    runtime), coercing both through ``Decimal`` first. This is the single
    sanctioned Decimal→str path for monetary fields (DOC-008 § Financial
    Precision), protecting the schema-level ``isdigit()``/Decimal validators
    (e.g. ``ObservationSnapshot.reserve0``, ``StateProjection.reserve0``)
    from an unrenderable scientific input.
    """
    # Passing an already-plain string through is a no-op (round-trips exactly);
    # passing a scientific string or a Decimal normalizes it to fixed-point.
    try:
        d = value if isinstance(value, Decimal) else Decimal(value)
    except InvalidOperation:
        # Not a numeric string — return untouched (defensive; callers should
        # never feed money-formatted non-numerics here).
        return value if isinstance(value, str) else ""
    return format(d, "f")
