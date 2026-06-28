"""Compatibility package for editable installs.

This re-exports ``packages.chantheory`` while keeping ``chantheory.*`` imports
working for apps and tests that rely on the top-level package name.
"""

from importlib import import_module as _import_module

_impl = _import_module("packages.chantheory")

__all__ = list(getattr(_impl, "__all__", []))
__doc__ = _impl.__doc__
__path__ = _impl.__path__

for _name in __all__:
    globals()[_name] = getattr(_impl, _name)
