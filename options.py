"""30-day at-the-money implied volatility from Yahoo Finance option chains.

For each ticker: pick the two listed expiries that bracket 30 calendar days
(at least MIN_DTE days out), take the median IV of the at-the-money calls and
puts on each, and interpolate total variance to exactly 30 days. Falls back to
the single nearest expiry when only one side exists.
"""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime

import pandas as pd
import yfinance as yf

TARGET_DTE = 30
MIN_DTE = 5
ATM_STRIKES = 2  # strikes on each side of spot, per option type
MAX_REL_SPREAD = 0.6  # (ask - bid) / mid above this = untradeable quote
MAX_STALE_DAYS = 14  # ignore contracts that have not traded recently
MIN_CONTRACTS = 2  # per expiry, after filtering
GOOD_CONTRACTS = 6  # across both expiries, for the "reliable" flag
MAX_PUT_CALL_GAP = 0.35  # relative gap between ATM call IV and put IV


def _pick_expiries(expiries: list[str], today: date) -> list[tuple[str, int]]:
    dted = []
    for e in expiries:
        try:
            dte = (datetime.strptime(e, "%Y-%m-%d").date() - today).days
        except ValueError:
            continue
        if dte >= MIN_DTE:
            dted.append((e, dte))
    if not dted:
        return []
    below = [x for x in dted if x[1] <= TARGET_DTE]
    above = [x for x in dted if x[1] > TARGET_DTE]
    picks = []
    if below:
        picks.append(max(below, key=lambda x: x[1]))
    if above:
        picks.append(min(above, key=lambda x: x[1]))
    return picks


def _atm_iv(chain, spot: float, today: date) -> dict | None:
    """Median ATM IV for one expiry, using only live two-sided quotes.

    Returns {'iv', 'n', 'call_iv', 'put_iv'} or None if too few usable contracts.
    """
    per_type = {}
    for name, df in (("call", chain.calls), ("put", chain.puts)):
        if df is None or df.empty:
            continue
        df = df.copy()
        mid = (df["bid"] + df["ask"]) / 2
        traded = pd.to_datetime(df["lastTradeDate"], utc=True, errors="coerce")
        age_days = (pd.Timestamp(today, tz="UTC") - traded).dt.days
        ok = (
            (df["impliedVolatility"] > 0.02)
            & (df["impliedVolatility"] < 4)
            & (df["bid"] > 0)
            & (df["ask"] > df["bid"])
            & ((df["ask"] - df["bid"]) / mid <= MAX_REL_SPREAD)
            & (age_days <= MAX_STALE_DAYS)
        )
        df = df[ok]
        if df.empty:
            continue
        df["dist"] = (df["strike"] - spot).abs()
        # Keep strikes within 10% of spot only; further out is not "ATM".
        df = df[df["dist"] <= spot * 0.10].nsmallest(ATM_STRIKES * 2, "dist")
        if not df.empty:
            per_type[name] = df["impliedVolatility"]
    if not per_type:
        return None
    allv = pd.concat(per_type.values())
    if len(allv) < MIN_CONTRACTS:
        return None
    return {
        "iv": float(allv.median()),
        "n": int(len(allv)),
        "call_iv": float(per_type["call"].median()) if "call" in per_type else None,
        "put_iv": float(per_type["put"].median()) if "put" in per_type else None,
    }


def fetch_iv30(yf_symbol: str, fallback_spot: float | None = None) -> dict | None:
    """Return {'iv30': pct, 'iv_expiries': [...], 'iv_spot', 'iv_n', 'iv_reliable'} or None."""
    try:
        today = date.today()
        tk = yf.Ticker(yf_symbol)
        expiries = list(tk.options or [])
        picks = _pick_expiries(expiries, today)
        if not picks:
            return None

        points = []  # (T_years, iv)
        n_total = 0
        call_ivs, put_ivs = [], []
        spot = fallback_spot
        for exp, dte in picks:
            chain = tk.option_chain(exp)
            und = getattr(chain, "underlying", None) or {}
            spot = und.get("regularMarketPrice") or spot
            if not spot:
                return None
            res = _atm_iv(chain, float(spot), today)
            if res is None:
                continue
            points.append((dte / 365.0, res["iv"]))
            n_total += res["n"]
            if res["call_iv"] is not None:
                call_ivs.append(res["call_iv"])
            if res["put_iv"] is not None:
                put_ivs.append(res["put_iv"])
        if not points:
            return None

        t30 = TARGET_DTE / 365.0
        if len(points) == 2:
            (t1, v1), (t2, v2) = points
            var1, var2 = v1 * v1 * t1, v2 * v2 * t2
            w = (t30 - t1) / (t2 - t1) if t2 != t1 else 0.0
            var30 = var1 + (var2 - var1) * w
            iv30 = math.sqrt(max(var30, 0) / t30)
        else:
            iv30 = points[0][1]

        # Reliability: enough live contracts, both expiries usable, and calls
        # and puts roughly agree (put-call parity says ATM IVs should match).
        pc_gap = None
        if call_ivs and put_ivs:
            c, p_ = sum(call_ivs) / len(call_ivs), sum(put_ivs) / len(put_ivs)
            pc_gap = abs(c - p_) / max(c, p_)
        reliable = (
            n_total >= GOOD_CONTRACTS
            and len(points) == len(picks)
            and pc_gap is not None
            and pc_gap <= MAX_PUT_CALL_GAP
        )
        return {
            "iv30": round(iv30 * 100, 2),
            "iv_expiries": [e for e, _ in picks],
            "iv_spot": float(spot),
            "iv_n": n_total,
            "iv_pc_gap": round(pc_gap, 2) if pc_gap is not None else None,
            "iv_reliable": bool(reliable),
        }
    except Exception:  # noqa: BLE001 - one bad ticker must not sink the scan
        return None


def fetch_all(
    symbols: list[str],
    spots: dict[str, float] | None = None,
    workers: int = 8,
    log=print,
) -> dict[str, dict | None]:
    spots = spots or {}
    out: dict[str, dict | None] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(fetch_iv30, s, spots.get(s)): s for s in symbols}
        for fut in as_completed(futs):
            sym = futs[fut]
            out[sym] = fut.result()
            done += 1
            if done % 50 == 0 or done == len(symbols):
                ok = sum(1 for v in out.values() if v)
                log(f"Implied vol: {done}/{len(symbols)} fetched, {ok} with data")
    return out


if __name__ == "__main__":
    import sys
    import time

    syms = sys.argv[1:] or ["AAPL", "MRNA", "BRK-B", "NVDA", "PCG"]
    t = time.time()
    res = fetch_all(syms, workers=4)
    for s in syms:
        print(s, res.get(s))
    print(f"{time.time() - t:.1f}s")
