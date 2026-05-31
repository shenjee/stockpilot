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
from datetime import datetime, timedelta
from pathlib import Path

# 配置路径
SKILL_DIR = Path(__file__).parent.parent
CONFIG_DIR = SKILL_DIR / "assets" / "config"
REPORT_DIR = SKILL_DIR / "assets" / "reports"

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


class ReportGenerator:
    """报告生成器"""
    
    def __init__(self, target_date: str = None):
        """
        初始化报告生成器
        target_date: 目标日期，格式 '2025-05-22'，None表示今天
        """
        self.api = TXStockAPI()
        
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
        """加载配置文件（简单YAML子集解析）"""
        filepath = CONFIG_DIR / filename
        if not filepath.exists():
            return {}
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            return self._parse_simple_yaml(content)
        except Exception as e:
            print(f"[WARN] 加载配置失败 {filename}: {e}")
            return {}
    
    def _parse_simple_yaml(self, content: str) -> dict:
        """简单YAML解析器（仅支持列表和基本键值对）"""
        result = {}
        current_list = None
        current_list_key = None
        current_item = None
        
        # 保护字段：保持字符串格式
        string_fields = {'code', 'name'}
        
        for line in content.split('\n'):
            # 跳过注释和空行
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            
            # 列表键（如 "watchlist:")
            if stripped.endswith(':') and not stripped.startswith('-'):
                key = stripped[:-1].strip()
                result[key] = []
                current_list_key = key
                current_list = result[key]
                current_item = None
                continue
            
            # 列表项（如 "- code: \"600519\"")
            if stripped.startswith('- '):
                # 新列表项
                current_item = {}
                if current_list is not None:
                    current_list.append(current_item)
                
                # 解析 "- key: value" 格式
                item_content = stripped[2:].strip()
                if ':' in item_content:
                    key, value = item_content.split(':', 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    # 尝试转换为数字（但保护字段保持字符串）
                    if key not in string_fields:
                        try:
                            if '.' in value:
                                value = float(value)
                            else:
                                value = int(value)
                        except:
                            pass
                    current_item[key] = value
                continue
            
            # 列表项的属性（缩进格式）
            if current_item is not None and ':' in stripped:
                key, value = stripped.split(':', 1)
                key = key.strip()
                value = value.strip()
                
                # 处理数组值（如 tags: ["a", "b"]）
                if value.startswith('[') and value.endswith(']'):
                    try:
                        # 简单解析数组
                        arr_content = value[1:-1]
                        arr = []
                        for item in arr_content.split(','):
                            item = item.strip().strip('"').strip("'")
                            if item:
                                arr.append(item)
                        current_item[key] = arr
                    except:
                        current_item[key] = value
                else:
                    value = value.strip('"').strip("'")
                    # 尝试转换为数字（但保护字段保持字符串）
                    if key not in string_fields:
                        try:
                            if '.' in value:
                                value = float(value)
                            else:
                                value = int(value)
                        except:
                            pass
                    current_item[key] = value
        
        return result
    
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
            return self._get_historical_watchlist_data(watchlist)
        else:
            return self._get_realtime_watchlist_data(watchlist)
    
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
            return self._get_historical_portfolio_data(portfolio)
        else:
            return self._get_realtime_portfolio_data(portfolio)
    
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
        
        lines.append("| 股票 | 代码 | 现价 | 今日涨跌 | 持仓 | 成本价 | 市值 | 浮动盈亏 | 盈亏率 |")
        lines.append("|------|------|------|----------|------|--------|------|----------|--------|")
        
        total_market_value = 0
        total_cost_value = 0
        
        for item in data:
            total_market_value += item['market_value']
            total_cost_value += item['cost_value']
            
            change_str = f"{item['change']:+.2f} ({item['change_pct']:+.2f}%)"
            profit_str = f"{item['total_profit']:+.2f}"
            profit_pct_str = f"{item['profit_pct']:+.2f}%"
            
            lines.append(f"| {item['name']} | {item['code']} | {item['price']:.2f} | {change_str} | {item['position']} | {item['cost_price']:.2f} | {item['market_value']:.2f} | {profit_str} | {profit_pct_str} |")
        
        # 汇总
        total_profit = total_market_value - total_cost_value
        total_profit_pct = (total_profit / total_cost_value * 100) if total_cost_value > 0 else 0
        
        lines.append(f"\n**持仓汇总**：")
        lines.append(f"- 总市值：{total_market_value:,.2f} 元")
        lines.append(f"- 总成本：{total_cost_value:,.2f} 元")
        lines.append(f"- 浮动盈亏：{total_profit:+.2f} 元 ({total_profit_pct:+.2f}%)")
        
        return "\n".join(lines) + "\n"
    
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
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        
        suffix = "close" if report_type == "close" else "review"
        filename = f"daily_report_{self.date_str}_{suffix}.md"
        filepath = REPORT_DIR / filename
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        
        return filepath


def main():
    parser = argparse.ArgumentParser(description='A股每日行情报告生成器')
    parser.add_argument('--type', choices=['close', 'review'], default='close',
                       help='报告类型：close=收盘简报, review=复盘报告')
    parser.add_argument('--date', '-d', help='指定日期，格式：2025-05-22（默认今天）')
    parser.add_argument('--output', '-o', help='输出文件路径（默认保存到reports目录）')
    parser.add_argument('--force', action='store_true',
                       help='强制生成报告（忽略交易日检查）')
    args = parser.parse_args()
    
    # 确保配置目录存在
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    
    # 创建示例配置（如果不存在）
    watchlist_path = CONFIG_DIR / "watchlist.yaml"
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
    
    portfolio_path = CONFIG_DIR / "portfolio.yaml"
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
    
    # 生成报告
    generator = ReportGenerator(target_date=args.date)
    
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
