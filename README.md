# European stock prices for the €1,000 Strategy League

A free nightly GitHub Actions job collects daily, dividend-adjusted closing prices for every member of Europe's main and mid-cap stock indices (FTSE 100/250, DAX/MDAX/SDAX, CAC 40/SBF 120, AEX/AMX, FTSE MIB/Italia Mid Cap, IBEX 35/Medium Cap, SMI/SMI MID, OMX Stockholm 30, OMX Copenhagen 25, OMX Helsinki 25, OBX, BEL 20, PSI, ATX, ISEQ 20, WIG20/mWIG40), plus EUR exchange rates.

Results are published as assets of the `data` release: `eu_prices.parquet`, `eu_universe.csv`, `eu_report.json`.
Prices come from Yahoo Finance via `yfinance` for personal, non-commercial research.
