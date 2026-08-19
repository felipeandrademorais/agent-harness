"""Shared exception groups for intentional boundary catch sites."""

from __future__ import annotations

# Operational / integration failures — narrower than bare `Exception` for BLE001.
BOUNDARY_ERRORS: tuple[type[BaseException], ...] = (
    OSError,
    RuntimeError,
    TimeoutError,
    ConnectionError,
    ValueError,
    TypeError,
    AttributeError,
    KeyError,
    ImportError,
    LookupError,
    ArithmeticError,
)
