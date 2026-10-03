"""Import identity matrix including real editable and wheel installations."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
PROBE = Path(__file__).with_name("import_identity_probe.py")


class ImportCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.install_root = Path(cls.temp.name)
        source = cls.install_root / "source"
        source.mkdir()
        for name in ("packages", "marketdata", "chantheory", "fundamentalscreener"):
            shutil.copytree(ROOT / name, source / name, ignore=shutil.ignore_patterns("__pycache__"))
        for name in ("pyproject.toml", "README.md"):
            shutil.copyfile(ROOT / name, source / name)
        shutil.copytree(ROOT / "skills/china-stock-analysis/scripts", cls.install_root / "skill/scripts",
                        ignore=shutil.ignore_patterns("__pycache__"))
        for layout in ("editable", "installed"):
            target = cls.install_root / layout
            target.mkdir()
            # Both installs use the same source, but each gets its own site dir.
            (target / "source").symlink_to(source, target_is_directory=True)
            (target / "skill").symlink_to(cls.install_root / "skill", target_is_directory=True)
            command = [sys.executable, "-m", "pip", "install", "--no-deps", "--no-build-isolation",
                       "--no-index", "--disable-pip-version-check", "--target", str(target / "site")]
            if layout == "editable":
                command.append("--editable")
            result = subprocess.run(command + [str(source)], cwd=cls.temp.name,
                                    capture_output=True, text=True, timeout=120)
            if result.returncode:
                raise AssertionError(result.stdout + result.stderr)

    def _matrix(self, layout, first, packages=("marketdata", "chantheory", "fundamentalscreener")):
        root = self.install_root / layout if layout in {"editable", "installed"} else ROOT
        for package in packages:
            with self.subTest(package=package):
                result = subprocess.run(
                    [sys.executable, "-I", "-S", str(PROBE), layout, str(root), first, package],
                    cwd=self.temp.name, capture_output=True, text=True, timeout=60,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_root_short_first(self):
        self._matrix("root", "short")

    def test_root_canonical_first(self):
        self._matrix("root", "canonical")

    def test_packages_first_short_first(self):
        self._matrix("packages-first", "short")

    def test_packages_first_canonical_first(self):
        self._matrix("packages-first", "canonical")

    def test_packages_only_short_first(self):
        self._matrix("packages-only", "short")

    def test_editable_short_first(self):
        self._matrix("editable", "short")

    def test_editable_canonical_first(self):
        self._matrix("editable", "canonical")

    def test_installed_short_first(self):
        self._matrix("installed", "short")

    def test_installed_canonical_first(self):
        self._matrix("installed", "canonical")

    def test_editable_wrappers_first(self):
        self._matrix("editable", "wrapper", packages=("marketdata",))

    def test_installed_wrappers_first(self):
        self._matrix("installed", "wrapper", packages=("marketdata",))


if __name__ == "__main__":
    unittest.main()
