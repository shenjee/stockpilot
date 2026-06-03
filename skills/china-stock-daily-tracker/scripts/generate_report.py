#!/usr/bin/env python3
"""
中国A股每日行情追踪 - 报告生成脚本
生成事实型行情报告，不做买卖建议
支持实时数据和历史数据
"""

import urllib.request
import urllib.error
import json
import os
import sys
import argparse
import math
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

# 配置路径
SKILL_DIR = Path(__file__).parent.parent
DEFAULT_LOOKBACK_DAYS = 140
LOCAL_CONFIG_NAMES = ("china-stock-daily-tracker.local.json", "china-stock-daily-tracker.json")

# 主要指数代码（腾讯财经格式）
INDICES = {
    "sh000001": ("上证指数", "SH"),
    "sz399001": ("深证成指", "SZ"),
    "sz399006": ("创业板指", "SZ"),
    "sh000016": ("上证50", "SH"),
    "sh000300": ("沪深300", "SH"),
    "sh000905": ("中证500", "SH"),
    "sh000852": ("中证1000", "SH"),
    "sh000688": ("科创50", "SH"),
    "sz899050": ("北证50", "BJ"),
}


class RuntimePaths:
    """运行期路径：skill目录和私有工作区分离。"""

    def __init__(self, config_file: str = None):
        config = self._load_runtime_config(config_file)
        root_value = os.environ.get("CHINA_STOCK_DAILY_TRACKER_WORKSPACE") or config.get("workspace")
        root = Path(root_value).expanduser().resolve() if root_value else Path.cwd().resolve()

        self.workspace = root
        self.config_dir = self._resolve_path(config.get("config_dir", "config"))
        self.report_dir = self._resolve_path(config.get("reports_dir", "reports"))
        self.db_dir = self._resolve_path(config.get("db_dir", "db"))
        self.strategy_dir = self._resolve_path(config.get("strategies_dir", "strategies"))

    def _load_runtime_config(self, config_file: str = None) -> dict:
        path = config_file or os.environ.get("CHINA_STOCK_DAILY_TRACKER_CONFIG")
        if path:
            return self._read_json_config(Path(path).expanduser())

        for name in LOCAL_CONFIG_NAMES:
            candidate = Path.cwd() / name
            if candidate.exists():
                return self._read_json_config(candidate)
        return {}

    def _read_json_config(self, path: Path) -> dict:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception as e:
            print(f"[WARN] 加载运行配置失败 {path}: {e}")
            return {}

    def _resolve_path(self, value: str) -> Path:
        path = Path(value).expanduser()
        return path.resolve() if path.is_absolute() else (self.workspace / path).resolve()

    def ensure_dirs(self):
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.db_dir.mkdir(parents=True, exist_ok=True)


class TXStockAPI:
    """腾讯财经股票数据接口 - 仅使用标准库"""
    
    TIMEOUT = 10
    MAX_RETRIES = 3
    
    @staticmethod
    def _get_prefix(code: str, market: str = None) -> str:
        """
        判断交易所前缀
        优先使用显式传入的 market 参数，否则按代码规则推断
        """
        if market:
            return market
        # 推断规则（备用）
        if code.startswith("6"):
            return "sh"
        elif code.startswith("0") or code.startswith("3"):
            return "sz"
        elif code.startswith("8") or code.startswith("4"):
            return "bj"
        return "sh"
    
    @classmethod
    def _fetch_with_retry(cls, url: str, decode: str = "gbk") -> str:
        """带重试机制的HTTP请求"""
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.0'
        })
        
        last_error = None
        for attempt in range(cls.MAX_RETRIES):
            try:
                with urllib.request.urlopen(req, timeout=cls.TIMEOUT) as resp:
                    return resp.read().decode(decode)
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
                last_error = e
                if attempt < cls.MAX_RETRIES - 1:
                    import time
                    time.sleep(1 * (attempt + 1))
                continue
        
        raise last_error or Exception("Request failed after retries")
    
    @classmethod
    def realtime(cls, codes, markets=None):
        """
        获取实时行情
        codes: 股票代码列表或 (code, market) 元组列表
        markets: 与codes对应的market列表（可选）
        """
        if isinstance(codes, str):
            codes = [codes]
        
        # 处理指数代码（已带前缀）和普通股票代码
        code_str_parts = []
        for i, c in enumerate(codes):
            if isinstance(c, tuple):
                # (code, market) 元组格式
                code, market = c
                code_str_parts.append(f"{market}{code}")
            elif c.startswith(("sh", "sz", "bj")):
                code_str_parts.append(c)
            else:
                # 使用显式market或推断
                market = markets[i] if markets else None
                code_str_parts.append(f"{cls._get_prefix(c, market)}{c}")
        
        url = f"https://qt.gtimg.cn/q={','.join(code_str_parts)}"
        
        try:
            data = cls._fetch_with_retry(url, decode="gbk")
        except Exception as e:
            print(f"[ERROR] 获取行情失败: {e}")
            return []
        
        results = []
        for line in data.strip().split(';'):
            if 'v_' not in line or '"' not in line:
                continue
            try:
                parts = line.split('"')[1].split('~')
                if len(parts) < 35:
                    continue
                
                # 计算涨跌幅
                price = float(parts[3])
                pre_close = float(parts[4])
                change = price - pre_close
                change_pct = (change / pre_close * 100) if pre_close > 0 else 0
                
                results.append({
                    'name': parts[1],
                    'code': parts[2],
                    'price': price,
                    'pre_close': pre_close,
                    'open': float(parts[5]),
                    'high': float(parts[33]),
                    'low': float(parts[34]),
                    'volume': int(parts[6]),  # 手
                    'amount': float(parts[37]) if len(parts) > 37 else 0,  # 万元
                    'change': round(change, 2),
                    'change_pct': round(change_pct, 2),
                })
            except Exception as e:
                continue
        
        return results[0] if len(results) == 1 and len(codes) == 1 else results
    
    @classmethod
    def get_kline(cls, code: str, start_date: str, end_date: str, ktype: str = 'day', autype: str = 'qfq', market: str = None) -> list:
        """
        获取历史K线数据
        
        参数:
            code: 股票代码，如 '600111'
            start_date: 开始日期，'2025-05-01'
            end_date: 结束日期，'2025-05-17'
            ktype: K线类型 - 'day'(日线), 'week'(周线), 'month'(月线)
            autype: 复权类型 - 'qfq'(前复权), 'hfq'(后复权), 'bfq'(不复权)
            market: 交易所前缀，如 'sh', 'sz', 'bj'（可选，默认自动推断）
        
        返回:
            [{'date': '2025-05-15', 'open': 24.48, 'close': 24.63, 'high': 25.22, 'low': 24.43, 'volume': 1233415}, ...]
        """
        prefix = cls._get_prefix(code, market)
        
        # 构建URL
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{code},{ktype},{start_date},{end_date},500,{autype}"
        
        try:
            data = json.loads(cls._fetch_with_retry(url, decode="utf-8"))
        except Exception as e:
            print(f"[ERROR] 获取K线数据失败 {code}: {e}")
            return []
        
        if data.get('code') != 0:
            return []
        
        # 提取K线数据
        key = f"{autype}day" if ktype == 'day' else f"{autype}{ktype}"
        klines = data['data'].get(f"{prefix}{code}", {}).get(key, [])
        
        results = []
        for item in klines:
            results.append({
                'date': item[0],
                'open': round(float(item[1]), 2),
                'close': round(float(item[2]), 2),
                'high': round(float(item[3]), 2),
                'low': round(float(item[4]), 2),
                'volume': int(float(item[5])),
            })
        
        return results
    
    @classmethod
    def get_daily_quote(cls, code: str, trade_date: str, autype: str = 'qfq', market: str = None) -> dict:
        """
        获取特定日期的行情数据
        trade_date: '2025-05-15'
        market: 交易所前缀，如 'sh', 'sz', 'bj'（可选）
        """
        df = cls.get_kline(code, trade_date, trade_date, ktype='day', autype=autype, market=market)
        if not df:
            return None
        
        data = df[0]
        # 计算涨跌幅（需要前一日收盘价）
        prev_date = (datetime.strptime(trade_date, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
        prev_data = cls.get_kline(code, prev_date, prev_date, ktype='day', autype=autype)
        
        if prev_data:
            pre_close = prev_data[0]['close']
            change = data['close'] - pre_close
            change_pct = (change / pre_close * 100) if pre_close > 0 else 0
        else:
            pre_close = data['open']  # 如果无法获取前一日，用开盘价代替
            change = data['close'] - pre_close
            change_pct = (change / pre_close * 100) if pre_close > 0 else 0
        
        return {
            'date': data['date'],
            'open': data['open'],
            'close': data['close'],
            'high': data['high'],
            'low': data['low'],
            'volume': data['volume'],
            'pre_close': pre_close,
            'change': round(change, 2),
            'change_pct': round(change_pct, 2),
        }


class KLineStore:
    """本地SQLite K线库"""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def _init_schema(self):
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_klines (
                    symbol TEXT NOT NULL,
                    code TEXT NOT NULL,
                    market TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    open REAL NOT NULL,
                    close REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    volume INTEGER NOT NULL,
                    source TEXT NOT NULL DEFAULT 'tencent',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (symbol, trade_date)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_daily_klines_date ON daily_klines(trade_date)")

    @staticmethod
    def symbol(code: str, market: str = None) -> str:
        prefix = market or TXStockAPI._get_prefix(code)
        return f"{prefix}{code}"

    def latest_date(self, code: str, market: str = None) -> str:
        symbol = self.symbol(code, market)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MAX(trade_date) FROM daily_klines WHERE symbol = ?",
                (symbol,),
            ).fetchone()
        return row[0] if row and row[0] else None

    def count_since(self, code: str, start_date: str, market: str = None) -> int:
        symbol = self.symbol(code, market)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM daily_klines WHERE symbol = ? AND trade_date >= ?",
                (symbol, start_date),
            ).fetchone()
        return int(row[0] or 0)

    def upsert_many(self, code: str, market: str, klines: list):
        if not klines:
            return
        symbol = self.symbol(code, market)
        updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rows = [
            (
                symbol,
                code,
                market or TXStockAPI._get_prefix(code),
                item["date"],
                item["open"],
                item["close"],
                item["high"],
                item["low"],
                item["volume"],
                "tencent",
                updated_at,
            )
            for item in klines
        ]
        with self._connect() as conn:
            conn.executemany("""
                INSERT INTO daily_klines (
                    symbol, code, market, trade_date, open, close, high, low,
                    volume, source, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, trade_date) DO UPDATE SET
                    open = excluded.open,
                    close = excluded.close,
                    high = excluded.high,
                    low = excluded.low,
                    volume = excluded.volume,
                    updated_at = excluded.updated_at
            """, rows)

    def get_klines(self, code: str, end_date: str, market: str = None, limit: int = 120) -> list:
        symbol = self.symbol(code, market)
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT trade_date, open, close, high, low, volume
                FROM daily_klines
                WHERE symbol = ? AND trade_date <= ?
                ORDER BY trade_date DESC
                LIMIT ?
            """, (symbol, end_date, limit)).fetchall()

        rows.reverse()
        return [
            {
                "date": row[0],
                "open": row[1],
                "close": row[2],
                "high": row[3],
                "low": row[4],
                "volume": row[5],
            }
            for row in rows
        ]


class IndicatorCalculator:
    """常规技术指标和量价状态计算"""

    @staticmethod
    def _sma(values: list, period: int):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    @staticmethod
    def _ema_series(values: list, period: int) -> list:
        if not values:
            return []
        alpha = 2 / (period + 1)
        ema = [values[0]]
        for value in values[1:]:
            ema.append(value * alpha + ema[-1] * (1 - alpha))
        return ema

    @classmethod
    def calculate(cls, klines: list, float_shares: float = None) -> dict:
        if not klines:
            return {}

        closes = [k["close"] for k in klines]
        highs = [k["high"] for k in klines]
        lows = [k["low"] for k in klines]
        volumes = [k["volume"] for k in klines]
        last = klines[-1]
        close = closes[-1]

        ma = {period: cls._sma(closes, period) for period in (5, 10, 20, 60)}
        prev_ma = {period: cls._sma(closes[:-1], period) for period in (5, 10, 20, 60)}
        volume_ma5 = cls._sma(volumes, 5)
        volume_ma20 = cls._sma(volumes, 20)

        ema12 = cls._ema_series(closes, 12)
        ema26 = cls._ema_series(closes, 26)
        dif_series = [a - b for a, b in zip(ema12, ema26)]
        dea_series = cls._ema_series(dif_series, 9)
        macd_hist = [(dif - dea) * 2 for dif, dea in zip(dif_series, dea_series)]

        rsi6 = cls._rsi(closes, 6)
        kdj = cls._kdj(klines)
        boll = cls._boll(closes)
        atr14 = cls._atr(klines, 14)

        amplitude = ((last["high"] - last["low"]) / close * 100) if close else None
        volume_ratio = (last["volume"] / volume_ma5) if volume_ma5 else None
        turnover_rate = None
        if float_shares:
            turnover_rate = last["volume"] * 100 / float_shares * 100

        values = {
            "ma": ma,
            "prev_ma": prev_ma,
            "macd": {
                "dif": dif_series[-1] if dif_series else None,
                "dea": dea_series[-1] if dea_series else None,
                "hist": macd_hist[-1] if macd_hist else None,
                "prev_hist": macd_hist[-2] if len(macd_hist) >= 2 else None,
            },
            "kdj": kdj,
            "rsi6": rsi6,
            "boll": boll,
            "volume_ma5": volume_ma5,
            "volume_ma20": volume_ma20,
            "volume_ratio": volume_ratio,
            "turnover_rate": turnover_rate,
            "amplitude": amplitude,
            "atr14": atr14,
        }
        values["states"] = cls.describe(values, close, last["volume"])
        return values

    @staticmethod
    def _rsi(closes: list, period: int):
        if len(closes) <= period:
            return None
        gains = []
        losses = []
        for i in range(-period, 0):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0))
            losses.append(abs(min(diff, 0)))
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    @staticmethod
    def _kdj(klines: list, period: int = 9):
        if len(klines) < period:
            return None
        k_value = 50.0
        d_value = 50.0
        for idx in range(period - 1, len(klines)):
            window = klines[idx - period + 1:idx + 1]
            low = min(item["low"] for item in window)
            high = max(item["high"] for item in window)
            close = klines[idx]["close"]
            rsv = 50.0 if high == low else (close - low) / (high - low) * 100
            k_value = (2 / 3) * k_value + (1 / 3) * rsv
            d_value = (2 / 3) * d_value + (1 / 3) * k_value
        return {"k": k_value, "d": d_value, "j": 3 * k_value - 2 * d_value}

    @classmethod
    def _boll(cls, closes: list, period: int = 20):
        if len(closes) < period:
            return None
        window = closes[-period:]
        mid = sum(window) / period
        variance = sum((value - mid) ** 2 for value in window) / period
        std = math.sqrt(variance)
        return {"upper": mid + 2 * std, "mid": mid, "lower": mid - 2 * std}

    @staticmethod
    def _atr(klines: list, period: int = 14):
        if len(klines) <= period:
            return None
        trs = []
        for idx in range(1, len(klines)):
            high = klines[idx]["high"]
            low = klines[idx]["low"]
            prev_close = klines[idx - 1]["close"]
            trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        if len(trs) < period:
            return None
        return sum(trs[-period:]) / period

    @staticmethod
    def _fmt(value, suffix: str = "", digits: int = 2) -> str:
        if value is None:
            return "N/A"
        return f"{value:.{digits}f}{suffix}"

    @classmethod
    def describe(cls, values: dict, close: float, volume: int) -> list:
        states = []
        ma = values["ma"]
        ma_parts = []
        for period in (5, 10, 20, 60):
            value = ma.get(period)
            if value:
                pos = "上方" if close >= value else "下方"
                ma_parts.append(f"MA{period}{pos}({value:.2f})")
        if ma_parts:
            states.append("均线：" + "，".join(ma_parts))

        macd = values["macd"]
        if macd["dif"] is not None and macd["dea"] is not None:
            axis = "零轴上方" if macd["dif"] >= 0 else "零轴下方"
            hist = macd["hist"]
            prev_hist = macd["prev_hist"]
            if hist is not None and prev_hist is not None:
                momentum = "动能增强" if abs(hist) > abs(prev_hist) else "动能减弱"
                bar = "红柱" if hist >= 0 else "绿柱"
                states.append(f"MACD：{axis}，{bar}{momentum}")

        rsi = values.get("rsi6")
        if rsi is not None:
            if rsi >= 80:
                rsi_state = "偏热"
            elif rsi >= 60:
                rsi_state = "偏强"
            elif rsi <= 20:
                rsi_state = "偏冷"
            elif rsi <= 40:
                rsi_state = "偏弱"
            else:
                rsi_state = "中性"
            states.append(f"RSI6：{rsi_state}({rsi:.1f})")

        kdj = values.get("kdj")
        if kdj:
            kdj_state = "偏强" if kdj["k"] >= kdj["d"] else "偏弱"
            states.append(f"KDJ：{kdj_state}(K {kdj['k']:.1f}, D {kdj['d']:.1f}, J {kdj['j']:.1f})")

        boll = values.get("boll")
        if boll:
            if close >= boll["upper"]:
                boll_state = "触及或突破上轨"
            elif close <= boll["lower"]:
                boll_state = "触及或跌破下轨"
            elif close >= boll["mid"]:
                boll_state = "位于中轨上方"
            else:
                boll_state = "位于中轨下方"
            states.append(f"BOLL：{boll_state}")

        volume_ma20 = values.get("volume_ma20")
        if volume_ma20:
            ratio = volume / volume_ma20
            if ratio >= 2:
                volume_state = "显著放量"
            elif ratio >= 1.2:
                volume_state = "温和放量"
            elif ratio <= 0.6:
                volume_state = "缩量"
            else:
                volume_state = "接近20日均量"
            states.append(f"成交量：{volume_state}({ratio:.2f}x 20日均量)")

        turnover_rate = values.get("turnover_rate")
        if turnover_rate is not None:
            if turnover_rate >= 15:
                turnover_state = "高换手"
            elif turnover_rate >= 5:
                turnover_state = "活跃"
            elif turnover_rate <= 1:
                turnover_state = "低换手"
            else:
                turnover_state = "正常"
            states.append(f"换手率：{turnover_state}({turnover_rate:.2f}%)")
        else:
            states.append("换手率：未配置流通股本，暂不计算")

        amplitude = values.get("amplitude")
        atr = values.get("atr14")
        if amplitude is not None:
            states.append(f"振幅：{amplitude:.2f}%")
        if atr is not None:
            states.append(f"ATR14：{atr:.2f}")

        return states


class ReportGenerator:
    """报告生成器"""
    
    def __init__(self, target_date: str = None, paths: RuntimePaths = None):
        """
        初始化报告生成器
        target_date: 目标日期，格式 '2025-05-22'，None表示今天
        """
        self.api = TXStockAPI()
        self.paths = paths or RuntimePaths()
        self.kline_store = KLineStore(self.paths.db_dir / "china_stock_daily_tracker.sqlite")
        
        if target_date:
            self.target_date = datetime.strptime(target_date, '%Y-%m-%d')
            self.is_historical = True
        else:
            self.target_date = datetime.now()
            self.is_historical = False
        
        self.date_str = self.target_date.strftime("%Y%m%d")
        self.date_display = self.target_date.strftime("%Y-%m-%d")
        
    def is_trading_day(self) -> bool:
        """简单判断是否为交易日（周一至周五）"""
        weekday = self.target_date.weekday()
        return weekday < 5  # 0-4 为周一到周五
    
    def load_config(self, filename: str) -> dict:
        """加载配置文件。"""
        filepath = self.paths.config_dir / filename
        if not filepath.exists():
            return {}
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()

            try:
                import yaml
                data = yaml.safe_load(content)
                return data if isinstance(data, dict) else {}
            except ImportError:
                pass

            return self._parse_simple_yaml(content)
        except Exception as e:
            print(f"[WARN] 加载配置失败 {filename}: {e}")
            return {}
    
    def _parse_simple_yaml(self, content: str) -> dict:
        """简单YAML解析器，支持本项目配置所需的映射、列表和嵌套列表。"""
        lines = []
        for raw_line in content.splitlines():
            if not raw_line.strip() or raw_line.lstrip().startswith('#'):
                continue
            indent = len(raw_line) - len(raw_line.lstrip(' '))
            lines.append((indent, raw_line.strip()))

        def parse_scalar(value: str):
            value = value.strip()
            if not value:
                return None
            if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                return value[1:-1]
            if value.startswith('[') and value.endswith(']'):
                arr = []
                for item in value[1:-1].split(','):
                    parsed = parse_scalar(item)
                    if parsed is not None:
                        arr.append(parsed)
                return arr
            if value.lower() in {'true', 'false'}:
                return value.lower() == 'true'
            try:
                return float(value) if '.' in value else int(value)
            except ValueError:
                return value

        def parse_block(index: int, indent: int):
            if index >= len(lines) or lines[index][0] < indent:
                return None, index
            if lines[index][0] == indent and lines[index][1].startswith('- '):
                return parse_list(index, indent)
            return parse_map(index, indent)

        def parse_list(index: int, indent: int):
            result = []
            while index < len(lines):
                line_indent, stripped = lines[index]
                if line_indent < indent:
                    break
                if line_indent != indent or not stripped.startswith('- '):
                    break

                item_content = stripped[2:].strip()
                if not item_content:
                    item, index = parse_block(index + 1, indent + 2)
                    result.append(item)
                    continue

                if ':' not in item_content:
                    result.append(parse_scalar(item_content))
                    index += 1
                    continue

                item = {}
                key, value = item_content.split(':', 1)
                key = key.strip()
                value = value.strip()
                if value:
                    item[key] = parse_scalar(value)
                    index += 1
                else:
                    child, index = parse_block(index + 1, indent + 2)
                    item[key] = child if child is not None else []

                while index < len(lines):
                    prop_indent, prop_line = lines[index]
                    if prop_indent <= indent:
                        break
                    if prop_indent != indent + 2 or prop_line.startswith('- ') or ':' not in prop_line:
                        break

                    prop_key, prop_value = prop_line.split(':', 1)
                    prop_key = prop_key.strip()
                    prop_value = prop_value.strip()
                    if prop_value:
                        item[prop_key] = parse_scalar(prop_value)
                        index += 1
                    else:
                        child, index = parse_block(index + 1, prop_indent + 2)
                        item[prop_key] = child if child is not None else []

                result.append(item)
            return result, index

        def parse_map(index: int, indent: int):
            result = {}
            while index < len(lines):
                line_indent, stripped = lines[index]
                if line_indent < indent:
                    break
                if line_indent != indent or stripped.startswith('- ') or ':' not in stripped:
                    break

                key, value = stripped.split(':', 1)
                key = key.strip()
                value = value.strip()
                if value:
                    result[key] = parse_scalar(value)
                    index += 1
                else:
                    child, index = parse_block(index + 1, indent + 2)
                    result[key] = child if child is not None else {}
            return result, index

        parsed, _ = parse_block(0, 0)
        return parsed if isinstance(parsed, dict) else {}
    
    def get_index_data(self) -> list:
        """获取主要指数数据"""
        # 历史日期报告使用实时指数数据（腾讯K线接口不支持指数历史数据）
        return self._get_realtime_index_data()
    
    def _get_realtime_index_data(self) -> list:
        """获取实时指数数据"""
        # 使用 (code, market) 元组格式
        codes = [(code[2:], code[:2]) for code in INDICES.keys()]
        data = self.api.realtime(codes)
        
        # 补充指数信息
        for item in data:
            market = self.api._get_prefix(item['code'])
            full_code = f"{market}{item['code']}"
            if full_code in INDICES:
                item['index_name'] = INDICES[full_code][0]
                item['market'] = INDICES[full_code][1]
        
        return data
    
    def _get_historical_index_data(self) -> list:
        """获取历史指数数据"""
        results = []
        date_str = self.date_display
        
        for full_code, (name, market) in INDICES.items():
            code = full_code[2:]  # 去掉前缀
            prefix = full_code[:2]  # 前缀
            data = self.api.get_daily_quote(code, date_str, market=prefix)
            if data:
                results.append({
                    'name': code,
                    'code': code,
                    'index_name': name,
                    'market': market,
                    'price': data['close'],
                    'pre_close': data['pre_close'],
                    'open': data['open'],
                    'high': data['high'],
                    'low': data['low'],
                    'volume': data['volume'],
                    'change': data['change'],
                    'change_pct': data['change_pct'],
                })
        
        return results
    
    def get_watchlist_data(self) -> list:
        """获取自选股数据"""
        config = self.load_config("watchlist.yaml")
        watchlist = config.get("watchlist", [])
        
        if not watchlist:
            return []
        
        if self.is_historical:
            data = self._get_historical_watchlist_data(watchlist)
        else:
            data = self._get_realtime_watchlist_data(watchlist)

        return self._enrich_stock_data(data, watchlist)
    
    def _get_realtime_watchlist_data(self, watchlist: list) -> list:
        """获取实时自选股数据"""
        # 使用 (code, market) 元组格式
        codes = [(item["code"], item.get("market")) for item in watchlist]
        quotes = self.api.realtime(codes)
        
        # 合并配置信息
        if isinstance(quotes, dict):
            quotes = [quotes]
        
        code_to_config = {item["code"]: item for item in watchlist}
        for quote in quotes:
            config_item = code_to_config.get(quote["code"], {})
            quote["tags"] = config_item.get("tags", [])
        
        return quotes
    
    def _get_historical_watchlist_data(self, watchlist: list) -> list:
        """获取历史自选股数据"""
        results = []
        date_str = self.date_display
        
        for item in watchlist:
            code = item["code"]
            market = item.get("market")
            data = self.api.get_daily_quote(code, date_str, market=market)
            if data:
                results.append({
                    'name': item.get('name', code),
                    'code': code,
                    'price': data['close'],
                    'pre_close': data['pre_close'],
                    'open': data['open'],
                    'high': data['high'],
                    'low': data['low'],
                    'volume': data['volume'],
                    'change': data['change'],
                    'change_pct': data['change_pct'],
                    'tags': item.get('tags', []),
                })
        
        return results
    
    def get_portfolio_data(self) -> list:
        """获取持仓股数据"""
        config = self.load_config("portfolio.yaml")
        portfolio = config.get("portfolio", [])
        
        if not portfolio:
            return []
        
        if self.is_historical:
            data = self._get_historical_portfolio_data(portfolio)
        else:
            data = self._get_realtime_portfolio_data(portfolio)

        return self._enrich_stock_data(data, portfolio)

    def _ensure_kline_data(self, code: str, market: str = None):
        """优先使用本地K线；本地缺失或样本不足时补拉外部数据。"""
        start_date = (self.target_date - timedelta(days=DEFAULT_LOOKBACK_DAYS)).strftime('%Y-%m-%d')
        latest = self.kline_store.latest_date(code, market)
        local_count = self.kline_store.count_since(code, start_date, market)
        if latest and latest >= self.date_display and local_count >= 60:
            return

        klines = self.api.get_kline(code, start_date, self.date_display, market=market)
        if klines:
            self.kline_store.upsert_many(code, market, klines)

    def _enrich_stock_data(self, data: list, config_items: list) -> list:
        """补充本地K线、指标状态和基础风险提示。"""
        config_by_code = {item["code"]: item for item in config_items}
        for item in data:
            code = item["code"]
            config = config_by_code.get(code, {})
            market = config.get("market") or item.get("market")
            item["market"] = market
            self._ensure_kline_data(code, market)
            klines = self.kline_store.get_klines(code, self.date_display, market=market, limit=120)
            float_shares = config.get("float_shares")
            indicators = IndicatorCalculator.calculate(klines, float_shares=float_shares)
            item["klines_count"] = len(klines)
            item["last_kline"] = klines[-1] if klines else None
            item["prev_kline"] = klines[-2] if len(klines) >= 2 else None
            item["indicators"] = indicators
            item["risk_flags"] = self._build_risk_flags(item)
        return data

    def _build_risk_flags(self, item: dict) -> list:
        """基于事实的持仓风险提示，不给买卖建议。"""
        flags = []
        change_pct = item.get("change_pct", 0)
        profit_pct = item.get("profit_pct")
        indicators = item.get("indicators") or {}
        ma = indicators.get("ma") or {}
        price = item.get("price")

        if change_pct <= -5:
            flags.append(f"单日跌幅 {abs(change_pct):.2f}% ，波动较大")
        if profit_pct is not None and profit_pct <= -10:
            flags.append(f"浮亏 {abs(profit_pct):.2f}% ，已超过10%观察阈值")
        if price and ma.get(20) and price < ma[20]:
            flags.append("收盘价位于MA20下方")
        if price and ma.get(60) and price < ma[60]:
            flags.append("收盘价位于MA60下方")
        turnover_rate = indicators.get("turnover_rate")
        if turnover_rate and turnover_rate >= 15:
            flags.append(f"换手率 {turnover_rate:.2f}% ，交易分歧较高")

        return flags
    
    def _get_realtime_portfolio_data(self, portfolio: list) -> list:
        """获取实时持仓股数据"""
        # 使用 (code, market) 元组格式
        codes = [(item["code"], item.get("market")) for item in portfolio]
        quotes = self.api.realtime(codes)
        
        if isinstance(quotes, dict):
            quotes = [quotes]
        
        # 计算持仓盈亏
        code_to_config = {item["code"]: item for item in portfolio}
        results = []
        
        for quote in quotes:
            config_item = code_to_config.get(quote["code"], {})
            position = config_item.get("position", 0)
            cost_price = config_item.get("cost_price", 0)
            
            current_price = quote["price"]
            
            # 计算盈亏
            profit_per_share = current_price - cost_price if cost_price > 0 else 0
            total_profit = profit_per_share * position
            profit_pct = (profit_per_share / cost_price * 100) if cost_price > 0 else 0
            market_value = current_price * position
            cost_value = cost_price * position
            
            quote["position"] = position
            quote["cost_price"] = cost_price
            quote["market_value"] = round(market_value, 2)
            quote["cost_value"] = round(cost_value, 2)
            quote["total_profit"] = round(total_profit, 2)
            quote["profit_pct"] = round(profit_pct, 2)
            quote["target_weight"] = config_item.get("target_weight")
            quote["broker"] = config_item.get("broker")
            quote["buy_orders"] = config_item.get("buy_orders", [])
            
            results.append(quote)
        
        return results
    
    def _get_historical_portfolio_data(self, portfolio: list) -> list:
        """获取历史持仓股数据"""
        results = []
        date_str = self.date_display
        
        for item in portfolio:
            code = item["code"]
            market = item.get("market")
            position = item.get("position", 0)
            cost_price = item.get("cost_price", 0)
            
            data = self.api.get_daily_quote(code, date_str, market=market)
            if not data:
                continue
            
            current_price = data['close']
            
            # 计算盈亏
            profit_per_share = current_price - cost_price if cost_price > 0 else 0
            total_profit = profit_per_share * position
            profit_pct = (profit_per_share / cost_price * 100) if cost_price > 0 else 0
            market_value = current_price * position
            cost_value = cost_price * position
            
            results.append({
                'name': item.get('name', code),
                'code': code,
                'price': current_price,
                'pre_close': data['pre_close'],
                'open': data['open'],
                'high': data['high'],
                'low': data['low'],
                'volume': data['volume'],
                'change': data['change'],
                'change_pct': data['change_pct'],
                'position': position,
                'cost_price': cost_price,
                'market_value': round(market_value, 2),
                'cost_value': round(cost_value, 2),
                'total_profit': round(total_profit, 2),
                'profit_pct': round(profit_pct, 2),
                'target_weight': item.get('target_weight'),
                'broker': item.get('broker'),
                'buy_orders': item.get('buy_orders', []),
            })
        
        return results
    
    def format_volume(self, volume: int) -> str:
        """格式化成交量"""
        if volume >= 100000000:
            return f"{volume/100000000:.2f}亿手"
        elif volume >= 10000:
            return f"{volume/10000:.2f}万手"
        return f"{volume}手"
    
    def format_amount(self, amount: float) -> str:
        """格式化成交额"""
        if amount >= 10000:
            return f"{amount/10000:.2f}亿"
        return f"{amount:.2f}万"

    def _format_optional(self, value, suffix: str = "", digits: int = 2) -> str:
        if value is None:
            return "-"
        return f"{value:.{digits}f}{suffix}"
    
    def generate_index_section(self, data: list, is_historical: bool = False) -> str:
        """生成指数板块"""
        lines = ["## 📊 主要指数行情\n"]
        
        if is_historical:
            lines.append("*注：历史报告使用最新指数数据（腾讯接口不支持指数历史K线）*\n")
        
        if not data:
            lines.append("*暂无数据*\n")
            return "\n".join(lines)
        
        lines.append("| 指数 | 最新价 | 涨跌 | 涨跌幅 | 成交量 |")
        lines.append("|------|--------|------|--------|--------|")
        
        for item in data:
            name = item.get('index_name', item['name'])
            change_str = f"{item['change']:+.2f}"
            change_pct_str = f"{item['change_pct']:+.2f}%"
            volume_str = self.format_volume(item['volume'])
            
            lines.append(f"| {name} | {item['price']:.2f} | {change_str} | {change_pct_str} | {volume_str} |")
        
        # 添加简要描述
        up_count = sum(1 for i in data if i['change'] > 0)
        down_count = sum(1 for i in data if i['change'] < 0)
        flat_count = len(data) - up_count - down_count
        
        lines.append(f"\n**市场概况**：{up_count}个指数上涨，{down_count}个下跌，{flat_count}个平盘。")
        
        return "\n".join(lines) + "\n"
    
    def generate_watchlist_section(self, data: list) -> str:
        """生成自选股板块"""
        lines = ["## 📈 自选股行情\n"]
        
        if not data:
            lines.append("*暂无自选股配置，请在 config/watchlist.yaml 中添加*\n")
            return "\n".join(lines)
        
        lines.append("| 股票 | 代码 | 现价 | 涨跌 | 涨跌幅 | 成交量 | 标签 |")
        lines.append("|------|------|------|------|--------|--------|------|")
        
        for item in data:
            tags = ", ".join(item.get('tags', [])) if item.get('tags') else "-"
            change_str = f"{item['change']:+.2f}"
            change_pct_str = f"{item['change_pct']:+.2f}%"
            volume_str = self.format_volume(item['volume'])
            
            lines.append(f"| {item['name']} | {item['code']} | {item['price']:.2f} | {change_str} | {change_pct_str} | {volume_str} | {tags} |")
        
        # 异动描述
        movers = [i for i in data if abs(i['change_pct']) >= 3]
        if movers:
            lines.append(f"\n**今日异动**：")
            for m in movers:
                direction = "上涨" if m['change_pct'] > 0 else "下跌"
                lines.append(f"- {m['name']} ({m['code']}) {direction} {abs(m['change_pct']):.2f}%")
        
        return "\n".join(lines) + "\n"
    
    def generate_portfolio_section(self, data: list) -> str:
        """生成持仓股板块"""
        lines = ["## 💼 持仓股监控\n"]
        
        if not data:
            lines.append("*暂无持仓配置，请在 config/portfolio.yaml 中添加*\n")
            return "\n".join(lines)
        
        lines.append("| 股票 | 代码 | 券商 | 现价 | 今日涨跌 | 持仓 | 成本价 | 市值 | 浮动盈亏 | 盈亏率 |")
        lines.append("|------|------|------|------|----------|------|--------|------|----------|--------|")
        
        total_market_value = 0
        total_cost_value = 0
        
        for item in data:
            total_market_value += item['market_value']
            total_cost_value += item['cost_value']
            
            change_str = f"{item['change']:+.2f} ({item['change_pct']:+.2f}%)"
            profit_str = f"{item['total_profit']:+.2f}"
            profit_pct_str = f"{item['profit_pct']:+.2f}%"
            broker = item.get('broker') or "-"
            
            lines.append(f"| {item['name']} | {item['code']} | {broker} | {item['price']:.2f} | {change_str} | {item['position']} | {item['cost_price']:.2f} | {item['market_value']:.2f} | {profit_str} | {profit_pct_str} |")
        
        # 汇总
        total_profit = total_market_value - total_cost_value
        total_profit_pct = (total_profit / total_cost_value * 100) if total_cost_value > 0 else 0
        
        lines.append(f"\n**持仓汇总**：")
        lines.append(f"- 总市值：{total_market_value:,.2f} 元")
        lines.append(f"- 总成本：{total_cost_value:,.2f} 元")
        lines.append(f"- 浮动盈亏：{total_profit:+.2f} 元 ({total_profit_pct:+.2f}%)")

        risk_items = []
        for item in data:
            for flag in item.get("risk_flags", []):
                risk_items.append(f"- {item['name']} ({item['code']})：{flag}")
        if risk_items:
            lines.append("\n**持仓风险提示（事实阈值）**：")
            lines.extend(risk_items)
        
        return "\n".join(lines) + "\n"

    def generate_indicator_section(self, watchlist_data: list, portfolio_data: list) -> str:
        """生成技术指标和量价状态板块"""
        lines = ["## 📐 技术指标与量价状态\n"]
        combined = self._dedupe_stock_rows(portfolio_data + watchlist_data)
        if not combined:
            lines.append("*暂无股票数据*\n")
            return "\n".join(lines)

        lines.append("| 股票 | 代码 | K线样本 | MA状态 | MACD | RSI6 | 成交量 | 换手率 |")
        lines.append("|------|------|---------|--------|------|------|--------|--------|")

        for item in combined:
            indicators = item.get("indicators") or {}
            ma = indicators.get("ma") or {}
            price = item.get("price")
            ma20 = ma.get(20)
            ma60 = ma.get(60)
            ma_state = []
            if price and ma20:
                ma_state.append("MA20上方" if price >= ma20 else "MA20下方")
            if price and ma60:
                ma_state.append("MA60上方" if price >= ma60 else "MA60下方")
            ma_text = "，".join(ma_state) if ma_state else "-"

            macd = indicators.get("macd") or {}
            hist = macd.get("hist")
            prev_hist = macd.get("prev_hist")
            if hist is None:
                macd_text = "-"
            else:
                bar = "红柱" if hist >= 0 else "绿柱"
                if prev_hist is None:
                    macd_text = bar
                else:
                    macd_text = f"{bar}{'放大' if abs(hist) > abs(prev_hist) else '缩短'}"

            volume_ma20 = indicators.get("volume_ma20")
            if volume_ma20:
                volume_text = f"{item['volume'] / volume_ma20:.2f}x"
            else:
                volume_text = "-"

            turnover = indicators.get("turnover_rate")
            lines.append(
                f"| {item['name']} | {item['code']} | {item.get('klines_count', 0)} | "
                f"{ma_text} | {macd_text} | {self._format_optional(indicators.get('rsi6'), digits=1)} | "
                f"{volume_text} | {self._format_optional(turnover, '%')} |"
            )

        lines.append("\n**状态说明**：")
        for item in combined[:8]:
            states = (item.get("indicators") or {}).get("states") or []
            if states:
                lines.append(f"- {item['name']} ({item['code']})：" + "；".join(states[:4]))
        if len(combined) > 8:
            lines.append(f"- 其余 {len(combined) - 8} 只股票已计算指标，详见上表。")

        return "\n".join(lines) + "\n"

    def _dedupe_stock_rows(self, rows: list) -> list:
        seen = set()
        result = []
        for row in rows:
            key = row.get("code")
            if key in seen:
                continue
            seen.add(key)
            result.append(row)
        return result

    def load_strategy_rules(self) -> list:
        config = self.load_config("strategy_rules.yaml")
        return config.get("strategies", [])

    def generate_strategy_section(self, watchlist_data: list, portfolio_data: list) -> str:
        """生成经验策略规则板块"""
        lines = ["## 📚 经验策略规则\n"]
        rules = self.load_strategy_rules()
        if not rules:
            lines.append("*暂无策略规则配置，请在 config/strategy_rules.yaml 中添加*\n")
            return "\n".join(lines)

        status_counts = {}
        for rule in rules:
            status = rule.get("status", "active")
            status_counts[status] = status_counts.get(status, 0) + 1
        lines.append(
            "**规则库状态**：" +
            "，".join(f"{status} {count}条" for status, count in sorted(status_counts.items()))
        )
        lines.append("")

        active_rules = [rule for rule in rules if rule.get("status", "active") in ("active", "testing")]
        lines.append("| 状态 | 类别 | 规则 |")
        lines.append("|------|------|------|")
        for rule in active_rules[:12]:
            lines.append(
                f"| {rule.get('status', 'active')} | {rule.get('category', '-')} | "
                f"{rule.get('text', rule.get('name', '-'))} |"
            )

        triggered = self._evaluate_strategy_facts(active_rules, self._dedupe_stock_rows(portfolio_data + watchlist_data))
        if triggered:
            lines.append("\n**今日规则事实检查**：")
            lines.extend(triggered[:20])
        else:
            lines.append("\n**今日规则事实检查**：暂无可自动检查的规则触发。")

        return "\n".join(lines) + "\n"

    def _evaluate_strategy_facts(self, rules: list, stocks: list) -> list:
        results = []
        for stock in stocks:
            indicators = stock.get("indicators") or {}
            ma = indicators.get("ma") or {}
            prev_ma = indicators.get("prev_ma") or {}
            price = stock.get("price")
            prev = stock.get("prev_kline") or {}
            for rule in rules:
                check = rule.get("check")
                if not check:
                    continue
                if check == "below_ma20" and price and ma.get(20) and price < ma[20]:
                    results.append(f"- {stock['name']} ({stock['code']})：低于MA20，匹配规则「{rule.get('name', rule.get('text', '未命名'))}」")
                elif check == "below_ma60" and price and ma.get(60) and price < ma[60]:
                    results.append(f"- {stock['name']} ({stock['code']})：低于MA60，匹配规则「{rule.get('name', rule.get('text', '未命名'))}」")
                elif check == "break_below_ma5" and price and prev.get("close") and ma.get(5) and prev_ma.get(5):
                    if prev["close"] >= prev_ma[5] and price < ma[5]:
                        results.append(f"- {stock['name']} ({stock['code']})：跌破MA5，匹配规则「{rule.get('name', rule.get('text', '未命名'))}」")
                elif check == "volume_above_20ma":
                    volume_ma20 = indicators.get("volume_ma20")
                    if volume_ma20 and stock.get("volume", 0) >= volume_ma20 * 1.2:
                        results.append(f"- {stock['name']} ({stock['code']})：成交量高于20日均量，匹配规则「{rule.get('name', rule.get('text', '未命名'))}」")
        return results
    
    def generate_sector_section(self) -> str:
        """生成板块轮动板块（V1预留）"""
        lines = ["## 🔄 板块轮动\n"]
        lines.append("*板块数据暂未接入，V2版本将支持*\n")
        return "\n".join(lines) + "\n"
    
    def generate_summary_section(self, index_data: list, watchlist_data: list, portfolio_data: list) -> str:
        """生成市场总结 - 仅使用事实性语言"""
        lines = ["## 📝 市场总结\n"]
        
        if not index_data:
            lines.append("*暂无数据*\n")
            return "\n".join(lines)
        
        summary_points = []
        
        # 1. 主要指数涨跌
        for idx in index_data:
            name = idx.get('index_name', idx['name'])
            change_pct = idx['change_pct']
            direction = "上涨" if change_pct > 0 else "下跌"
            summary_points.append(f"- {name}{direction} {abs(change_pct):.1f}%")
        
        # 2. 风格判断（大盘 vs 小盘）
        large_cap_codes = ['000300', '000016']  # 沪深300、上证50
        small_cap_codes = ['000852', '399006']  # 中证1000、创业板指
        
        large_cap_data = [i for i in index_data if i['code'] in large_cap_codes]
        small_cap_data = [i for i in index_data if i['code'] in small_cap_codes]
        
        if large_cap_data and small_cap_data:
            large_avg = sum(i['change_pct'] for i in large_cap_data) / len(large_cap_data)
            small_avg = sum(i['change_pct'] for i in small_cap_data) / len(small_cap_data)
            
            if large_avg > small_avg + 0.5:
                summary_points.append("- 权重股强于小盘股")
            elif small_avg > large_avg + 0.5:
                summary_points.append("- 小盘股强于权重股")
            else:
                summary_points.append("- 大小盘表现分化不明显")
        
        # 3. 市场涨跌分布（基于自选股样本）
        if watchlist_data:
            up_count = sum(1 for i in watchlist_data if i['change_pct'] > 0)
            down_count = sum(1 for i in watchlist_data if i['change_pct'] < 0)
            flat_count = len(watchlist_data) - up_count - down_count
            
            if up_count > down_count * 1.5:
                summary_points.append(f"- 个股涨多跌少（{up_count}涨{down_count}跌）")
            elif down_count > up_count * 1.5:
                summary_points.append(f"- 个股跌多涨少（{up_count}涨{down_count}跌）")
            else:
                summary_points.append(f"- 个股涨跌互现（{up_count}涨{down_count}跌）")
        
        # 4. 持仓整体表现
        if portfolio_data:
            total_profit_pct = sum(i['profit_pct'] for i in portfolio_data) / len(portfolio_data)
            if total_profit_pct > 0:
                summary_points.append(f"- 持仓整体浮盈 {total_profit_pct:.1f}%")
            else:
                summary_points.append(f"- 持仓整体浮亏 {abs(total_profit_pct):.1f}%")
        
        # 5. 异动提醒（基于事实）
        all_data = watchlist_data + portfolio_data
        if all_data:
            big_movers = [i for i in all_data if abs(i['change_pct']) >= 5]
            if big_movers:
                up_movers = [i for i in big_movers if i['change_pct'] > 0]
                down_movers = [i for i in big_movers if i['change_pct'] < 0]
                if up_movers:
                    summary_points.append(f"- {len(up_movers)}只个股涨幅超5%")
                if down_movers:
                    summary_points.append(f"- {len(down_movers)}只个股跌幅超5%")
        
        lines.extend(summary_points)
        lines.append("")
        
        return "\n".join(lines)
    
    def generate_report(self, report_type: str = "close", force: bool = False) -> str:
        """生成完整报告"""
        if not force and not self.is_trading_day():
            return None
        
        # 获取数据
        index_data = self.get_index_data()
        watchlist_data = self.get_watchlist_data()
        portfolio_data = self.get_portfolio_data()
        
        # 构建报告
        lines = [
            f"# A股每日行情报告 - {self.date_display}",
            "",
            f"**报告类型**：{'收盘简报' if report_type == 'close' else '复盘报告'}",
            f"**生成时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"**数据来源**：腾讯财经{'（历史数据）' if self.is_historical else '（实时行情）'}",
            "",
            "---",
            "",
        ]
        
        lines.append(self.generate_summary_section(index_data, watchlist_data, portfolio_data))
        lines.append(self.generate_index_section(index_data, is_historical=self.is_historical))
        lines.append(self.generate_watchlist_section(watchlist_data))
        lines.append(self.generate_portfolio_section(portfolio_data))
        lines.append(self.generate_indicator_section(watchlist_data, portfolio_data))
        lines.append(self.generate_strategy_section(watchlist_data, portfolio_data))
        lines.append(self.generate_sector_section())
        
        lines.extend([
            "---",
            "",
            "## ⚠️ 免责声明",
            "",
            "本报告仅基于公开行情数据生成，**不构成任何投资建议**。",
            "所有数据仅供参考，投资有风险，决策需谨慎。",
            "",
        ])
        
        return "\n".join(lines)
    
    def save_report(self, content: str, report_type: str = "close") -> Path:
        """保存报告到文件"""
        self.paths.report_dir.mkdir(parents=True, exist_ok=True)
        
        suffix = "close" if report_type == "close" else "review"
        filename = f"daily_report_{self.date_str}_{suffix}.md"
        filepath = self.paths.report_dir / filename
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        
        return filepath


def main():
    parser = argparse.ArgumentParser(description='A股每日行情报告生成器')
    parser.add_argument('--type', choices=['close', 'review'], default='close',
                       help='报告类型：close=收盘简报, review=复盘报告')
    parser.add_argument('--date', '-d', help='指定日期，格式：2025-05-22（默认今天）')
    parser.add_argument('--output', '-o', help='输出文件路径（默认保存到reports目录）')
    parser.add_argument('--config',
                       help='运行配置JSON路径；默认查找 CHINA_STOCK_DAILY_TRACKER_CONFIG、当前目录 china-stock-daily-tracker.local.json、china-stock-daily-tracker.json')
    parser.add_argument('--force', action='store_true',
                       help='强制生成报告（忽略交易日检查）')
    args = parser.parse_args()

    paths = RuntimePaths(config_file=args.config)
    
    # 确保配置目录存在
    paths.ensure_dirs()
    
    # 创建示例配置（如果不存在）
    watchlist_path = paths.config_dir / "watchlist.yaml"
    if not watchlist_path.exists():
        with open(watchlist_path, 'w', encoding='utf-8') as f:
            f.write("""# 自选股配置
# 请在下方添加你关注的股票

watchlist:
  # 示例：
  # - code: "600519"
  #   name: "贵州茅台"
  #   tags: ["白酒", "核心资产"]
  # - code: "300750"
  #   name: "宁德时代"
  #   tags: ["新能源", "创业板"]
""")
    
    portfolio_path = paths.config_dir / "portfolio.yaml"
    if not portfolio_path.exists():
        with open(portfolio_path, 'w', encoding='utf-8') as f:
            f.write("""# 持仓配置
# 请在下方添加你的持仓记录

portfolio:
  # 示例：
  # - code: "600111"
  #   name: "北方稀土"
  #   position: 1000        # 持仓数量（股）
  #   cost_price: 25.50     # 成本价（元）
  #   target_weight: 0.15   # 目标仓位占比（可选）
""")

    strategy_rules_path = paths.config_dir / "strategy_rules.yaml"
    if not strategy_rules_path.exists():
        with open(strategy_rules_path, 'w', encoding='utf-8') as f:
            f.write("""# 经验策略规则
# status: active/testing/deprecated/conflict
# check 为可选字段，用于日报中的事实检查，不生成买卖建议。

strategies:
  - name: "20日均线趋势过滤"
    category: "均线"
    status: "active"
    check: "below_ma20"
    text: "不上20日均线，不确认趋势条件"
  - name: "60日均线长期过滤"
    category: "均线"
    status: "active"
    check: "below_ma60"
    text: "长期位于60日均线下方的标的保持谨慎观察"
  - name: "5日均线短线风险"
    category: "均线"
    status: "testing"
    check: "break_below_ma5"
    text: "跌破5日均线时记录短线风险变化"
  - name: "量能观察"
    category: "成交量"
    status: "testing"
    check: "volume_above_20ma"
    text: "成交量高于20日均量时记录量能变化"
""")
    
    # 生成报告
    generator = ReportGenerator(target_date=args.date, paths=paths)
    
    if not args.force and not generator.is_trading_day():
        print(f"[{generator.date_display}] 非交易日，跳过报告生成（使用 --force 强制生成）")
        sys.exit(0)
    
    print(f"[{generator.date_display}] 正在生成{('收盘简报' if args.type == 'close' else '复盘报告')}...")
    
    report = generator.generate_report(args.type, force=args.force)
    if not report:
        print("报告生成失败")
        sys.exit(1)
    
    # 保存报告
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(report)
        print(f"报告已保存: {output_path}")
    else:
        filepath = generator.save_report(report, args.type)
        print(f"报告已保存: {filepath}")
    
    # 同时输出到控制台（完整报告，供 OpenClaw 捕获发送到对话）
    print("\n" + "="*50)
    print(report)
    print("="*50)


if __name__ == "__main__":
    main()
