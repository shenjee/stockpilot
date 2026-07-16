#!/usr/bin/env python3
"""生成证券主数据 JSON（securities_master.json）。

一次性、幂等的生成脚本，产物提交进仓库供运行时导入到 SQLite。
覆盖 A 股股票 / 指数 / ETF，以及维护中的港股静态主数据；每条记录包含 code / market / type / name / pinyin
（pinyin 为名称拼音首字母大写，如 平安银行 -> PAYH），便于前端按代码 / 名称 /
拼音首字母搜索。未来扩展 基金 / 可转债 时，往本脚本加新的 type 段即可。

akshare 与 pypinyin 只在本脚本内使用，且采用懒导入：本模块可在没有这两个
依赖的环境里被 import（例如只为了拿常量），只有在真正执行 `_build()` 时才需要。
运行时 App 不依赖 akshare / pypinyin —— 主数据已在构建期算好并固化进 JSON。

共享运行时默认从 ``packages/marketdata/securities_master.json`` 读取；为保持
兼容，本脚本也会同步更新 ``skills/china-stock-analysis/scripts/`` 下的旧路径。
两份 JSON 都应由本脚本统一输出，避免共享版与 standalone 产物漂移。

执行::

    python skills/china-stock-analysis/scripts/build_securities_master.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 让脚本既能直接运行（相对导入 market_data）也能被 -m 调用。
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from market_data import INDICES, get_market_prefix  # noqa: E402

_ROOT = _SCRIPTS_DIR.parents[2]
OUTPUT_PATH = _ROOT / "packages" / "marketdata" / "securities_master.json"
LEGACY_OUTPUT_PATH = _SCRIPTS_DIR / "securities_master.json"

HK_STATIC_STOCKS = [
    ("00005", "汇丰控股", "HFKG"),
    ("00020", "商汤", "ST"),
    ("00175", "吉利汽车", "JLQC"),
    ("00241", "阿里健康", "ALJK"),
    ("00386", "中国石化", "ZGSH"),
    ("00388", "香港交易所", "XGJYS"),
    ("00700", "腾讯控股", "TXKG"),
    ("00883", "中海油", "ZHY"),
    ("00939", "建设银行", "JSYH"),
    ("00941", "中国移动", "ZGYD"),
    ("01024", "快手", "KS"),
    ("01088", "中国神华", "ZGSH2"),
    ("01288", "农业银行", "NYYH"),
    ("01299", "友邦保险", "YBBX"),
    ("01398", "工商银行", "GSYH"),
    ("01810", "小米集团", "XMJT"),
    ("01833", "平安好医生", "PAHYS"),
    ("02196", "复星医药", "FXYY"),
    ("02238", "理想汽车", "LXQC"),
    ("02318", "中国平安", "ZGPA"),
    ("02382", "舜宇光学科技", "SYGXKJ"),
    ("02601", "中国太保", "ZGTB"),
    ("02628", "中国人寿", "ZGRS"),
    ("03690", "美团", "MT"),
    ("03896", "金山云", "JSY"),
    ("03968", "招商银行", "ZSYH"),
    ("06060", "众安在线", "ZAZX"),
    ("06618", "京东健康", "JDJK"),
    ("06862", "海底捞", "HDL"),
    ("09618", "京东集团", "JDJT"),
    ("09633", "农夫山泉", "NFSQ"),
    ("09866", "蔚来", "WL"),
    ("09869", "小鹏汽车", "XPQC"),
    ("09888", "百度集团", "BDJT"),
    ("09988", "阿里巴巴", "ALBB"),
]


def _pinyin_initials(name: str) -> str:
    """名称 -> 拼音首字母大写。非汉字字符（数字/字母/符号）原样保留并大写。

    pypinyin 默认把多音字「长」解析为 zhǎng（Z），但在证券名称里「长」几乎都是
    cháng（如 长飞光纤、长江、长城、长三角）。这里把「长」的单字默认改为 cháng；
    已知词组（成长、班长 等）仍由 PHRASES_DICT 优先正确解析为 zhǎng，不受影响。
    """

    from pypinyin import Style, lazy_pinyin, load_single_dict

    load_single_dict({ord("长"): "cháng"})
    parts = lazy_pinyin(name, style=Style.FIRST_LETTER)
    return "".join(p.upper() for p in parts if p)


def _record(code: str, market: str, sec_type: str, name: str) -> dict:
    code = str(code).strip().zfill(6)
    name = str(name).strip()
    return {
        "code": code,
        "market": market,
        "type": sec_type,
        "name": name,
        "pinyin": _pinyin_initials(name),
    }


def _collect_stocks(records: list[dict]) -> None:
    import akshare as ak

    # ak.stock_info_a_code_name() 返回 code/name 两列，覆盖沪深京全市场 A 股。
    df = ak.stock_info_a_code_name()
    for _, row in df.iterrows():
        code = str(row["code"]).strip()
        records.append(_record(code, get_market_prefix(code), "stock", row["name"]))


def _collect_etfs(records: list[dict]) -> None:
    import akshare as ak

    # ak.fund_etf_spot_em() 返回中文列名（代码 / 名称 / ...）。
    df = ak.fund_etf_spot_em()
    for _, row in df.iterrows():
        code = str(row["代码"]).strip()
        records.append(_record(code, get_market_prefix(code), "etf", row["名称"]))


def _collect_indices(records: list[dict]) -> None:
    # 指数的 market 必须取自 INDICES *键* 的前缀（如 "sh000001" -> "sh"），不能用
    # get_market_prefix(code)：get_market_prefix("000001") 会误判成 "sz"，而
    # 000001 在 sh 是上证指数。INDICES 值里的 exchange（大写）只是展示标签。
    for sym, (name, exchange) in INDICES.items():
        prefix = sym[:2].lower()
        if prefix != exchange.lower():
            # 标记 INDICES 里 key 前缀与 value 交易所不一致的情况（如 sz899050/北证50）。
            # 这是既有的 INDICES 数据问题，与本特性正交，单独修；这里只用 key 前缀，
            # 因为它才是腾讯行情接口的真实符号前缀。
            print(
                f"[WARN] INDICES 前缀与交易所不一致: {sym} prefix={prefix} "
                f"exchange={exchange}，按 key 前缀 {prefix} 取用。",
                file=sys.stderr,
            )
        records.append(_record(sym[2:], prefix, "index", name))


def _collect_hk_stocks(records: list[dict]) -> None:
    """收集维护中的港股静态主数据。

    当前港股不走 akshare 动态抓取，避免生成流程受外部源波动影响；
    这里维护一份明确的静态清单，供共享版与 standalone 一起产出。
    """

    for code, name, pinyin in HK_STATIC_STOCKS:
        records.append(
            {
                "code": code,
                "market": "hk",
                "type": "stock",
                "name": name,
                "pinyin": pinyin,
            }
        )


def _build() -> None:
    records: list[dict] = []
    _collect_stocks(records)
    _collect_etfs(records)
    _collect_indices(records)
    _collect_hk_stocks(records)

    # 按 (code, market) 去重，再按 (type, code) 排序，保证 JSON diff 稳定。
    seen: set[tuple[str, str]] = set()
    unique: list[dict] = []
    for r in records:
        key = (r["code"], r["market"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)
    unique.sort(key=lambda r: (r["type"], r["code"], r["market"]))

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    for path in (OUTPUT_PATH, LEGACY_OUTPUT_PATH):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(unique, f, ensure_ascii=False, indent=2)

    by_type: dict[str, int] = {}
    for r in unique:
        by_type[r["type"]] = by_type.get(r["type"], 0) + 1
    print(
        f"写入 {OUTPUT_PATH} 和 {LEGACY_OUTPUT_PATH}：共 {len(unique)} 条；"
        + "；".join(f"{t} {c}" for t, c in sorted(by_type.items()))
    )


if __name__ == "__main__":
    _build()
