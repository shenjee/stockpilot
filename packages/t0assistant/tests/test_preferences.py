from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import sqlite3
import tempfile
from threading import Barrier
from pathlib import Path
import unittest

from packages.t0assistant.preferences import (
    LayerPreference,
    LayoutPreference,
    PreferenceService,
    PreferenceValidationError,
    PreferenceValues,
    PreferencesReadOnlyError,
)
from packages.t0assistant.repositories import (
    AppDatabaseCompatibilityError,
    SqlitePreferenceRepository,
    open_app_database,
)


class PreferenceValueTests(unittest.TestCase):
    def test_first_run_defaults_match_product_and_transport_contract(self) -> None:
        self.assertEqual(
            PreferenceValues().to_dict(),
            {
                "last_symbol": None,
                "layout": {
                    "chart_split": "64_36",
                    "show_intraday": True,
                },
                "layers": {
                    "ma5": False,
                    "ma10": False,
                    "ma20": False,
                    "ma30": False,
                    "ma60": False,
                    "strokes": True,
                    "pivot_zones": True,
                },
            },
        )

    def test_mapping_round_trip_matches_app_v1_shape(self) -> None:
        payload = {
            "last_symbol": "sh.600584",
            "layout": {"chart_split": "50_50", "show_intraday": False},
            "layers": {
                "ma5": True,
                "ma10": False,
                "ma20": True,
                "ma30": False,
                "ma60": True,
                "strokes": False,
                "pivot_zones": True,
            },
        }
        self.assertEqual(PreferenceValues.from_mapping(payload).to_dict(), payload)

    def test_invalid_symbol_layout_layer_and_extra_key_are_rejected(self) -> None:
        cases = (
            (
                {
                    **PreferenceValues().to_dict(),
                    "last_symbol": "600584",
                },
                "last_symbol",
            ),
            (
                {
                    **PreferenceValues().to_dict(),
                    "layout": {"chart_split": "70_30", "show_intraday": True},
                },
                "layout.chart_split",
            ),
            (
                {
                    **PreferenceValues().to_dict(),
                    "layers": {
                        **PreferenceValues().layers.to_dict(),
                        "ma5": 1,
                    },
                },
                "layers.ma5",
            ),
            (
                {
                    **PreferenceValues().to_dict(),
                    "layout": {
                        **PreferenceValues().layout.to_dict(),
                        "unexpected": True,
                    },
                },
                "layout",
            ),
            (
                {
                    **PreferenceValues().to_dict(),
                    "layers": {
                        **PreferenceValues().layers.to_dict(),
                        "unexpected": True,
                    },
                },
                "layers",
            ),
            (
                {
                    **PreferenceValues().to_dict(),
                    "unknown": True,
                },
                "preferences",
            ),
        )
        for payload, field in cases:
            with self.subTest(field=field), self.assertRaises(
                PreferenceValidationError
            ) as ctx:
                PreferenceValues.from_mapping(payload)
            self.assertEqual(ctx.exception.field, field)


class PreferencePersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tempdir.name) / "t0-assistant.sqlite3"

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    @staticmethod
    def _changed_values() -> PreferenceValues:
        return PreferenceValues(
            last_symbol="sh.600584",
            layout=LayoutPreference(chart_split="50_50", show_intraday=False),
            layers=LayerPreference(
                ma5=True,
                ma10=True,
                ma20=False,
                ma30=False,
                ma60=True,
                strokes=False,
                pivot_zones=True,
            ),
        )

    def test_init_is_idempotent_and_startup_restores_confirmed_copy(self) -> None:
        with open_app_database(self.db_path) as database:
            service = PreferenceService(SqlitePreferenceRepository(database))
            initial = service.restore_for_startup()
            self.assertEqual(initial.snapshot.preference_revision, 0)
            self.assertTrue(initial.capability.writable)

            saved = service.save(self._changed_values())
            self.assertEqual(saved.preference_revision, 1)

        with open_app_database(self.db_path) as reopened:
            restored = PreferenceService(
                SqlitePreferenceRepository(reopened)
            ).restore_for_startup()
            self.assertEqual(restored.snapshot, saved)

    def test_idempotent_save_does_not_inflate_revision(self) -> None:
        with open_app_database(self.db_path) as database:
            service = PreferenceService(SqlitePreferenceRepository(database))
            first = service.save(self._changed_values())
            second = service.save(self._changed_values())
            self.assertEqual(first, second)
            self.assertEqual(second.preference_revision, 1)

    def test_concurrent_identical_saves_return_current_snapshot(self) -> None:
        with open_app_database(self.db_path) as database:
            repository = SqlitePreferenceRepository(database)
            worker_count = 8
            start = Barrier(worker_count)

            def save_from_worker():
                start.wait()
                return repository.save(self._changed_values())

            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                snapshots = tuple(
                    executor.map(lambda _index: save_from_worker(), range(worker_count))
                )

            self.assertEqual(
                snapshots,
                (snapshots[0],) * worker_count,
            )
            self.assertEqual(snapshots[0].preference_revision, 1)
            self.assertEqual(repository.load(), snapshots[0])

    def test_concurrent_loads_and_saves_share_one_connection_safely(self) -> None:
        with open_app_database(self.db_path) as database:
            repositories = (
                SqlitePreferenceRepository(database),
                SqlitePreferenceRepository(database),
            )
            changed = self._changed_values()
            values = (PreferenceValues(), changed)

            def exercise_repository(index: int) -> None:
                repository = repositories[index % len(repositories)]
                for offset in range(20):
                    if (index + offset) % 2:
                        repository.save(values[(index + offset) % len(values)])
                    else:
                        repository.load()

            with ThreadPoolExecutor(max_workers=6) as executor:
                tuple(executor.map(exercise_repository, range(6)))

            snapshot = repositories[0].load()
            self.assertIn(snapshot.preferences, values)
            self.assertGreaterEqual(snapshot.preference_revision, 1)

    def test_read_only_startup_restores_but_save_is_explicitly_rejected(self) -> None:
        with open_app_database(self.db_path) as writable:
            expected = PreferenceService(
                SqlitePreferenceRepository(writable)
            ).save(self._changed_values())

        with open_app_database(self.db_path, force_read_only=True) as read_only:
            service = PreferenceService(SqlitePreferenceRepository(read_only))
            restored = service.restore_for_startup()
            self.assertEqual(restored.snapshot, expected)
            self.assertTrue(restored.capability.readable)
            self.assertFalse(restored.capability.writable)
            self.assertIn("不能保存", restored.capability.reason or "")

            with self.assertRaises(PreferencesReadOnlyError):
                service.save(PreferenceValues())
            self.assertEqual(service.restore_for_startup().snapshot, expected)

    def test_failed_transaction_does_not_advance_revision_or_change_values(self) -> None:
        with open_app_database(self.db_path) as database:
            service = PreferenceService(SqlitePreferenceRepository(database))
            before = service.restore_for_startup().snapshot
            database.connection.execute(
                """
                CREATE TRIGGER reject_preference_update
                BEFORE UPDATE ON preferences
                BEGIN
                    SELECT RAISE(ABORT, 'injected write failure');
                END
                """
            )
            with self.assertRaisesRegex(RuntimeError, "未确认持久化"):
                service.save(self._changed_values())
            self.assertEqual(service.restore_for_startup().snapshot, before)

    def test_incompatible_existing_database_is_not_cleared(self) -> None:
        connection = sqlite3.connect(self.db_path)
        connection.execute("CREATE TABLE preferences(singleton_id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO preferences VALUES (1)")
        connection.commit()
        connection.close()

        with self.assertRaises(AppDatabaseCompatibilityError):
            open_app_database(self.db_path)

        connection = sqlite3.connect(self.db_path)
        tables = tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )
        )
        self.assertEqual(tables, ("preferences",))
        self.assertEqual(
            connection.execute("SELECT singleton_id FROM preferences").fetchone()[0],
            1,
        )
        connection.close()


if __name__ == "__main__":
    unittest.main()
