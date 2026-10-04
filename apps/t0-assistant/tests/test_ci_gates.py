"""Regression checks for required discovery and the T+0 workflow trigger surface."""

import fnmatch
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
RUNNER = Path(__file__).with_name("run_required_python.py")


class RequiredDiscoveryTests(unittest.TestCase):
    def run_target(self, source, pattern="test_*.py"):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "test_sample.py").write_text(source)
            return subprocess.run(
                [sys.executable, str(RUNNER), directory, pattern],
                capture_output=True, text=True, cwd=ROOT,
            )

    def test_real_test_is_counted_and_run(self):
        result = self.run_target("import unittest\nclass Sample(unittest.TestCase):\n def test_ok(self): pass\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Discovered tests: 1", result.stdout)
        self.assertIn("run=1", result.stdout)

    def test_missing_pattern_and_empty_module_fail(self):
        for source, pattern in [("import unittest\n", "test_missing.py"), ("", "test_*.py")]:
            with self.subTest(pattern=pattern):
                result = self.run_target(source, pattern)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Discovered tests: 0", result.stdout)

    def test_test_failure_import_error_and_all_skipped_fail(self):
        sources = [
            "import unittest\nclass Sample(unittest.TestCase):\n def test_bad(self): self.fail('sentinel')\n",
            "raise ImportError('sentinel')\n",
            "import unittest\n@unittest.skip('sentinel')\nclass Sample(unittest.TestCase):\n def test_skip(self): pass\n",
        ]
        for source in sources:
            with self.subTest(source=source):
                self.assertNotEqual(self.run_target(source).returncode, 0)


class WorkflowCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = yaml.load(
            (ROOT / ".github/workflows/t0-assistant.yml").read_text(), Loader=yaml.BaseLoader,
        )

    def test_each_required_surface_triggers_pr_and_main_push(self):
        changed_files = [
            "packages/marketdata/example.py", "packages/t0assistant/example.py",
            "packages/chantheory/example.py", "packages/indicators/example.py",
            "packages/t0assistant/tests/test_example.py", "packages/__init__.py",
            "packages/_import_aliases.py",
            "marketdata/__init__.py", "chantheory/__init__.py",
            "apps/t0-assistant/contracts/logical-schema.json",
            "packages/t0assistant/contracts/example.json",
            "apps/t0-assistant/package-lock.json", "apps/t0-assistant/vitest.config.ts",
            "apps/t0-assistant/tests/run_required_python.py",
            "pyproject.toml", ".github/workflows/t0-assistant.yml",
            "spikes/0008-czsc-update-and-rebuild-strategy/run_spike.py",
            "spikes/0009-czsc-1.0.1-upgrade/baseline/inputs/example.json",
        ]
        for event in ("pull_request", "push"):
            patterns = self.workflow["on"][event]["paths"]
            for path in changed_files:
                with self.subTest(event=event, path=path):
                    self.assertTrue(any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns))
        self.assertEqual(self.workflow["on"]["push"]["branches"], ["main"])

    def test_required_jobs_and_python_targets_are_retained(self):
        jobs = self.workflow["jobs"]
        self.assertEqual(set(jobs), {"python-smoke", "renderer-smoke", "electron-smoke", "contract-smoke"})
        commands = [step.get("run", "") for job in jobs.values() for step in job["steps"]]
        for target in [
            "packages/marketdata/tests", "packages/t0assistant/tests",
            "packages/chantheory/tests", "packages/indicators/tests",
            "apps/t0-assistant/tests test_service.py",
            "apps/t0-assistant/tests test_ci_gates.py",
            "apps/t0-assistant/tests test_contracts.py",
        ]:
            self.assertIn(f"python apps/t0-assistant/tests/run_required_python.py {target}", commands)
        for command in ("npm run test:app", "npm run smoke:renderer", "npm run smoke:electron", "npm run test:contracts"):
            self.assertIn(command, commands)
        chart_step = next(
            step for step in jobs["renderer-smoke"]["steps"]
            if step.get("name") == "Run chart regression tests"
        )
        chart_command = shlex.split(chart_step["run"])
        self.assertEqual(chart_command[:3], ["node", "tests/run-required-tests.mjs", "node"])
        for target in (
            "tests/ci-gates.test.mjs", "tests/chart-model.test.mjs",
            "tests/chart-primitives-lc.test.mjs", "tests/chart-snapshot-smoke.test.mjs",
        ):
            self.assertIn(target, chart_command[3:])
        for job in jobs.values():
            self.assertNotIn("continue-on-error", job)
            for step in job["steps"]:
                self.assertNotIn("continue-on-error", step)


if __name__ == "__main__":
    unittest.main()
