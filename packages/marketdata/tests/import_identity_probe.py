"""Fresh-process assertions used by test_import_compat (stdlib only)."""
from importlib import import_module
import json
from pathlib import Path
import site
import sys
import unittest

layout, root, first, package = sys.argv[1:]
root = Path(root).resolve()
if layout in {"editable", "installed"}:
    site.addsitedir(str(root / "site"))
    source = (root / ("source" if layout == "editable" else "site")).resolve()
    from importlib.metadata import distribution
    direct_url = json.loads(distribution("stockpilot").read_text("direct_url.json"))
    assert bool(direct_url.get("dir_info", {}).get("editable")) == (layout == "editable")
    # -I -S never processes the host environment's .pth files. Only the
    # explicitly built temporary editable installation may register a finder.
    editable_modules = [module for name, module in sys.modules.items()
                        if name.startswith("__editable__")]
    assert bool(editable_modules) == (layout == "editable")
    for module in editable_modules:
        assert Path(module.__file__).resolve().is_relative_to(root / "site")
else:
    source = root
    if layout != "packages-only":
        sys.path.insert(0, str(root))
    if layout in {"packages-first", "packages-only"}:
        sys.path.insert(0, str(root / "packages"))

check = unittest.TestCase()
# Trace module body execution, not just final sys.modules aliases: otherwise
# an eager double load followed by re-exporting could hide duplicated state.
executions = {}
def trace(frame, event, arg):
    if event == "call" and frame.f_code.co_name == "<module>":
        path = Path(frame.f_code.co_filename)
        if path.is_relative_to(source / "packages" / package):
            executions[str(path)] = executions.get(str(path), 0) + 1
    return trace

sys.setprofile(trace)
if first == "wrapper":
    sys.path.insert(0, str(root / "skill" / "scripts"))
    import_module("market_data")
a_name, b_name = (package, "packages." + package) if first == "short" else ("packages." + package, package)
a, b = import_module(a_name), import_module(b_name)
check.assertIs(a, b)
check.assertTrue(Path(a.__file__).resolve().is_relative_to(source / "packages"))

modules = {
    "marketdata": [("provider_result", "MarketDataResult"), ("provider_request_queue", "ProviderQueueClosedError"),
                   ("repositories.kline_store", "KLineStore"), ("services.kline_data_service", "KLineDataService")],
    "chantheory": [("schema", "AnalysisResult"), ("normalize", "NormalizationError"), ("engine", "EngineImportError")],
    "fundamentalscreener": [("schema", "SectorsPayload"), ("repositories", "FixtureRepository"), ("sqlite_repository", "QualityInvalidError")],
}
for suffix, symbol in modules[package]:
    # repositories/services are deliberately imported after package initialization.
    x, y = import_module(a_name + "." + suffix), import_module(b_name + "." + suffix)
    check.assertIs(x, y)
    check.assertEqual(x.__spec__.name, "packages." + package + "." + suffix)
    check.assertEqual(x.__package__, x.__spec__.parent)
    check.assertIs(getattr(x, symbol), getattr(y, symbol))
    check.assertTrue(getattr(x, symbol).__module__.startswith("packages." + package))
    if isinstance(getattr(x, symbol), type) and issubclass(getattr(x, symbol), Exception):
        try:
            raise getattr(x, symbol)("cross-namespace")
        except getattr(y, symbol):
            pass
        else:
            check.fail("exception was not caught")

if package == "marketdata":
    q1 = import_module(a_name + ".provider_request_queue")
    q2 = import_module(b_name + ".provider_request_queue")
    result_type = import_module(a_name + ".provider_result").MarketDataResult
    queue = q2.get_shared_provider_request_queue()
    try:
        check.assertIs(q1.get_shared_provider_request_queue(), queue)
        check.assertIs(q1._SHARED_QUEUE, q2._SHARED_QUEUE)
        calls = []
        def operation():
            calls.append(1)
            return result_type(len(calls) == 2, [])
        outcome = queue.execute("mixed-import-retry", operation, max_attempts=2, timeout=5)
        check.assertEqual(len(calls), 2)
        check.assertTrue(outcome.result.success)
    finally:
        queue.shutdown()
    try:
        queue.submit("closed", lambda: None)
    except q2.ProviderQueueClosedError:
        pass
    else:
        check.fail("closed queue did not raise across namespaces")
else:
    check.assertNotIn("marketdata", sys.modules)
    check.assertNotIn("packages.marketdata", sys.modules)

# A copied standalone tree remains independent even with the aliases active.
if package == "marketdata":
    scripts = root / "skill" / "scripts" if layout in {"editable", "installed"} else root / "skills/china-stock-analysis/scripts"
    sys.path.insert(0, str(scripts))
    wrapper = import_module("market_data")
    check.assertIs(wrapper.TencentStockDataProvider, a.TencentStockDataProvider)
    for wrapper_name, suffix, symbol in (
        ("runtime_paths", "runtime_paths", "RuntimePaths"),
        ("repositories.kline_store", "repositories.kline_store", "KLineStore"),
        ("repositories.securities_store", "repositories.securities_store", "SecuritiesStore"),
        ("services.kline_data_service", "services.kline_data_service", "KLineDataService"),
    ):
        check.assertIs(getattr(import_module(wrapper_name), symbol),
                       getattr(import_module("packages.marketdata." + suffix), symbol))
    standalone = import_module("_standalone_marketdata.provider_result")
    check.assertIsNot(standalone.MarketDataResult, a.MarketDataResult)
    check.assertTrue(wrapper.TencentStockDataProvider.__module__.startswith("packages.marketdata."))

sys.setprofile(None)
for path, count in executions.items():
    # The packages-first bootstrap visits __init__ once under each name, but
    # only the canonical guard executes imports/exports. Business bodies once.
    if Path(path).name != "__init__.py":
        check.assertEqual(count, 1, path)
for name, module in list(sys.modules.items()):
    if name.startswith(package + "."):
        check.assertIs(module, sys.modules["packages." + name])
        parent, _, child = name.rpartition(".")
        check.assertIs(getattr(sys.modules[parent], child), module)
check.assertFalse(any("_standalone_marketdata" in str(f) for f in sys.meta_path))
print(json.dumps({"layout": layout, "first": first, "package": package, "origin": a.__file__}))
