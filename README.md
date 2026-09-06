# volscan — S&P 500 volatility scanner

Scans every S&P 500 constituent and ranks them by realized volatility or by
30-day implied volatility from the option chain. Shows the top 100 (or
25/50/200/all) in a sortable table with sector filter, search, and 60-day
sparklines.

## Run

```bash
.venv/bin/python server.py
```

Then type **http://localhost:5340** into the browser address bar (do not open
`static/index.html` as a file). The first launch runs a scan automatically
(~1.5 min including option chains); after that press **Rescan now** to refresh.

CLI only (writes `data/scan.json`):

```bash
.venv/bin/python scan.py             # uses cached constituent list (1 day)
.venv/bin/python scan.py --refresh-list
.venv/bin/python scan.py --no-iv     # skip option chains (~45 s)
.venv/bin/python options.py AAPL MRNA   # debug IV for a few tickers
```

## Metrics

| Column | Meaning |
|---|---|
| 20d / 60d / 120d / 1y vol | annualised std-dev of daily log returns over the window, in % |
| ATR20 % | 20-day average true range ÷ last close |
| IV 30d | at-the-money implied vol interpolated (in variance) to 30 days from the two nearest listed expiries; median of ATM calls and puts |
| IV/RV | IV 30d ÷ 20d realized vol; above 1 means options price more movement than the stock has recently delivered |
| Max day (60d) | largest single-day absolute move in the last 60 sessions |
| 5d / 20d ret / 1y ret | simple price returns |

IV uses only live two-sided quotes (bid > 0, spread ≤ 60 % of mid, traded in the
last 14 days, strike within 10 % of spot). A ticker is flagged **~ thin** when it
has fewer than 6 usable contracts, only one usable expiry, or ATM call and put IV
disagree by more than 35 %; thin tickers rank last in the IV views. Thresholds
live at the top of `options.py`.

Prices come from Yahoo Finance via `yfinance` (dividend/split adjusted). The
constituent list is scraped from Wikipedia and cached for a day in
`data/constituents.json`. Tickers with a dot (BRK.B) are mapped to Yahoo's
dash form (BRK-B).

## Setup from scratch

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

Needs Python 3.12+. No API keys: prices and option chains come from Yahoo
Finance and the constituent list from Wikipedia.
