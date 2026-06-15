import math


class IndicatorCalculator:
    """常规技术指标和量价状态计算。"""

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
    def calculate(cls, klines: list, float_shares: float | None = None) -> dict:
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
        prev_boll = cls._boll(closes[:-1])
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
            "prev_boll": prev_boll,
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
            window = klines[idx - period + 1 : idx + 1]
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

    @classmethod
    def describe_ma(cls, values: dict, close: float) -> str:
        ma = values["ma"]
        ma_parts = []
        for period in (5, 10, 20, 60):
            value = ma.get(period)
            if value:
                pos = "上方" if close >= value else "下方"
                ma_parts.append(f"MA{period}{pos}({value:.2f})")
        return "均线：" + "，".join(ma_parts) if ma_parts else "均线：数据不足"

    @classmethod
    def describe_macd(cls, values: dict) -> str:
        macd = values["macd"]
        if macd["dif"] is not None and macd["dea"] is not None:
            axis = "零轴上方" if macd["dif"] >= 0 else "零轴下方"
            hist = macd["hist"]
            prev_hist = macd["prev_hist"]
            if hist is not None and prev_hist is not None:
                bar_change = "放大" if abs(hist) > abs(prev_hist) else "缩短"
                momentum = "动能增强" if abs(hist) > abs(prev_hist) else "动能减弱"
                bar = "红柱" if hist >= 0 else "绿柱"
                return f"MACD：{axis}，{bar}{bar_change}，{momentum}"
            if hist is not None:
                bar = "红柱" if hist >= 0 else "绿柱"
                return f"MACD：{axis}，{bar}"
        return "MACD：数据不足"

    @staticmethod
    def describe_rsi(values: dict) -> str:
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
            return f"RSI6：{rsi_state}({rsi:.1f})"
        return "RSI6：数据不足"

    @staticmethod
    def describe_kdj(values: dict) -> str:
        kdj = values.get("kdj")
        if kdj:
            if kdj["j"] >= 100:
                kdj_state = "超买钝化"
            elif kdj["j"] <= 0:
                kdj_state = "超卖钝化"
            else:
                kdj_state = "偏强" if kdj["k"] >= kdj["d"] else "偏弱"
            return f"KDJ：{kdj_state}(K {kdj['k']:.1f}, D {kdj['d']:.1f}, J {kdj['j']:.1f})"
        return "KDJ：数据不足"

    @staticmethod
    def describe_boll(values: dict, close: float) -> str:
        boll = values.get("boll")
        if boll:
            width = boll["upper"] - boll["lower"]
            prev_boll = values.get("prev_boll")
            width_state = ""
            if prev_boll:
                prev_width = prev_boll["upper"] - prev_boll["lower"]
                if prev_width:
                    change_ratio = (width - prev_width) / prev_width
                    if change_ratio >= 0.03:
                        width_state = "，波动扩张"
                    elif change_ratio <= -0.03:
                        width_state = "，波动收敛"
                    else:
                        width_state = "，波动持平"

            if close >= boll["upper"]:
                boll_state = "触及或突破上轨"
            elif close <= boll["lower"]:
                boll_state = "触及或跌破下轨"
            elif width and close >= boll["upper"] - width * 0.15:
                boll_state = "接近上轨"
            elif width and close <= boll["lower"] + width * 0.15:
                boll_state = "接近下轨"
            elif close >= boll["mid"]:
                boll_state = "位于中轨上方"
            else:
                boll_state = "位于中轨下方"
            return f"BOLL：{boll_state}{width_state}"
        return "BOLL：数据不足"

    @staticmethod
    def describe_volume(values: dict, volume: int) -> str:
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
            return f"成交量：{volume_state}({ratio:.2f}x 20日均量)"
        return "成交量：数据不足"

    @staticmethod
    def describe_volume_ratio(values: dict) -> str:
        ratio = values.get("volume_ratio")
        if ratio is None:
            return "量比：数据不足"
        if ratio >= 2:
            ratio_state = "短线显著放量"
        elif ratio >= 1.2:
            ratio_state = "短线放量"
        elif ratio <= 0.6:
            ratio_state = "短线缩量"
        else:
            ratio_state = "接近5日均量"
        return f"量比：{ratio:.2f}，{ratio_state}"

    @staticmethod
    def describe_turnover(values: dict) -> str:
        turnover_rate = values.get("turnover_rate")
        if turnover_rate is not None:
            if turnover_rate >= 15:
                turnover_state = "高位分歧"
            elif turnover_rate >= 5:
                turnover_state = "活跃"
            elif turnover_rate <= 1:
                turnover_state = "低换手"
            else:
                turnover_state = "正常"
            return f"换手率：{turnover_state}({turnover_rate:.2f}%)"
        return "换手率：流通股本未配置"

    @staticmethod
    def describe_amplitude(values: dict) -> str:
        amplitude = values.get("amplitude")
        if amplitude is not None:
            if amplitude >= 10:
                amplitude_state = "大幅波动"
            elif amplitude >= 5:
                amplitude_state = "波动放大"
            elif amplitude <= 2:
                amplitude_state = "窄幅震荡"
            else:
                amplitude_state = "正常波动"
            return f"振幅：{amplitude:.2f}%，{amplitude_state}"
        return "振幅：数据不足"

    @staticmethod
    def describe_atr(values: dict, close: float) -> str:
        atr = values.get("atr14")
        if atr is not None:
            atr_pct = atr / close * 100 if close else None
            if atr_pct is None:
                return f"ATR14：{atr:.2f}"
            if atr_pct >= 8:
                atr_state = "高波动"
            elif atr_pct >= 4:
                atr_state = "波动偏高"
            elif atr_pct <= 1.5:
                atr_state = "低波动"
            else:
                atr_state = "正常波动"
            return f"ATR14：{atr:.2f}({atr_pct:.2f}%)，{atr_state}"
        return "ATR14：数据不足"

    @classmethod
    def describe(cls, values: dict, close: float, volume: int) -> list:
        return [
            cls.describe_ma(values, close),
            cls.describe_macd(values),
            cls.describe_rsi(values),
            cls.describe_kdj(values),
            cls.describe_boll(values, close),
            cls.describe_volume(values, volume),
            cls.describe_volume_ratio(values),
            cls.describe_turnover(values),
            cls.describe_amplitude(values),
            cls.describe_atr(values, close),
        ]
