"""Compatibility wrapper for shared market-data providers."""

from importlib import import_module as _import_module
from pathlib import Path
import sys

_SCRIPTS_DIR = Path(__file__).resolve().parent
_ROOT = Path(__file__).resolve().parents[3]
_PACKAGES_DIR = _ROOT / "packages"
for _path in (str(_SCRIPTS_DIR), str(_ROOT), str(_PACKAGES_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

try:
    _impl = _import_module("marketdata.market_data")
except ModuleNotFoundError as exc:
    if exc.name != "marketdata":
        raise
    _impl = _import_module("_standalone_marketdata.market_data")

__all__ = [
    "INDICES",
    "MarketDataProvider",
    "TencentStockDataProvider",
    "create_market_data_provider",
    "get_market_prefix",
]
__doc__ = _impl.__doc__

for _name in __all__:
    globals()[_name] = getattr(_impl, _name)
