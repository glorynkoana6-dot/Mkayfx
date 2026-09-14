import os
import json
import math
import html
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

API_KEY = os.environ["TWELVE_DATA_API_KEY"]

INTERVAL = "5min"
OUTPUT_SIZE = 100
TIMEOUT = 25
SIGNAL_EXPIRY_MINUTES = 20
MAX_TRACK_MINUTES = 240
HISTORY_FILE = "signal_history.json"

SYMBOLS = {
    "XAUUSD": "XAU/USD",
    "EURUSD": "EUR/USD",
    "GBPUSD": "GBP/USD",
    "BTCUSD": "BTC/USD",
}

CONTRACT_UNITS = {
    "XAUUSD": 100.0,
    "EURUSD": 100000.0,
    "GBPUSD": 100000.0,
    "BTCUSD": 1.0,
}


def utc_now():
    return datetime.now(timezone.utc)


def iso_now():
    return utc_now().isoformat()


def parse_dt(value):
    if not value:
        return None
    value = str(value).strip()
    try:
        if "T" in value:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def decimals_for(symbol):
    return 5 if symbol in ("EURUSD", "GBPUSD") else 2


def rounded(symbol, value):
    if value is None:
        return None
    return round(float(value), decimals_for(symbol))


def pct_change(old, new):
    if old in (0, None):
        return 0.0
    return ((new - old) / old) * 100.0


def ema(values, period):
    if len(values) < period:
        return None
    k = 2.0 / (period + 1.0)
    out = sum(values[:period]) / period
    for value in values[period:]:
        out = value * k + out * (1.0 - k)
    return out


def rsi(values, period=14):
    if len(values) < period + 1:
        return 50.0
    gains = []
    losses = []
    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def true_ranges(candles):
    out = []
    for i in range(1, len(candles)):
        c = candles[i]
        p = candles[i - 1]
        out.append(max(
            c["high"] - c["low"],
            abs(c["high"] - p["close"]),
            abs(c["low"] - p["close"]),
        ))
    return out


def atr(candles, period=14):
    values = true_ranges(candles)
    if not values:
        return 0.0
    recent = values[-period:]
    return sum(recent) / len(recent)


def resample(candles, factor):
    out = []
    for i in range(0, len(candles), factor):
        chunk = candles[i:i + factor]
        if len(chunk) < factor:
            continue
        out.append({
            "datetime": chunk[-1]["datetime"],
            "open": chunk[0]["open"],
            "high": max(c["high"] for c in chunk),
            "low": min(c["low"] for c in chunk),
            "close": chunk[-1]["close"],
        })
    return out


def timeframe_bias(candles):
    closes = [c["close"] for c in candles]
    if len(closes) < 12:
        return "MIXED"
    fast_period = min(9, max(3, len(closes) // 5))
    slow_period = min(20, max(6, len(closes) // 2))
    fast = ema(closes, fast_period)
    slow = ema(closes, slow_period)
    if fast is None or slow is None:
        return "MIXED"
    price = closes[-1]
    if price > fast > slow:
        return "BULLISH"
    if price < fast < slow:
        return "BEARISH"
    return "MIXED"


def support_resistance(candles, lookback=20):
    recent = candles[-lookback:]
    return (
        min(c["low"] for c in recent),
        max(c["high"] for c in recent),
    )


def market_structure(candles):
    if len(candles) < 16:
        return "MIXED"
    older = candles[-16:-8]
    recent = candles[-8:]
    oh = max(c["high"] for c in older)
    ol = min(c["low"] for c in older)
    rh = max(c["high"] for c in recent)
    rl = min(c["low"] for c in recent)
    if rh > oh and rl > ol:
        return "BULLISH"
    if rh < oh and rl < ol:
        return "BEARISH"
    return "MIXED"


def candle_pattern(candles):
    if len(candles) < 3:
        return "NONE"
    prev = candles[-2]
    cur = candles[-1]
    body = abs(cur["close"] - cur["open"])
    rng = max(cur["high"] - cur["low"], 1e-12)
    upper = cur["high"] - max(cur["open"], cur["close"])
    lower = min(cur["open"], cur["close"]) - cur["low"]

    if (
        prev["close"] < prev["open"]
        and cur["close"] > cur["open"]
        and cur["open"] <= prev["close"]
        and cur["close"] >= prev["open"]
    ):
        return "BULLISH ENGULFING"

    if (
        prev["close"] > prev["open"]
        and cur["close"] < cur["open"]
        and cur["open"] >= prev["close"]
        and cur["close"] <= prev["open"]
    ):
        return "BEARISH ENGULFING"

    if lower > body * 2 and lower > upper and body < rng * 0.45:
        return "BULLISH REJECTION"

    if upper > body * 2 and upper > lower and body < rng * 0.45:
        return "BEARISH REJECTION"

    last3 = candles[-3:]
    if all(c["close"] > c["open"] for c in last3):
        return "3 BULLISH CANDLES"
    if all(c["close"] < c["open"] for c in last3):
        return "3 BEARISH CANDLES"
    return "NONE"


def detect_fvg(candles):
    if len(candles) < 3:
        return "NONE"
    a, _, c = candles[-3], candles[-2], candles[-1]
    if c["low"] > a["high"]:
        return "BULLISH FVG"
    if c["high"] < a["low"]:
        return "BEARISH FVG"
    return "NONE"


def liquidity_sweep(candles, lookback=10):
    if len(candles) < lookback + 2:
        return "NONE"
    prior = candles[-(lookback + 1):-1]
    cur = candles[-1]
    prior_high = max(c["high"] for c in prior)
    prior_low = min(c["low"] for c in prior)

    if cur["high"] > prior_high and cur["close"] < prior_high:
        return "BEARISH SWEEP"
    if cur["low"] < prior_low and cur["close"] > prior_low:
        return "BULLISH SWEEP"
    return "NONE"


def break_of_structure(candles, lookback=20):
    if len(candles) < lookback + 2:
        return "NONE"
    prior = candles[-(lookback + 1):-1]
    cur = candles[-1]
    prior_high = max(c["high"] for c in prior)
    prior_low = min(c["low"] for c in prior)
    if cur["close"] > prior_high:
        return "BULLISH BOS"
    if cur["close"] < prior_low:
        return "BEARISH BOS"
    return "NONE"


def momentum_analysis(closes, current_atr):
    if len(closes) < 7:
        return "WEAK", "NEUTRAL"
    change = closes[-1] - closes[-6]
    absolute = abs(change)
    if current_atr <= 0:
        strength = "WEAK"
    elif absolute >= current_atr * 2:
        strength = "STRONG"
    elif absolute >= current_atr:
        strength = "MODERATE"
    else:
        strength = "WEAK"

    if change > 0:
        direction = "BULLISH"
    elif change < 0:
        direction = "BEARISH"
    else:
        direction = "NEUTRAL"
    return strength, direction


def regime(candles, current_atr, ema20, ema50):
    ranges = true_ranges(candles)
    baseline = ranges[-40:-14]
    avg = sum(baseline) / len(baseline) if baseline else current_atr
    if avg > 0 and current_atr > avg * 1.55:
        return "HIGH VOLATILITY"
    if current_atr > 0 and abs(ema20 - ema50) > current_atr * 0.70:
        return "TRENDING"
    return "RANGING"


def trend_strength(current_atr, ema9, ema20, ema50):
    if current_atr <= 0:
        return 0
    spread = abs(ema9 - ema20) + abs(ema20 - ema50)
    score = int(round((spread / current_atr) * 35))
    return max(0, min(100, score))


def grade(confidence):
    if confidence >= 92:
        return "A+"
    if confidence >= 86:
        return "A"
    if confidence >= 80:
        return "B+"
    if confidence >= 72:
        return "B"
    if confidence >= 64:
        return "C"
    return "D"


def classify_risk(regime_name, atr_percent):
    if regime_name == "HIGH VOLATILITY" or atr_percent >= 0.7:
        return "HIGH"
    if regime_name == "TRENDING" or atr_percent >= 0.25:
        return "MEDIUM"
    return "LOW"


def classify_setup(signal, regime_name, bos, sweep, fvg, structure):
    if signal == "WAIT":
        return "NO SETUP"
    side = "BULLISH" if signal == "BUY" else "BEARISH"
    if side in bos:
        return "BREAKOUT"
    if side in sweep:
        return "LIQUIDITY REVERSAL"
    if side in fvg:
        return "FVG CONTINUATION"
    if regime_name == "TRENDING" and structure == side:
        return "TREND CONTINUATION"
    if regime_name == "RANGING":
        return "RANGE SETUP"
    return "CONFLUENCE"


def candle_age_minutes(candle_time):
    dt = parse_dt(candle_time)
    if not dt:
        return None
    return max(0, int((utc_now() - dt).total_seconds() / 60))


def fetch_market(symbol):
    params = urllib.parse.urlencode({
        "symbol": symbol,
        "interval": INTERVAL,
        "outputsize": OUTPUT_SIZE,
        "order": "ASC",
        "timezone": "UTC",
        "apikey": API_KEY,
    })
    url = "https://api.twelvedata.com/time_series?" + params
    req = urllib.request.Request(url, headers={"User-Agent": "Mkayfx-V4-Max"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
        data = json.loads(response.read().decode("utf-8"))
    if "values" not in data:
        raise RuntimeError(data.get("message", "No market data returned"))

    candles = []
    for c in data["values"]:
        candles.append({
            "datetime": c["datetime"],
            "open": float(c["open"]),
            "high": float(c["high"]),
            "low": float(c["low"]),
            "close": float(c["close"]),
        })
    if len(candles) < 60:
        raise RuntimeError("Not enough candle data")
    return candles


def fetch_news():
    query = urllib.parse.quote(
        '"Federal Reserve" OR inflation OR CPI OR NFP OR payrolls OR gold OR bitcoin OR forex'
    )
    url = (
        "https://news.google.com/rss/search?q="
        + query
        + "&hl=en-US&gl=US&ceid=US:en"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 Mkayfx"})
        with urllib.request.urlopen(req, timeout=12) as response:
            root = ET.fromstring(response.read())
        items = []
        high_words = (
            "federal reserve", "fomc", "powell", "interest rate", "rate decision",
            "inflation", "cpi", "nonfarm", "payroll", "jobs report", "war",
        )
        medium_words = (
            "gold", "bitcoin", "dollar", "forex", "treasury", "yield", "tariff",
            "oil", "employment", "gdp",
        )
        for item in root.findall(".//item")[:8]:
            title = html.unescape((item.findtext("title") or "").strip())
            link = (item.findtext("link") or "").strip()
            published = (item.findtext("pubDate") or "").strip()
            lower = title.lower()
            if any(word in lower for word in high_words):
                impact = "HIGH"
            elif any(word in lower for word in medium_words):
                impact = "MEDIUM"
            else:
                impact = "LOW"
            items.append({
                "title": title,
                "link": link,
                "published": published,
                "impact": impact,
            })
        return items
    except Exception as exc:
        return [{
            "title": "News feed temporarily unavailable",
            "link": "",
            "published": "",
            "impact": "LOW",
            "error": str(exc)[:120],
        }]


def analyse(name, api_symbol):
    candles = fetch_market(api_symbol)
    closes = [c["close"] for c in candles]
    price = closes[-1]

    ema9 = ema(closes, 9)
    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    current_rsi = rsi(closes, 14)
    current_atr = atr(candles, 14)
    atr_pct = (current_atr / price * 100.0) if price else 0.0

    support20, resistance20 = support_resistance(candles, 20)
    support50, resistance50 = support_resistance(candles, 50)

    structure = market_structure(candles)
    pattern = candle_pattern(candles)
    fvg = detect_fvg(candles)
    sweep = liquidity_sweep(candles)
    bos = break_of_structure(candles)
    mom_strength, mom_direction = momentum_analysis(closes, current_atr)
    regime_name = regime(candles, current_atr, ema20, ema50)

    candles15 = resample(candles, 3)
    candles60 = resample(candles, 12)
    tf5 = timeframe_bias(candles)
    tf15 = timeframe_bias(candles15)
    tf60 = timeframe_bias(candles60)

    bull = 0
    bear = 0
    bull_reasons = []
    bear_reasons = []
    breakdown = []

    def add(side, points, reason):
        nonlocal bull, bear
        if side == "BULL":
            bull += points
            bull_reasons.append(reason)
            breakdown.append({"side": "BULL", "points": points, "reason": reason})
        else:
            bear += points
            bear_reasons.append(reason)
            breakdown.append({"side": "BEAR", "points": points, "reason": reason})

    if ema20 > ema50:
        add("BULL", 2, "EMA20 above EMA50")
    elif ema20 < ema50:
        add("BEAR", 2, "EMA20 below EMA50")

    if price > ema9 > ema20:
        add("BULL", 1, "Price above EMA9 and EMA20")
    elif price < ema9 < ema20:
        add("BEAR", 1, "Price below EMA9 and EMA20")

    if current_rsi >= 56:
        add("BULL", 1, "RSI bullish pressure")
    elif current_rsi <= 44:
        add("BEAR", 1, "RSI bearish pressure")

    if mom_direction == "BULLISH":
        add("BULL", 1, f"{mom_strength} bullish momentum")
    elif mom_direction == "BEARISH":
        add("BEAR", 1, f"{mom_strength} bearish momentum")

    if structure == "BULLISH":
        add("BULL", 1, "Bullish structure")
    elif structure == "BEARISH":
        add("BEAR", 1, "Bearish structure")

    if "BULLISH" in pattern:
        add("BULL", 1, pattern)
    elif "BEARISH" in pattern:
        add("BEAR", 1, pattern)

    if "BULLISH" in fvg:
        add("BULL", 1, fvg)
    elif "BEARISH" in fvg:
        add("BEAR", 1, fvg)

    if "BULLISH" in sweep:
        add("BULL", 2, sweep)
    elif "BEARISH" in sweep:
        add("BEAR", 2, sweep)

    if "BULLISH" in bos:
        add("BULL", 2, bos)
    elif "BEARISH" in bos:
        add("BEAR", 2, bos)

    for tf_name, tf_value in (("15M", tf15), ("1H", tf60)):
        if tf_value == "BULLISH":
            add("BULL", 1, f"{tf_name} trend bullish")
        elif tf_value == "BEARISH":
            add("BEAR", 1, f"{tf_name} trend bearish")

    diff = bull - bear
    if diff >= 3:
        signal = "BUY"
    elif diff <= -3:
        signal = "SELL"
    else:
        signal = "WAIT"

    alignment = sum(1 for value in (tf5, tf15, tf60) if value == ("BULLISH" if signal == "BUY" else "BEARISH"))
    strength = trend_strength(current_atr, ema9, ema20, ema50)

    confidence = 50 + abs(diff) * 5 + alignment * 3
    if regime_name == "TRENDING" and signal != "WAIT":
        confidence += 3
    if mom_strength == "STRONG" and signal != "WAIT":
        confidence += 2
    confidence = min(97, int(confidence))
    if signal == "WAIT":
        confidence = min(confidence, 64)

    entry = sl = tp1 = tp2 = None
    rr = None
    if signal == "BUY":
        entry = price
        structural_risk = max(current_atr, price - (support20 - current_atr * 0.10))
        risk = min(structural_risk, current_atr * 1.50)
        sl = entry - risk
        tp1 = entry + risk
        tp2 = entry + risk * 2
        rr = 2.0
    elif signal == "SELL":
        entry = price
        structural_risk = max(current_atr, (resistance20 + current_atr * 0.10) - price)
        risk = min(structural_risk, current_atr * 1.50)
        sl = entry + risk
        tp1 = entry - risk
        tp2 = entry - risk * 2
        rr = 2.0
    else:
        risk = None

    risk_level = classify_risk(regime_name, atr_pct)
    setup_type = classify_setup(signal, regime_name, bos, sweep, fvg, structure)

    last_time = candles[-1]["datetime"]
    age = candle_age_minutes(last_time)
    freshness = "FRESH" if age is not None and age <= 15 else "STALE"

    expires_at = None
    if signal in ("BUY", "SELL"):
        candle_dt = parse_dt(last_time)
        if candle_dt:
            expires_at = (candle_dt + timedelta(minutes=SIGNAL_EXPIRY_MINUTES)).isoformat()

    if signal == "BUY":
        reasons = bull_reasons[:7]
    elif signal == "SELL":
        reasons = bear_reasons[:7]
    else:
        reasons = ["Bull and bear evidence are too balanced", "Waiting for stronger confluence"]

    score_max = max(bull, bear, 1)
    dominance = round(abs(diff) / score_max * 100.0, 1)

    distance_support_pct = pct_change(price, support20)
    distance_resistance_pct = pct_change(price, resistance20)

    return {
        "symbol": name,
        "api_symbol": api_symbol,
        "price": rounded(name, price),
        "signal": signal,
        "confidence": confidence,
        "grade": grade(confidence),
        "setup_type": setup_type,
        "risk_level": risk_level,
        "trend": tf5,
        "trend_15m": tf15,
        "trend_1h": tf60,
        "trend_strength": strength,
        "alignment": alignment,
        "regime": regime_name,
        "structure": structure,
        "momentum": mom_strength,
        "momentum_direction": mom_direction,
        "rsi": round(current_rsi, 1),
        "rsi_state": "OVERBOUGHT" if current_rsi >= 70 else "OVERSOLD" if current_rsi <= 30 else "NEUTRAL",
        "ema9": rounded(name, ema9),
        "ema20": rounded(name, ema20),
        "ema50": rounded(name, ema50),
        "atr": rounded(name, current_atr),
        "atr_percent": round(atr_pct, 3),
        "change_5m": round(pct_change(closes[-2], closes[-1]), 3),
        "change_15m": round(pct_change(closes[-4], closes[-1]), 3),
        "change_1h": round(pct_change(closes[-13], closes[-1]), 3),
        "change_4h": round(pct_change(closes[-49], closes[-1]), 3),
        "support": rounded(name, support20),
        "resistance": rounded(name, resistance20),
        "major_support": rounded(name, support50),
        "major_resistance": rounded(name, resistance50),
        "distance_support_pct": round(distance_support_pct, 3),
        "distance_resistance_pct": round(distance_resistance_pct, 3),
        "candle_pattern": pattern,
        "fvg": fvg,
        "liquidity_sweep": sweep,
        "bos": bos,
        "bull_score": bull,
        "bear_score": bear,
        "dominance": dominance,
        "score_breakdown": breakdown,
        "reasons": reasons,
        "entry": rounded(name, entry),
        "sl": rounded(name, sl),
        "tp1": rounded(name, tp1),
        "tp": rounded(name, tp2),
        "risk_reward": rr,
        "risk_distance": rounded(name, risk if signal in ("BUY", "SELL") else None),
        "contract_units_per_lot": CONTRACT_UNITS[name],
        "signal_expiry_minutes": SIGNAL_EXPIRY_MINUTES,
        "expires_at": expires_at,
        "candle_time": last_time,
        "candle_age_minutes": age,
        "freshness": freshness,
        "sparkline": [rounded(name, v) for v in closes[-36:]],
        "_candles": candles,
    }


def load_history():
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("bad history")
        data.setdefault("trades", [])
        data.setdefault("last_signal", {})
        return data
    except Exception:
        return {"trades": [], "last_signal": {}}


def evaluate_open_trade(trade, candles, symbol):
    if trade.get("status") != "OPEN":
        return trade

    opened = parse_dt(trade.get("opened_candle_time")) or parse_dt(trade.get("opened_at"))
    if not opened:
        return trade

    relevant = []
    for c in candles:
        cdt = parse_dt(c["datetime"])
        if cdt and cdt > opened:
            relevant.append(c)

    entry = float(trade["entry"])
    sl = float(trade["sl"])
    tp = float(trade["tp"])
    side = trade["signal"]

    for c in relevant:
        if side == "BUY":
            hit_sl = c["low"] <= sl
            hit_tp = c["high"] >= tp
        else:
            hit_sl = c["high"] >= sl
            hit_tp = c["low"] <= tp

        if hit_sl and hit_tp:
            trade["status"] = "AMBIGUOUS"
            trade["closed_at"] = c["datetime"]
            trade["r_result"] = 0.0
            return trade
        if hit_sl:
            trade["status"] = "LOSS"
            trade["closed_at"] = c["datetime"]
            trade["r_result"] = -1.0
            return trade
        if hit_tp:
            trade["status"] = "WIN"
            trade["closed_at"] = c["datetime"]
            trade["r_result"] = 2.0
            return trade

    opened_at = parse_dt(trade.get("opened_at"))
    if opened_at and (utc_now() - opened_at).total_seconds() / 60 >= MAX_TRACK_MINUTES:
        last = candles[-1]["close"]
        risk = abs(entry - sl)
        if risk > 0:
            move = (last - entry) if side == "BUY" else (entry - last)
            r_value = move / risk
        else:
            r_value = 0.0
        trade["status"] = "EXPIRED"
        trade["closed_at"] = candles[-1]["datetime"]
        trade["r_result"] = round(r_value, 2)
        trade["close_price"] = rounded(symbol, last)

    return trade


def performance_summary(trades):
    wins = [t for t in trades if t.get("status") == "WIN"]
    losses = [t for t in trades if t.get("status") == "LOSS"]
    ambiguous = [t for t in trades if t.get("status") == "AMBIGUOUS"]
    open_trades = [t for t in trades if t.get("status") == "OPEN"]
    expired = [t for t in trades if t.get("status") == "EXPIRED"]

    resolved = wins + losses
    win_rate = round(len(wins) / len(resolved) * 100.0, 1) if resolved else 0.0
    total_r = round(sum(float(t.get("r_result", 0.0) or 0.0) for t in trades if t.get("status") != "AMBIGUOUS"), 2)
    positive = sum(max(0.0, float(t.get("r_result", 0.0) or 0.0)) for t in trades)
    negative = abs(sum(min(0.0, float(t.get("r_result", 0.0) or 0.0)) for t in trades))
    profit_factor = round(positive / negative, 2) if negative > 0 else (999.0 if positive > 0 else 0.0)

    closed_sorted = [t for t in trades if t.get("status") in ("WIN", "LOSS")]
    streak_type = None
    streak = 0
    if closed_sorted:
        latest_type = closed_sorted[-1]["status"]
        for t in reversed(closed_sorted):
            if t["status"] == latest_type:
                streak += 1
            else:
                break
        streak_type = latest_type

    by_symbol = {}
    for symbol in SYMBOLS:
        s = [t for t in trades if t.get("symbol") == symbol]
        sw = sum(1 for t in s if t.get("status") == "WIN")
        sl = sum(1 for t in s if t.get("status") == "LOSS")
        sr = sw + sl
        by_symbol[symbol] = {
            "trades": len(s),
            "wins": sw,
            "losses": sl,
            "win_rate": round(sw / sr * 100.0, 1) if sr else 0.0,
            "total_r": round(sum(float(t.get("r_result", 0.0) or 0.0) for t in s if t.get("status") != "AMBIGUOUS"), 2),
        }

    return {
        "tracked": len(trades),
        "open": len(open_trades),
        "wins": len(wins),
        "losses": len(losses),
        "expired": len(expired),
        "ambiguous": len(ambiguous),
        "win_rate": win_rate,
        "total_r": total_r,
        "profit_factor": profit_factor,
        "streak": streak,
        "streak_type": streak_type,
        "by_symbol": by_symbol,
    }


def strip_internal(market):
    result = dict(market)
    result.pop("_candles", None)
    return result


history = load_history()
results = []

for name, api_symbol in SYMBOLS.items():
    try:
        market = analyse(name, api_symbol)
        results.append(market)
    except Exception as exc:
        results.append({
            "symbol": name,
            "signal": "ERROR",
            "error": str(exc),
        })

# Evaluate previously tracked signals.
for trade in history["trades"]:
    symbol = trade.get("symbol")
    market = next((m for m in results if m.get("symbol") == symbol and "_candles" in m), None)
    if market:
        evaluate_open_trade(trade, market["_candles"], symbol)

# Create a new tracked trade only when a symbol changes into BUY or SELL.
for market in results:
    symbol = market.get("symbol")
    signal = market.get("signal")
    if signal == "ERROR":
        continue

    previous_signal = history["last_signal"].get(symbol)
    if signal in ("BUY", "SELL") and previous_signal != signal:
        existing_open = any(
            t.get("symbol") == symbol and t.get("status") == "OPEN"
            for t in history["trades"]
        )
        if not existing_open and market.get("entry") is not None:
            history["trades"].append({
                "id": f"{symbol}-{int(utc_now().timestamp())}",
                "symbol": symbol,
                "signal": signal,
                "grade": market["grade"],
                "confidence": market["confidence"],
                "setup_type": market["setup_type"],
                "entry": market["entry"],
                "sl": market["sl"],
                "tp": market["tp"],
                "opened_at": iso_now(),
                "opened_candle_time": market["candle_time"],
                "status": "OPEN",
                "r_result": None,
            })

    history["last_signal"][symbol] = signal

history["trades"] = history["trades"][-500:]

with open(HISTORY_FILE, "w", encoding="utf-8") as f:
    json.dump(history, f, indent=2)

valid = [m for m in results if m.get("signal") != "ERROR"]
buy = [m for m in valid if m.get("signal") == "BUY"]
sell = [m for m in valid if m.get("signal") == "SELL"]
wait = [m for m in valid if m.get("signal") == "WAIT"]

total_bull = sum(m.get("bull_score", 0) for m in valid)
total_bear = sum(m.get("bear_score", 0) for m in valid)

if total_bull > total_bear:
    overall_bias = "BULLISH"
elif total_bear > total_bull:
    overall_bias = "BEARISH"
else:
    overall_bias = "MIXED"

def strongest(items):
    if not items:
        return None
    m = max(items, key=lambda x: x.get("confidence", 0))
    return {
        "symbol": m["symbol"],
        "confidence": m["confidence"],
        "grade": m["grade"],
        "setup_type": m["setup_type"],
    }

leaderboard = sorted(
    [
        {
            "symbol": m["symbol"],
            "signal": m["signal"],
            "confidence": m["confidence"],
            "grade": m["grade"],
            "trend_strength": m["trend_strength"],
            "risk_level": m["risk_level"],
        }
        for m in valid
    ],
    key=lambda x: (x["confidence"], x["trend_strength"]),
    reverse=True,
)

perf = performance_summary(history["trades"])
recent_trades = list(reversed(history["trades"][-20:]))

market_risk = "LOW"
if any(m.get("risk_level") == "HIGH" for m in valid):
    market_risk = "HIGH"
elif any(m.get("risk_level") == "MEDIUM" for m in valid):
    market_risk = "MEDIUM"

output = {
    "status": "LIVE",
    "version": "Mkayfx V4 Max",
    "source": "Twelve Data",
    "interval": INTERVAL,
    "updated": iso_now(),
    "overall_bias": overall_bias,
    "market_risk": market_risk,
    "bull_score": total_bull,
    "bear_score": total_bear,
    "summary": {
        "buy": len(buy),
        "sell": len(sell),
        "wait": len(wait),
        "errors": len(results) - len(valid),
    },
    "strongest_buy": strongest(buy),
    "strongest_sell": strongest(sell),
    "leaderboard": leaderboard,
    "performance": perf,
    "recent_trades": recent_trades,
    "news": fetch_news(),
    "markets": [strip_internal(m) for m in results],
}

with open("data.json", "w", encoding="utf-8") as f:
    json.dump(output, f, indent=2)

print(json.dumps(output, indent=2))
