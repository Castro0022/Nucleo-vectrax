"""
Vectrax Alpaca Connector — Governance
=======================================
Domain: trading_paper_default
Mode:   read + execute (paper by default, live requires explicit env)

Allowlist:
  - paper-api.alpaca.markets  (paper trading)
  - api.alpaca.markets        (live trading)
  - data.alpaca.markets       (market data)

Authentication:
  - ALPACA_API_KEY    (env)
  - ALPACA_SECRET_KEY (env)

Security rules:
  - Default paper=True — never real without explicit ALPACA_PAPER=false
  - Max $20 per order, max 5 positions, max $100 total exposure
  - Kill switch: ALPACA_HALT=true blocks all execution
  - All operations logged to audit ledger
"""
