"""Import identity for the three historical public package names.

Only package initialization calls this module. Business modules (including the
standalone skill copies) do not depend on it. Short names are lazy aliases of
``packages.*``; their loaders never execute the implementation a second time.
"""

from importlib import import_module
from importlib.abc import Loader, MetaPathFinder
from importlib.util import find_spec, spec_from_loader
import sys


_PACKAGE_NAMES = frozenset({"marketdata", "chantheory", "fundamentalscreener"})


class _AliasLoader(Loader):
    def __init__(self, canonical_name):
        self.canonical_name = canonical_name

    def create_module(self, spec):
        module = import_module(self.canonical_name)
        self.canonical_spec = module.__spec__
        return module

    def exec_module(self, module):
        # module_from_spec assigns the alias spec even to an existing module.
        # Restore it so relative imports, reload and module metadata continue
        # to agree with the canonical __name__ and __package__.
        module.__spec__ = self.canonical_spec

    def get_code(self, fullname):
        # Preserve ``python -m <short-name>.cli`` without importing the CLI
        # first. runpy executes this code as __main__, as with a normal loader.
        spec = find_spec(self.canonical_name)
        return spec.loader.get_code(self.canonical_name)


class _AliasFinder(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.partition(".")[0] not in _PACKAGE_NAMES:
            return None
        canonical_name = "packages." + fullname
        canonical_spec = find_spec(canonical_name)
        if canonical_spec is None:
            return None
        return spec_from_loader(
            fullname,
            _AliasLoader(canonical_name),
            is_package=canonical_spec.submodule_search_locations is not None,
        )


_finder = _AliasFinder()


def initialize_package(name):
    """Return whether this boundary should execute its implementation body.

    A short-name boundary can be either the repository wrapper or the actual
    implementation found via sys.path. Both redirect before relative imports.
    Canonical initialization installs the finder before loading any children.
    """
    short_name = name.removeprefix("packages.")
    if short_name not in _PACKAGE_NAMES:
        raise ValueError(f"unsupported compatibility package: {name}")
    if _finder not in sys.meta_path:
        sys.meta_path.insert(0, _finder)
    if name == short_name:
        sys.modules[name] = import_module("packages." + short_name)
        return False
    return True
