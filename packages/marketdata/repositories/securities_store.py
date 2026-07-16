"""证券主数据仓储（securities master）。

在现有 K 线 SQLite 库（``market_data.sqlite``）里加一张 ``securities`` 表，存放
A 股股票 / 指数 / ETF 的 code / market / type / name / pinyin，供前端按代码、
名称或拼音首字母搜索后下拉选择。

设计要点：
- 纯标准库（``json`` / ``sqlite3`` / ``contextlib`` / ``pathlib``），与
  ``kline_store.py`` 的连接、建表、读写风格保持一致，便于审阅。
- 主数据由构建期脚本算好并固化进 ``securities_master.json``，运行时只需导入；
  App 运行时不依赖 akshare / pypinyin。
- 每次构造时，都会把随仓库分发的 JSON 通过 upsert 同步到本地表（``ensure_loaded``）。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional

# 随仓库分发的主数据 JSON，默认放在共享 marketdata 包目录下。
_BUNDLED_JSON = Path(__file__).resolve().parent.parent / "securities_master.json"


class SecuritiesStore:
    """证券主数据表读写。与 :class:`KLineStore` 共用同一个 SQLite 文件。"""

    def __init__(self, db_path: Path | str, json_path: Path | str | None = None):
        self.db_path = Path(db_path)
        self.json_path = Path(json_path) if json_path else _BUNDLED_JSON
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        self.ensure_loaded()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS securities (
                    code TEXT NOT NULL,
                    market TEXT NOT NULL,
                    type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    pinyin TEXT NOT NULL,
                    PRIMARY KEY (code, market)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_securities_code ON securities(code)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_securities_pinyin ON securities(pinyin)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_securities_name ON securities(name)")

    # ------------------------------------------------------------------
    # 导入 / 写入
    # ------------------------------------------------------------------

    def ensure_loaded(self) -> None:
        """将随仓库分发的 JSON 主数据 upsert 同步到本地表。

        这样已存在 A 股记录的老数据库在升级后，也能补齐新增证券（如港股）；
        若 JSON 缺失且表为空，则打印告警而不是静默吞掉——否则前端搜索会一直为空。
        """

        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM securities").fetchone()
        if self.json_path.exists():
            self.import_json(self.json_path)
            return
        if row and row[0] > 0:
            return
        print(
            f"[WARN] 证券主数据 JSON 不存在：{self.json_path}；securities 表为空，"
            "前端搜索将无任何结果。请运行 build_securities_master.py 生成该文件"
            "并放置到上述路径。",
            file=sys.stderr,
        )

    def import_json(self, json_path: Path | str) -> int:
        """读取 JSON 文件并 upsert 进库，返回写入条数。"""

        with open(json_path, encoding="utf-8") as f:
            records = json.load(f)
        return self.upsert_many(records)

    def upsert_many(self, securities: List[Dict[str, object]]) -> int:
        """批量 upsert 证券记录，返回写入条数。"""

        rows = [
            (
                str(r["code"]),
                str(r["market"]),
                str(r["type"]),
                str(r["name"]),
                str(r["pinyin"]),
            )
            for r in securities
        ]
        if not rows:
            return 0
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO securities (code, market, type, name, pinyin)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(code, market) DO UPDATE SET
                    type = excluded.type,
                    name = excluded.name,
                    pinyin = excluded.pinyin
                """,
                rows,
            )
        return len(rows)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def search(self, query: str, limit: int = 50) -> List[Dict[str, object]]:
        """按 code / 名称 / 拼音首字母搜索，返回匹配记录列表。

        - 空白查询返回 ``[]``。
        - code / pinyin 走精确或前缀匹配（大小写不敏感，pinyin 存大写）。
        - name 走子串匹配。
        - 精确匹配优先，其次前缀，最后子串，再按 code 排序。
        """

        q = (query or "").strip()
        if not q:
            return []
        qu = q.upper()
        with self._connect() as conn:
            cur = conn.execute(
                """
                SELECT code, market, type, name, pinyin FROM securities
                WHERE code = ? OR code LIKE ? ESCAPE '\\'
                   OR pinyin = ? OR pinyin LIKE ? ESCAPE '\\'
                   OR name LIKE ? ESCAPE '\\'
                ORDER BY
                    CASE
                        WHEN code = ? THEN 0
                        WHEN pinyin = ? THEN 1
                        WHEN code LIKE ? ESCAPE '\\' THEN 2
                        WHEN pinyin LIKE ? ESCAPE '\\' THEN 3
                        ELSE 4
                    END,
                    code,
                    market
                LIMIT ?
                """,
                (
                    q, _like_prefix(q),
                    qu, _like_prefix(qu),
                    _like_contains(q),
                    q, qu, _like_prefix(q), _like_prefix(qu),
                    limit,
                ),
            )
            rows = cur.fetchall()
        return [
            {
                "code": r[0],
                "market": r[1],
                "type": r[2],
                "name": r[3],
                "pinyin": r[4],
            }
            for r in rows
        ]

    def get(self, code: str, market: Optional[str] = None) -> Optional[Dict[str, object]]:
        """按 code（可选 market）取单条记录，无则返回 ``None``。"""

        with self._connect() as conn:
            if market is not None:
                row = conn.execute(
                    "SELECT code, market, type, name, pinyin FROM securities WHERE code = ? AND market = ?",
                    (code, market),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT code, market, type, name, pinyin FROM securities WHERE code = ? ORDER BY market LIMIT 1",
                    (code,),
                ).fetchone()
        if not row:
            return None
        return {
            "code": row[0],
            "market": row[1],
            "type": row[2],
            "name": row[3],
            "pinyin": row[4],
        }


def _like_prefix(value: str) -> str:
    """构造 ``value%`` 的 LIKE 串，转义 ``%``/``_``/``\\``。"""

    return _escape_like(value) + "%"


def _like_contains(value: str) -> str:
    """构造 ``%value%`` 的 LIKE 串，转义 ``%``/``_``/``\\``。"""

    return "%" + _escape_like(value) + "%"


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
