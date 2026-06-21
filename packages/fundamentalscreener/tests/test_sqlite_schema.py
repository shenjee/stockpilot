"""Phase 6A: SQLite schema 与 init_db 测试。"""

from __future__ import annotations

import unittest

from packages.fundamentalscreener.sqlite_schema import (
    TABLE_NAMES,
    connect,
    init_db,
    list_tables,
    required_lineage_columns,
    table_columns,
)


class InitDbTests(unittest.TestCase):
    def test_init_db_creates_all_required_tables(self) -> None:
        conn = connect(":memory:")
        try:
            init_db(conn)
            tables = set(list_tables(conn))
            for name in TABLE_NAMES:
                self.assertIn(name, tables, f"missing table: {name}")
        finally:
            conn.close()

    def test_init_db_is_idempotent(self) -> None:
        conn = connect(":memory:")
        try:
            init_db(conn)
            init_db(conn)  # 第二次调用不应失败。
            init_db(conn)  # 第三次也不行。
            tables = set(list_tables(conn))
            for name in TABLE_NAMES:
                self.assertIn(name, tables)
        finally:
            conn.close()

    def test_collection_tables_have_lineage_columns(self) -> None:
        conn = connect(":memory:")
        try:
            init_db(conn)
            collection_tables = [t for t in TABLE_NAMES if t != "data_fetch_log"]
            for table in collection_tables:
                cols = set(table_columns(conn, table))
                for required in required_lineage_columns(table):
                    self.assertIn(
                        required,
                        cols,
                        f"table {table} missing lineage column {required!r}",
                    )
        finally:
            conn.close()

    def test_data_fetch_log_columns(self) -> None:
        conn = connect(":memory:")
        try:
            init_db(conn)
            cols = set(table_columns(conn, "data_fetch_log"))
            for required in required_lineage_columns("data_fetch_log"):
                self.assertIn(required, cols)
            # success / row_count / used_cache 也必须存在。
            for required in ("success", "row_count", "used_cache", "error"):
                self.assertIn(required, cols)
        finally:
            conn.close()

    def test_init_db_preserves_existing_data(self) -> None:
        # 验证幂等：第二次 init_db 不应清空已有行。
        conn = connect(":memory:")
        try:
            init_db(conn)
            conn.execute(
                "INSERT INTO sectors "
                "(sector_id, classification_system, sector_name, source, "
                "fetch_run_id, source_updated_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "BK0001",
                    "em_industry",
                    "示例板块",
                    "fake",
                    "fetch-x",
                    None,
                    "2026-06-19T00:00:00+08:00",
                    "2026-06-19T00:00:00+08:00",
                ),
            )
            conn.commit()

            init_db(conn)  # 再次幂等

            cur = conn.execute("SELECT COUNT(*) FROM sectors")
            self.assertEqual(cur.fetchone()[0], 1)
        finally:
            conn.close()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
