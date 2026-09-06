"""S&P 500 volatility scanner.

Pulls the current S&P 500 constituent list from Wikipedia, downloads one year of
daily prices with yfinance, computes realized-volatility metrics for every
ticker, and writes the ranked result to data/scan.json.

Run directly:  .venv/bin/python scan.py [--no-iv] [--refresh-list]
"""

from __future__ import annotations

import io
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf

import options

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
CONSTITUENTS_FILE = DATA_DIR / "constituents.json"
SCAN_FILE = DATA_DIR / "scan.json"

WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
TRADING_DAYS = 252
WINDOWS = {"vol20": 20, "vol60": 60, "vol120": 120, "vol250": 250}


# --------------------------------------------------------------------------- #
# Constituents
# --------------------------------------------------------------------------- #
def fetch_constituents(force: bool = False) -> list[dict]:
    """Return [{symbol, yf_symbol, name, sector, industry}], cached for a day."""
    if not force and CONSTITUENTS_FILE.exists():
        age = time.time() - CONSTITUENTS_FILE.stat().st_mtime
        if age < 86400:
            return json.loads(CONSTITUENTS_FILE.read_text())

    resp = requests.get(WIKI_URL, headers={"User-Agent": "volscan/0.1"}, timeout=30)
    resp.raise_for_status()
    tables = pd.read_html(io.StringIO(resp.text))
    table = next(t for t in tables if "Symbol" in t.columns and "Security" in t.columns)

    rows = []
    for _, r in table.iterrows():
        symbol = str(r["Symbol"]).strip()
        rows.append(
            {
                "symbol": symbol,
                # Wikipedia writes BRK.B / BF.B; Yahoo wants BRK-B / BF-B.
                "yf_symbol": symbol.replace(".", "-"),
                "name": str(r["Security"]).strip(),
                "sector": str(r.get("GICS Sector", "")).strip(),
                "industry": str(r.get("GICS Sub-Industry", "")).strip(),
            }
        )
    if len(rows) < 450:
        raise RuntimeError(f"Only parsed {len(rows)} constituents; Wikipedia layout changed?")

    DATA_DIR.mkdir(exist_ok=True)
    CONSTITUENTS_FILE.write_text(json.dumps(rows, indent=1))
    return rows


# --------------------------------------------------------------------------- #
# Prices and metrics
# --------------------------------------------------------------------------- #
def download_prices(yf_symbols: list[str], log=print) -> pd.DataFrame:
    """Download ~1y of daily OHLC for all tickers. Returns MultiIndex columns."""
    log(f"Downloading 1y daily bars for {len(yf_symbols)} tickers...")
    df = yf.download(
        yf_symbols,
        period="1y",
        interval="1d",
        auto_adjust=True,
        group_by="ticker",
        threads=True,
        progress=False,
    )
    if df.empty:
        raise RuntimeError("yfinance returned no data")
    return df


def _annualized_vol(log_returns: pd.Series, window: int) -> float | None:
    tail = log_returns.dropna().tail(window)
    # Require at least 80% of the window so a fresh listing doesn't get a noisy number.
    if len(tail) < max(10, int(window * 0.8)):
        return None
    return float(tail.std(ddof=1) * np.sqrt(TRADING_DAYS) * 100)


def compute_metrics(prices: pd.DataFrame, yf_symbol: str) -> dict | None:
    try:
        frame = prices[yf_symbol]
    except KeyError:
        return None
    frame = frame.dropna(subset=["Close"])
    if len(frame) < 25:
        return None

    close = frame["Close"]
    high = frame["High"]
    low = frame["Low"]
    prev_close = close.shift(1)
    log_ret = np.log(close / prev_close)

    metrics: dict = {}
    for key, window in WINDOWS.items():
        metrics[key] = _annualized_vol(log_ret, window)

    # Average True Range as a percent of price (20-day), a range-based vol measure.
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr20 = tr.tail(20).mean()
    metrics["atr20_pct"] = float(atr20 / close.iloc[-1] * 100) if close.iloc[-1] else None

    # Biggest single-day move in the last 60 sessions.
    recent = log_ret.dropna().tail(60)
    metrics["max_move60_pct"] = (
        float((np.exp(recent.abs().max()) - 1) * 100) if len(recent) else None
    )

    def pct_change(n: int) -> float | None:
        if len(close) <= n:
            return None
        base = close.iloc[-1 - n]
        return float((close.iloc[-1] / base - 1) * 100) if base else None

    metrics["ret5_pct"] = pct_change(5)
    metrics["ret20_pct"] = pct_change(20)
    metrics["ret250_pct"] = pct_change(min(250, len(close) - 1))
    metrics["last"] = float(close.iloc[-1])
    metrics["last_date"] = close.index[-1].strftime("%Y-%m-%d")

    # 30-point sparkline of closes, normalised to the first point.
    spark = close.tail(60)
    metrics["spark"] = [round(float(v / spark.iloc[0]), 4) for v in spark]
    return metrics


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run_scan(log=print, force_constituents: bool = False, with_iv: bool = True) -> dict:
    started = time.time()
    constituents = fetch_constituents(force=force_constituents)
    log(f"{len(constituents)} S&P 500 constituents")

    prices = download_prices([c["yf_symbol"] for c in constituents], log=log)

    rows, missing = [], []
    for c in constituents:
        m = compute_metrics(prices, c["yf_symbol"])
        if m is None:
            missing.append(c["symbol"])
            continue
        rows.append({**c, **m})

    if with_iv:
        log("Fetching option chains for implied volatility (this takes a few minutes)...")
        ivs = options.fetch_all(
            [r["yf_symbol"] for r in rows],
            spots={r["yf_symbol"]: r["last"] for r in rows},
            workers=8,
            log=log,
        )
        for r in rows:
            iv = ivs.get(r["yf_symbol"]) or {}
            r["iv30"] = iv.get("iv30")
            r["iv_expiries"] = iv.get("iv_expiries")
            r["iv_n"] = iv.get("iv_n")
            r["iv_reliable"] = iv.get("iv_reliable", False)
            # IV relative to recent realized vol: >1 means options price more
            # movement than the stock has actually delivered lately.
            r["iv_rv"] = (
                round(r["iv30"] / r["vol20"], 2) if r["iv30"] and r.get("vol20") else None
            )
            r["iv_minus_rv"] = (
                round(r["iv30"] - r["vol20"], 1) if r["iv30"] and r.get("vol20") else None
            )
    else:
        for r in rows:
            r["iv30"] = r["iv_expiries"] = r["iv_rv"] = r["iv_minus_rv"] = r["iv_n"] = None
            r["iv_reliable"] = False

    # Default ranking: 20-day realized vol (most responsive to current conditions).
    rows.sort(key=lambda r: (r["vol20"] is None, -(r["vol20"] or 0)))
    for i, r in enumerate(rows, 1):
        r["rank"] = i

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "duration_s": round(time.time() - started, 1),
        "universe": len(constituents),
        "scanned": len(rows),
        "missing": missing,
        "iv_count": sum(1 for r in rows if r["iv30"] is not None),
        "iv_reliable_count": sum(1 for r in rows if r["iv_reliable"]),
        "rows": rows,
    }
    DATA_DIR.mkdir(exist_ok=True)
    SCAN_FILE.write_text(json.dumps(result))
    log(f"Done: {len(rows)} scanned, {len(missing)} missing, {result['duration_s']}s")
    return result


if __name__ == "__main__":
    force = "--refresh-list" in sys.argv
    run_scan(force_constituents=force, with_iv="--no-iv" not in sys.argv)
