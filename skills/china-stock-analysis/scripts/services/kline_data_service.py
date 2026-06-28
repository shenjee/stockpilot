"""Compatibility wrapper for the shared K-line data service."""

from importlib import import_module as _import_module
from pathlib import Path
import sys

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
_ROOT = Path(__file__).resolve().parents[4]
_PACKAGES_DIR = _ROOT / "packages"
for _path in (str(_SCRIPTS_DIR), str(_ROOT), str(_PACKAGES_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

try:
    _impl = _import_module("marketdata.services.kline_data_service")
except ModuleNotFoundError as exc:
    if exc.name != "marketdata":
        raise
    _impl = _import_module("_standalone_marketdata.services.kline_data_service")

__all__ = [
    "DEFAULT_LOOKBACK_DAYS",
    "DEFAULT_MIN_LOCAL_COUNT",
    "MINUTE_TIMEFRAMES",
    "KLineDataService",
]
__doc__ = _impl.__doc__

for _name in __all__:
    globals()[_name] = getattr(_impl, _name)
