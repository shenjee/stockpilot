"""临时诊断脚本：运行 002149.sz 5分钟K线分析，输出线段信息"""
import sys
import json
from pathlib import Path

ROOT = Path("/Users/jishen/development/stockpilot")
sys.path.insert(0, str(ROOT / "packages"))
sys.path.insert(0, str(ROOT / "skills" / "china-stock-analysis" / "scripts"))

from market_data import TencentStockDataProvider
from repositories.kline_store import KLineStore, resolve_market_data_db_path
from runtime_paths import RuntimePaths
from services.kline_data_service import KLineDataService
from chantheory import analyze_tracker_klines, get_default_max_bi_num

paths = RuntimePaths()
db_path = resolve_market_data_db_path(paths.db_dir)
service = KLineDataService(TencentStockDataProvider(), KLineStore(db_path))

rows = service.get_klines(
    code="002149", end_date="2026-06-19", market="sz",
    timeframe="5m", start_date="2026-04-01", limit=5000,
)
print(f"获取K线条数: {len(rows)}")

max_bi = 500
result = analyze_tracker_klines(
    rows=rows, code="002149", market="sz", timeframe="5m",
    parameters={"max_bi_num": max_bi},
)

strokes = result.strokes
segments = result.segments
print(f"\n笔总数: {len(strokes)}")
print(f"线段总数: {len(segments)}")

print("\n=== 所有线段 ===")
for i, seg in enumerate(segments):
    status = seg.meta.get("status", "?")
    confirmed = seg.confirmed
    start_ts = seg.start_timestamp
    end_ts = seg.end_timestamp
    start_price = seg.start_price
    end_price = seg.end_price
    feat_break = seg.meta.get("feature_sequence_break")
    dropped = seg.meta.get("dropped_segments_summary")

    print(f"\n[线段{i}] direction={seg.direction} status={status} confirmed={confirmed}")
    print(f"  start: {start_ts} @ {start_price}")
    print(f"  end:   {end_ts} @ {end_price}")
    print(f"  strokes: {seg.meta.get('start_stroke_index', '?')}~{seg.meta.get('end_stroke_index', '?')}")
    print(f"  stroke_count: {seg.meta.get('stroke_count', '?')}")
    if feat_break:
        print(f"  feature_break: {feat_break}")
    if dropped:
        print(f"  dropped_segments: {json.dumps(dropped, ensure_ascii=False, indent=4)}")

# 找到 04-27 14:25 附近的线段
print("\n\n=== 04-27 14:25 附近的线段 ===")
target = "2026-04-27 14:25"
for i, seg in enumerate(segments):
    if seg.start_timestamp >= "2026-04-27" or seg.end_timestamp >= "2026-04-27":
        print(f"\n[线段{i}] direction={seg.direction} status={seg.meta.get('status')} confirmed={seg.confirmed}")
        print(f"  start: {seg.start_timestamp} @ {seg.start_price}")
        print(f"  end:   {seg.end_timestamp} @ {seg.end_price}")
        print(f"  strokes: {seg.meta.get('start_stroke_index', '?')}~{seg.meta.get('end_stroke_index', '?')}")
        feat_break = seg.meta.get("feature_sequence_break")
        if feat_break:
            print(f"  feature_break: {feat_break}")

# 找到 04-27 14:25 附近的笔
print("\n\n=== 04-27 14:25 之后的笔 ===")
for i, s in enumerate(strokes):
    if s.start_timestamp >= target:
        print(f"  笔{i} (UI编号{i+1}): dir={s.direction} {s.start_timestamp}@{s.start_price} -> {s.end_timestamp}@{s.end_price}")
        if i > 130:
            break
