import os
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API_KEY = os.environ["TWELVE_DATA_API_KEY"]

TIMEOUT = 20
INTERVAL = "5min"
OUTPUT_SIZE = 100

SYMBOLS = {
    "XAUUSD": "XAU/USD",
    "EURUSD": "EUR/USD",
    "GBPUSD": "GBP/USD",
    "BTCUSD": "BTC/USD",
}


def decimals_for(symbol):
    if symbol in ("EURUSD", "GBPUSD"):
        return 5
    return 2


def rounded(symbol, value):
    if value is None:
        return None

    return round(
        float(value),
        decimals_for(symbol)
    )


def percent_change(old, new):
    if old == 0:
        return 0.0

    return ((new - old) / old) * 100.0


def ema(values, period):
    if len(values) < period:
        return None

    multiplier = 2.0 / (period + 1.0)

    value = (
        sum(values[:period])
        / period
    )

    for price in values[period:]:
        value = (
            price * multiplier
            +
            value * (1.0 - multiplier)
        )

    return value


def rsi(values, period=14):
    if len(values) < period + 1:
        return 50.0

    gains = []
    losses = []

    for i in range(1, len(values)):
        change = (
            values[i]
            -
            values[i - 1]
        )

        gains.append(
            max(change, 0.0)
        )

        losses.append(
            max(-change, 0.0)
        )

    avg_gain = (
        sum(gains[-period:])
        /
        period
    )

    avg_loss = (
        sum(losses[-period:])
        /
        period
    )

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss

    return (
        100.0
        -
        (
            100.0
            /
            (1.0 + rs)
        )
    )


def true_ranges(candles):
    values = []

    for i in range(1, len(candles)):
        current = candles[i]

        previous = candles[
            i - 1
        ]

        tr = max(
            current["high"]
            -
            current["low"],

            abs(
                current["high"]
                -
                previous["close"]
            ),

            abs(
                current["low"]
                -
                previous["close"]
            )
        )

        values.append(tr)

    return values


def atr(candles, period=14):
    values = true_ranges(
        candles
    )

    if not values:
        return 0.0

    recent = values[-period:]

    return (
        sum(recent)
        /
        len(recent)
    )


def support_resistance(
    candles,
    lookback=20
):

    recent = candles[
        -lookback:
    ]

    support = min(
        candle["low"]
        for candle in recent
    )

    resistance = max(
        candle["high"]
        for candle in recent
    )

    return (
        support,
        resistance
    )


def market_structure(candles):
    if len(candles) < 12:
        return "MIXED"

    old = candles[-12:-6]
    recent = candles[-6:]

    old_high = max(
        candle["high"]
        for candle in old
    )

    old_low = min(
        candle["low"]
        for candle in old
    )

    recent_high = max(
        candle["high"]
        for candle in recent
    )

    recent_low = min(
        candle["low"]
        for candle in recent
    )

    if (
        recent_high > old_high
        and
        recent_low > old_low
    ):
        return "BULLISH"

    if (
        recent_high < old_high
        and
        recent_low < old_low
    ):
        return "BEARISH"

    return "MIXED"


def candle_pattern(candles):
    if len(candles) < 3:
        return "NONE"

    previous = candles[-2]
    current = candles[-1]

    body = abs(
        current["close"]
        -
        current["open"]
    )

    candle_range = max(
        current["high"]
        -
        current["low"],
        0.000000000001
    )

    upper_wick = (
        current["high"]
        -
        max(
            current["open"],
            current["close"]
        )
    )

    lower_wick = (
        min(
            current["open"],
            current["close"]
        )
        -
        current["low"]
    )

    if (
        previous["close"]
        <
        previous["open"]
        and
        current["close"]
        >
        current["open"]
        and
        current["open"]
        <=
        previous["close"]
        and
        current["close"]
        >=
        previous["open"]
    ):
        return "BULLISH ENGULFING"

    if (
        previous["close"]
        >
        previous["open"]
        and
        current["close"]
        <
        current["open"]
        and
        current["open"]
        >=
        previous["close"]
        and
        current["close"]
        <=
        previous["open"]
    ):
        return "BEARISH ENGULFING"

    if (
        lower_wick > body * 2
        and
        lower_wick > upper_wick
        and
        body < candle_range * 0.45
    ):
        return "BULLISH REJECTION"

    if (
        upper_wick > body * 2
        and
        upper_wick > lower_wick
        and
        body < candle_range * 0.45
    ):
        return "BEARISH REJECTION"

    last_three = candles[-3:]

    if all(
        candle["close"]
        >
        candle["open"]
        for candle in last_three
    ):
        return "3 BULLISH CANDLES"

    if all(
        candle["close"]
        <
        candle["open"]
        for candle in last_three
    ):
        return "3 BEARISH CANDLES"

    return "NONE"


def market_regime(
    candles,
    current_atr,
    ema20,
    ema50
):

    ranges = true_ranges(
        candles
    )

    if len(ranges) < 30:
        return "NORMAL"

    historical = ranges[
        -40:-14
    ]

    if historical:
        average_atr = (
            sum(historical)
            /
            len(historical)
        )
    else:
        average_atr = current_atr

    if (
        average_atr > 0
        and
        current_atr
        >
        average_atr * 1.55
    ):
        return "HIGH VOLATILITY"

    ema_distance = abs(
        ema20 - ema50
    )

    if (
        current_atr > 0
        and
        ema_distance
        >
        current_atr * 0.70
    ):
        return "TRENDING"

    return "RANGING"


def momentum_analysis(
    closes,
    current_atr
):

    if len(closes) < 7:
        return (
            "WEAK",
            "NEUTRAL"
        )

    change = (
        closes[-1]
        -
        closes[-6]
    )

    absolute_change = abs(
        change
    )

    if current_atr <= 0:
        strength = "WEAK"

    elif (
        absolute_change
        >=
        current_atr * 2
    ):
        strength = "STRONG"

    elif (
        absolute_change
        >=
        current_atr
    ):
        strength = "MODERATE"

    else:
        strength = "WEAK"

    if change > 0:
        direction = "BULLISH"

    elif change < 0:
        direction = "BEARISH"

    else:
        direction = "NEUTRAL"

    return (
        strength,
        direction
    )


def signal_grade(confidence):
    if confidence >= 90:
        return "A+"

    if confidence >= 84:
        return "A"

    if confidence >= 78:
        return "B+"

    if confidence >= 70:
        return "B"

    if confidence >= 62:
        return "C"

    return "D"


def candle_age_minutes(
    candle_time
):

    try:
        candle_datetime = (
            datetime.strptime(
                candle_time,
                "%Y-%m-%d %H:%M:%S"
            )
            .replace(
                tzinfo=timezone.utc
            )
        )

        age_seconds = (
            datetime.now(
                timezone.utc
            )
            -
            candle_datetime
        ).total_seconds()

        return max(
            0,
            int(
                age_seconds / 60
            )
        )

    except Exception:
        return None


def get_data(symbol):

    params = urllib.parse.urlencode({
        "symbol": symbol,
        "interval": INTERVAL,
        "outputsize": OUTPUT_SIZE,
        "order": "ASC",
        "timezone": "UTC",
        "apikey": API_KEY
    })

    url = (
        "https://api.twelvedata.com/"
        "time_series?"
        +
        params
    )

    request = (
        urllib.request.Request(
            url,
            headers={
                "User-Agent":
                    "Mkayfx-V3"
            }
        )
    )

    with urllib.request.urlopen(
        request,
        timeout=TIMEOUT
    ) as response:

        data = json.loads(
            response
            .read()
            .decode(
                "utf-8"
            )
        )

    if "values" not in data:
        raise RuntimeError(
            data.get(
                "message",
                "No market data returned"
            )
        )

    candles = []

    for candle in data[
        "values"
    ]:

        candles.append({
            "datetime":
                candle["datetime"],

            "open":
                float(
                    candle["open"]
                ),

            "high":
                float(
                    candle["high"]
                ),

            "low":
                float(
                    candle["low"]
                ),

            "close":
                float(
                    candle["close"]
                )
        })

    if len(candles) < 60:
        raise RuntimeError(
            "Not enough candle data"
        )

    return candles


def analyse(
    name,
    api_symbol
):

    candles = get_data(
        api_symbol
    )

    closes = [
        candle["close"]
        for candle in candles
    ]

    price = closes[-1]

    current_ema20 = ema(
        closes,
        20
    )

    current_ema50 = ema(
        closes,
        50
    )

    current_rsi = rsi(
        closes,
        14
    )

    current_atr = atr(
        candles,
        14
    )

    (
        support,
        resistance
    ) = support_resistance(
        candles,
        20
    )

    structure = market_structure(
        candles
    )

    pattern = candle_pattern(
        candles
    )

    (
        momentum_strength,
        momentum_direction
    ) = momentum_analysis(
        closes,
        current_atr
    )

    regime = market_regime(
        candles,
        current_atr,
        current_ema20,
        current_ema50
    )

    change_5m = percent_change(
        closes[-2],
        closes[-1]
    )

    change_1h = percent_change(
        closes[-13],
        closes[-1]
    )

    bull_score = 0
    bear_score = 0

    bull_reasons = []
    bear_reasons = []


    if (
        current_ema20
        >
        current_ema50
    ):

        bull_score += 2

        bull_reasons.append(
            "EMA20 is above EMA50"
        )

    elif (
        current_ema20
        <
        current_ema50
    ):

        bear_score += 2

        bear_reasons.append(
            "EMA20 is below EMA50"
        )


    if (
        price
        >
        current_ema20
    ):

        bull_score += 1

        bull_reasons.append(
            "Price is above EMA20"
        )

    elif (
        price
        <
        current_ema20
    ):

        bear_score += 1

        bear_reasons.append(
            "Price is below EMA20"
        )


    if current_rsi >= 55:

        bull_score += 1

        bull_reasons.append(
            "RSI shows bullish pressure"
        )

    elif current_rsi <= 45:

        bear_score += 1

        bear_reasons.append(
            "RSI shows bearish pressure"
        )


    if (
        momentum_direction
        ==
        "BULLISH"
    ):

        bull_score += 1

        bull_reasons.append(
            momentum_strength
            +
            " bullish momentum"
        )

    elif (
        momentum_direction
        ==
        "BEARISH"
    ):

        bear_score += 1

        bear_reasons.append(
            momentum_strength
            +
            " bearish momentum"
        )


    if structure == "BULLISH":

        bull_score += 1

        bull_reasons.append(
            "Bullish market structure"
        )

    elif structure == "BEARISH":

        bear_score += 1

        bear_reasons.append(
            "Bearish market structure"
        )


    if "BULLISH" in pattern:

        bull_score += 1

        bull_reasons.append(
            pattern
        )

    elif "BEARISH" in pattern:

        bear_score += 1

        bear_reasons.append(
            pattern
        )


    difference = (
        bull_score
        -
        bear_score
    )


    if difference >= 2:
        signal = "BUY"

    elif difference <= -2:
        signal = "SELL"

    else:
        signal = "WAIT"


    confidence = (
        54
        +
        abs(
            difference
        ) * 7
    )


    if (
        regime == "TRENDING"
        and
        signal != "WAIT"
    ):
        confidence += 4


    if (
        momentum_strength
        ==
        "STRONG"
        and
        signal != "WAIT"
    ):
        confidence += 3


    confidence = min(
        95,
        int(confidence)
    )


    if signal == "WAIT":
        confidence = min(
            confidence,
            65
        )


    grade = signal_grade(
        confidence
    )


    if (
        price
        >
        current_ema20
        >
        current_ema50
    ):
        trend = "BULLISH"

    elif (
        price
        <
        current_ema20
        <
        current_ema50
    ):
        trend = "BEARISH"

    else:
        trend = "MIXED"


    entry = None
    stop_loss = None
    take_profit = None

    risk_reward = None
    sl_distance = None
    tp_distance = None


    if signal == "BUY":

        entry = price

        stop_loss = (
            price
            -
            current_atr
        )

        take_profit = (
            price
            +
            current_atr * 2
        )


    elif signal == "SELL":

        entry = price

        stop_loss = (
            price
            +
            current_atr
        )

        take_profit = (
            price
            -
            current_atr * 2
        )


    if entry is not None:

        sl_distance = abs(
            entry
            -
            stop_loss
        )

        tp_distance = abs(
            take_profit
            -
            entry
        )

        if sl_distance > 0:

            risk_reward = round(
                tp_distance
                /
                sl_distance,
                2
            )


    if price:
        atr_percent = (
            current_atr
            /
            price
        ) * 100

    else:
        atr_percent = 0


    last_candle_time = (
        candles[-1][
            "datetime"
        ]
    )


    age = candle_age_minutes(
        last_candle_time
    )


    if (
        age is not None
        and
        age <= 15
    ):
        freshness = "FRESH"

    else:
        freshness = "STALE"


    if signal == "BUY":
        reasons = bull_reasons

    elif signal == "SELL":
        reasons = bear_reasons

    else:
        reasons = [
            "Bullish and bearish evidence is too balanced",
            "Waiting for stronger confirmation"
        ]


    sparkline = [
        rounded(
            name,
            value
        )
        for value
        in closes[-24:]
    ]


    return {

        "symbol":
            name,

        "api_symbol":
            api_symbol,

        "price":
            rounded(
                name,
                price
            ),

        "signal":
            signal,

        "confidence":
            confidence,

        "grade":
            grade,

        "trend":
            trend,

        "regime":
            regime,

        "structure":
            structure,

        "momentum":
            momentum_strength,

        "momentum_direction":
            momentum_direction,

        "rsi":
            round(
                current_rsi,
                1
            ),

        "ema20":
            rounded(
                name,
                current_ema20
            ),

        "ema50":
            rounded(
                name,
                current_ema50
            ),

        "atr":
            rounded(
                name,
                current_atr
            ),

        "atr_percent":
            round(
                atr_percent,
                3
            ),

        "change_5m":
            round(
                change_5m,
                3
            ),

        "change_1h":
            round(
                change_1h,
                3
            ),

        "support":
            rounded(
                name,
                support
            ),

        "resistance":
            rounded(
                name,
                resistance
            ),

        "candle_pattern":
            pattern,

        "bull_score":
            bull_score,

        "bear_score":
            bear_score,

        "reasons":
            reasons[:5],

        "entry":
            rounded(
                name,
                entry
            ),

        "sl":
            rounded(
                name,
                stop_loss
            ),

        "tp":
            rounded(
                name,
                take_profit
            ),

        "sl_distance":
            rounded(
                name,
                sl_distance
            ),

        "tp_distance":
            rounded(
                name,
                tp_distance
            ),

        "risk_reward":
            risk_reward,

        "candle_time":
            last_candle_time,

        "candle_age_minutes":
            age,

        "freshness":
            freshness,

        "sparkline":
            sparkline
    }


results = []


for (
    name,
    api_symbol
) in SYMBOLS.items():

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


valid_results = [
    market
    for market in results
    if (
        market.get("signal")
        !=
        "ERROR"
    )
]


buy_markets = [
    market
    for market in valid_results
    if (
        market.get("signal")
        ==
        "BUY"
    )
]


sell_markets = [
    market
    for market in valid_results
    if (
        market.get("signal")
        ==
        "SELL"
    )
]


wait_markets = [
    market
    for market in valid_results
    if (
        market.get("signal")
        ==
        "WAIT"
    )
]


total_bull_score = sum(
    market.get(
        "bull_score",
        0
    )
    for market
    in valid_results
)


total_bear_score = sum(
    market.get(
        "bear_score",
        0
    )
    for market
    in valid_results
)


if (
    total_bull_score
    >
    total_bear_score
):

    overall_bias = "BULLISH"


elif (
    total_bear_score
    >
    total_bull_score
):

    overall_bias = "BEARISH"


else:

    overall_bias = "MIXED"


strongest_buy = max(
    buy_markets,
    key=lambda market:
        market[
            "confidence"
        ],
    default=None
)


strongest_sell = max(
    sell_markets,
    key=lambda market:
        market[
            "confidence"
        ],
    default=None
)


leaderboard = sorted(

    [
        {
            "symbol":
                market[
                    "symbol"
                ],

            "signal":
                market[
                    "signal"
                ],

            "confidence":
                market[
                    "confidence"
                ],

            "grade":
                market[
                    "grade"
                ]
        }

        for market
        in valid_results
    ],

    key=lambda market:
        market[
            "confidence"
        ],

    reverse=True
)


output = {

    "status":
        "LIVE",

    "version":
        "Mkayfx V3 Pro",

    "source":
        "Twelve Data",

    "interval":
        INTERVAL,

    "updated":
        datetime.now(
            timezone.utc
        ).isoformat(),

    "overall_bias":
        overall_bias,

    "bull_score":
        total_bull_score,

    "bear_score":
        total_bear_score,

    "summary": {

        "buy":
            len(
                buy_markets
            ),

        "sell":
            len(
                sell_markets
            ),

        "wait":
            len(
                wait_markets
            )
    },

    "strongest_buy":

        None
        if strongest_buy is None
        else {

            "symbol":
                strongest_buy[
                    "symbol"
                ],

            "confidence":
                strongest_buy[
                    "confidence"
                ],

            "grade":
                strongest_buy[
                    "grade"
                ]
        },

    "strongest_sell":

        None
        if strongest_sell is None
        else {

            "symbol":
                strongest_sell[
                    "symbol"
                ],

            "confidence":
                strongest_sell[
                    "confidence"
                ],

            "grade":
                strongest_sell[
                    "grade"
                ]
        },

    "leaderboard":
        leaderboard,

    "markets":
        results
}


with open(
    "data.json",
    "w",
    encoding="utf-8"
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