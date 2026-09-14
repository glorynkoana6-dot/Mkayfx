import os
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API_KEY = os.environ["TWELVE_DATA_API_KEY"]

SYMBOLS = {
    "XAUUSD": "XAU/USD",
    "EURUSD": "EUR/USD",
    "GBPUSD": "GBP/USD",
    "BTCUSD": "BTC/USD"
}


def ema(values, period):
    if len(values) < period:
        return None

    k = 2 / (period + 1)
    value = sum(values[:period]) / period

    for price in values[period:]:
        value = price * k + value * (1 - k)

    return value


def rsi(values, period=14):
    gains = []
    losses = []

    for i in range(1, len(values)):
        change = values[i] - values[i - 1]

        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    if len(gains) < period:
        return 50

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


def atr(candles, period=14):
    values = []

    for i in range(1, len(candles)):

        high = candles[i]["high"]
        low = candles[i]["low"]
        previous_close = candles[i - 1]["close"]

        tr = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close)
        )

        values.append(tr)

    if not values:
        return 0

    return sum(values[-period:]) / min(period, len(values))


def get_data(symbol):

    params = urllib.parse.urlencode({
        "symbol": symbol,
        "interval": "5min",
        "outputsize": 100,
        "order": "ASC",
        "timezone": "UTC",
        "apikey": API_KEY
    })

    url = (
        "https://api.twelvedata.com/time_series?"
        + params
    )

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mkayfx"}
    )

    with urllib.request.urlopen(
        request,
        timeout=20
    ) as response:

        data = json.loads(
            response.read().decode()
        )

    if "values" not in data:
        raise Exception(
            data.get("message", "No market data")
        )

    candles = []

    for candle in data["values"]:

        candles.append({

            "datetime": candle["datetime"],

            "open":
                float(candle["open"]),

            "high":
                float(candle["high"]),

            "low":
                float(candle["low"]),

            "close":
                float(candle["close"])

        })

    return candles


def analyse(name, api_symbol):

    candles = get_data(api_symbol)

    closes = [
        candle["close"]
        for candle in candles
    ]

    price = closes[-1]

    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)

    current_rsi = rsi(closes)

    current_atr = atr(candles)

    bull = 0
    bear = 0


    # TREND

    if ema20 > ema50:
        bull += 2

    elif ema20 < ema50:
        bear += 2


    # PRICE POSITION

    if price > ema20:
        bull += 1

    elif price < ema20:
        bear += 1


    # RSI

    if current_rsi > 55:
        bull += 1

    elif current_rsi < 45:
        bear += 1


    # MOMENTUM

    if closes[-1] > closes[-10]:
        bull += 1

    elif closes[-1] < closes[-10]:
        bear += 1


    # LAST 3 CANDLES

    recent = candles[-3:]

    bullish = all(
        c["close"] > c["open"]
        for c in recent
    )

    bearish = all(
        c["close"] < c["open"]
        for c in recent
    )

    if bullish:
        bull += 1

    if bearish:
        bear += 1


    difference = bull - bear


    if difference >= 2:

        signal = "BUY"

    elif difference <= -2:

        signal = "SELL"

    else:

        signal = "WAIT"


    confidence = min(
        92,
        54 + abs(difference) * 7
    )


    if signal == "WAIT":

        confidence = min(
            confidence,
            65
        )


    risk = current_atr


    if signal == "BUY":

        entry = price

        stop_loss = (
            price - risk
        )

        take_profit = (
            price + risk * 2
        )


    elif signal == "SELL":

        entry = price

        stop_loss = (
            price + risk
        )

        take_profit = (
            price - risk * 2
        )


    else:

        entry = None
        stop_loss = None
        take_profit = None


    if (
        price > ema20
        and ema20 > ema50
    ):

        trend = "BULLISH"


    elif (
        price < ema20
        and ema20 < ema50
    ):

        trend = "BEARISH"


    else:

        trend = "MIXED"


    decimals = (
        5
        if name in [
            "EURUSD",
            "GBPUSD"
        ]
        else 2
    )


    def rounded(value):

        if value is None:
            return None

        return round(
            value,
            decimals
        )


    return {

        "symbol":
            name,

        "price":
            rounded(price),

        "signal":
            signal,

        "confidence":
            confidence,

        "trend":
            trend,

        "rsi":
            round(
                current_rsi,
                1
            ),

        "entry":
            rounded(entry),

        "sl":
            rounded(stop_loss),

        "tp":
            rounded(take_profit),

        "candle_time":
            candles[-1]["datetime"]

    }


results = []


for name, api_symbol in SYMBOLS.items():

    try:

        results.append(
            analyse(
                name,
                api_symbol
            )
        )

    except Exception as error:

        results.append({

            "symbol":
                name,

            "signal":
                "ERROR",

            "error":
                str(error)

        })


buys = sum(
    1
    for item in results
    if item.get("signal") == "BUY"
)

sells = sum(
    1
    for item in results
    if item.get("signal") == "SELL"
)


if buys > sells:

    bias = "BULLISH"

elif sells > buys:

    bias = "BEARISH"

else:

    bias = "MIXED"


output = {

    "status":
        "LIVE",

    "source":
        "Twelve Data",

    "interval":
        "5min",

    "updated":
        datetime.now(
            timezone.utc
        ).isoformat(),

    "overall_bias":
        bias,

    "markets":
        results

}


with open(
    "data.json",
    "w"
) as file:

    json.dump(
        output,
        file,
        indent=2
    )


print(
    json.dumps(
        output,
        indent=2
    )
)
