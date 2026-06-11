"""pricepulse — competitor pricing/feature-change monitor. Part of the Cognis Neural Suite."""

from pricepulse.core import (
    TOOL_NAME,
    TOOL_VERSION,
    DEFAULT_STORE,
    PricePulseError,
    FieldDef,
    Snapshot,
    Store,
    make_field,
    extract,
    extract_field,
    parse_price,
    fetch,
    take_snapshot,
    diff_values,
    diff_page,
    summarize_change,
)

__version__ = TOOL_VERSION

__all__ = [
    "TOOL_NAME",
    "TOOL_VERSION",
    "DEFAULT_STORE",
    "__version__",
    "PricePulseError",
    "FieldDef",
    "Snapshot",
    "Store",
    "make_field",
    "extract",
    "extract_field",
    "parse_price",
    "fetch",
    "take_snapshot",
    "diff_values",
    "diff_page",
    "summarize_change",
]
