"""Phase 6A: lineage 与 quality 模块的单元测试。"""

from __future__ import annotations

import unittest

from packages.fundamentalscreener.lineage import (
    DEFAULT_CONFIG_VERSION,
    DEFAULT_FORMULA_VERSION,
    SnapshotMetadata,
    SourceSet,
    new_fetch_run_id,
    new_quality_report_id,
    new_snapshot_id,
)
from packages.fundamentalscreener.quality import (
    LEVEL_ERROR,
    LEVEL_INFO,
    LEVEL_WARNING,
    QualityIssue,
    QualityReport,
)


class LineageIdTests(unittest.TestCase):
    def test_ids_have_expected_prefix(self) -> None:
        self.assertTrue(new_fetch_run_id().startswith("fetch-"))
        self.assertTrue(new_snapshot_id().startswith("snapshot-"))
        self.assertTrue(new_quality_report_id().startswith("quality-"))

    def test_ids_are_unique(self) -> None:
        seen = {new_fetch_run_id() for _ in range(20)}
        self.assertEqual(len(seen), 20)
        seen = {new_snapshot_id() for _ in range(20)}
        self.assertEqual(len(seen), 20)


class SourceSetTests(unittest.TestCase):
    def test_with_role_returns_new_instance(self) -> None:
        s = SourceSet()
        s2 = s.with_role("sector", "akshare_em")
        self.assertEqual(s.to_dict(), {})
        self.assertEqual(s2.to_dict(), {"sector": "akshare_em"})

    def test_to_dict_is_sorted(self) -> None:
        s = SourceSet()
        s.update("quote", "tencent")
        s.update("sector", "akshare_em")
        self.assertEqual(
            list(s.to_dict().keys()),
            ["quote", "sector"],
        )


class SnapshotMetadataTests(unittest.TestCase):
    def test_create_fills_defaults(self) -> None:
        meta = SnapshotMetadata.create(
            analysis_date="2026-06-19",
            source_set={"sector": "akshare_em"},
        )
        self.assertEqual(meta.analysis_date, "2026-06-19")
        self.assertEqual(meta.data_cutoff, "2026-06-19")
        self.assertEqual(meta.config_version, DEFAULT_CONFIG_VERSION)
        self.assertEqual(meta.formula_version, DEFAULT_FORMULA_VERSION)
        self.assertTrue(meta.snapshot_id.startswith("snapshot-"))
        self.assertTrue(meta.fetch_run_id.startswith("fetch-"))
        self.assertTrue(meta.quality_report_id.startswith("quality-"))
        self.assertEqual(meta.source_set.to_dict(), {"sector": "akshare_em"})

    def test_create_accepts_explicit_fetch_run_id(self) -> None:
        meta = SnapshotMetadata.create(
            analysis_date="2026-06-19",
            fetch_run_id="fetch-existing",
            quality_report_id="quality-existing",
        )
        self.assertEqual(meta.fetch_run_id, "fetch-existing")
        self.assertEqual(meta.quality_report_id, "quality-existing")

    def test_to_dict_contains_required_fields(self) -> None:
        meta = SnapshotMetadata.create(
            analysis_date="2026-06-19",
            source_set={"sector": "akshare_em", "quote": "tencent"},
        )
        d = meta.to_dict()
        for key in (
            "snapshot_id",
            "analysis_date",
            "data_cutoff",
            "data_quality_status",
            "source_set",
            "fetch_run_id",
            "quality_report_id",
            "config_version",
            "formula_version",
            "generated_at",
        ):
            self.assertIn(key, d)
        self.assertEqual(d["data_quality_status"], "ok")
        self.assertIsInstance(d["source_set"], dict)


class QualityReportTests(unittest.TestCase):
    def test_empty_report_status_is_ok(self) -> None:
        report = QualityReport()
        self.assertEqual(report.status, "ok")
        self.assertEqual(report.counts, {"error": 0, "warning": 0, "info": 0})

    def test_warning_only_status_is_degraded(self) -> None:
        report = QualityReport()
        report.add_issue("c", LEVEL_WARNING, "msg")
        self.assertEqual(report.status, "degraded")

    def test_error_status_is_invalid(self) -> None:
        report = QualityReport()
        report.add_issue("c", LEVEL_ERROR, "msg")
        self.assertEqual(report.status, "invalid")

    def test_stale_flag_sets_stale_when_no_issues(self) -> None:
        report = QualityReport(stale=True)
        self.assertEqual(report.status, "stale")

    def test_error_overrides_stale(self) -> None:
        report = QualityReport(stale=True)
        report.add_issue("c", LEVEL_ERROR, "msg")
        self.assertEqual(report.status, "invalid")

    def test_info_does_not_change_status(self) -> None:
        report = QualityReport()
        report.add_issue("c", LEVEL_INFO, "msg")
        self.assertEqual(report.status, "ok")

    def test_invalid_level_raises(self) -> None:
        with self.assertRaises(ValueError):
            QualityIssue(code="x", level="oops", message="")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
