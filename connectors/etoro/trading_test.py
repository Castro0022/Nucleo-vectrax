"""
connectors/etoro/trading_test.py — Round-Trip Trading Test
============================================================
Verifica la capacidad completa de trading ejecutando un ciclo:
  1. Lee portfolio actual
  2. Abre una posición mínima (BTC, $25)
  3. Verifica que aparece en portfolio
  4. Cierra la posición inmediatamente
  5. Verifica que se cerró
  6. Reporta latencias y estado

Uso:
  python -m connectors.etoro.trading_test              # dry-run (solo lee)
  python -m connectors.etoro.trading_test --execute     # ejecuta round-trip real
  python -m connectors.etoro.trading_test --close-only  # cierra posiciones abiertas

IMPORTANTE: --execute usa dinero REAL. Solo para verificación.

Creador: Mario Bravo Castro
Fecha: 2026-06-13
"""
from __future__ import annotations

import json
import logging
import sys
import time
from typing import Dict, Any, List

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("etoro.trading_test")

# Default test instrument: BTC (instrumentID=100000 on eToro)
TEST_SYMBOL = "BTC"
TEST_INSTRUMENT_ID = 100000
MIN_AMOUNT = 25  # minimum order in USD for BTC


def _step(n: int, label: str) -> None:
    print(f"\n{'='*50}")
    print(f"  STEP {n}: {label}")
    print(f"{'='*50}")


def read_portfolio() -> Dict[str, Any]:
    """Read current portfolio state."""
    from connectors.etoro.etoro_client import get_portfolio
    return get_portfolio()


def read_price(instrument_id: int = TEST_INSTRUMENT_ID) -> Dict[str, Any]:
    """Get current price for instrument."""
    from connectors.etoro.etoro_client import get_price
    return get_price(instrument_id)


def open_test_order(
    instrument_id: int = TEST_INSTRUMENT_ID,
    amount: float = MIN_AMOUNT,
) -> Dict[str, Any]:
    """Open a minimal buy order."""
    from connectors.etoro.etoro_client import open_position
    return open_position(
        instrument_id=instrument_id,
        amount=amount,
        is_buy=True,
        leverage=1,
    )


def close_test_order(
    position_id: str,
    instrument_id: int = TEST_INSTRUMENT_ID,
) -> Dict[str, Any]:
    """Close a position by ID."""
    from connectors.etoro.etoro_client import close_position
    return close_position(position_id, instrument_id)


def run_dry_test() -> Dict[str, Any]:
    """Read-only test: portfolio + price. No trades."""
    results = {"steps": [], "success": True}

    _step(1, "READ PORTFOLIO")
    t0 = time.time()
    portfolio = read_portfolio()
    lat = round((time.time() - t0) * 1000)
    if portfolio["success"]:
        print(f"  Credit:    ${portfolio['credit']:.2f}")
        print(f"  Invested:  ${portfolio['invested']:.2f}")
        print(f"  PnL:       ${portfolio['pnl']:.2f}")
        print(f"  Equity:    ${portfolio['equity']:.2f}")
        print(f"  Positions: {portfolio['n_positions']}")
        for p in portfolio["positions"]:
            pid = p.get("positionID")
            iid = p.get("instrumentID")
            amt = p.get("amount", 0)
            pnl = p.get("unrealizedPnL", {})
            pnl_val = pnl.get("pnL", 0) if isinstance(pnl, dict) else 0
            print(f"    POS {pid} | inst={iid} | ${amt:.2f} | PnL=${pnl_val:.2f}")
        print(f"  Latency:   {lat}ms")
        results["steps"].append({"step": "portfolio", "ok": True, "latency_ms": lat})
    else:
        print(f"  ERROR: {portfolio.get('error')}")
        results["steps"].append({"step": "portfolio", "ok": False, "error": portfolio.get("error")})
        results["success"] = False

    _step(2, f"READ PRICE ({TEST_SYMBOL})")
    t0 = time.time()
    price = read_price()
    lat = round((time.time() - t0) * 1000)
    if price["success"]:
        print(f"  Ask:  ${price['ask']:,.2f}")
        print(f"  Bid:  ${price['bid']:,.2f}")
        print(f"  Mid:  ${price['mid']:,.2f}")
        print(f"  Latency: {lat}ms")
        results["steps"].append({"step": "price", "ok": True, "latency_ms": lat})
    else:
        print(f"  ERROR: {price.get('error')}")
        results["steps"].append({"step": "price", "ok": False, "error": price.get("error")})
        results["success"] = False

    return results


def run_close_all() -> Dict[str, Any]:
    """Close all open positions."""
    results = {"closed": 0, "errors": 0}

    portfolio = read_portfolio()
    if not portfolio["success"]:
        print(f"  ERROR reading portfolio: {portfolio.get('error')}")
        return results

    positions = portfolio["positions"]
    if not positions:
        print("  No open positions.")
        return results

    for p in positions:
        pid = p.get("positionID")
        iid = p.get("instrumentID")
        amt = p.get("amount", 0)
        print(f"\n  Closing POS {pid} | inst={iid} | ${amt:.2f}...")
        t0 = time.time()
        r = close_test_order(str(pid), int(iid))
        lat = round((time.time() - t0) * 1000)
        if r["success"]:
            print(f"  ✓ Closed in {lat}ms")
            results["closed"] += 1
        else:
            print(f"  ✗ Error: {r.get('error')} {r.get('details', '')}")
            results["errors"] += 1

    return results


def run_round_trip() -> Dict[str, Any]:
    """Full round-trip: read → open → verify → close → verify."""
    results = {"steps": [], "success": True, "total_ms": 0}
    t_total = time.time()

    # Step 1: Read portfolio before
    _step(1, "PORTFOLIO (before)")
    portfolio = read_portfolio()
    if not portfolio["success"]:
        print(f"  ERROR: {portfolio.get('error')}")
        results["success"] = False
        return results
    initial_positions = portfolio["n_positions"]
    print(f"  Credit: ${portfolio['credit']:.2f} | Positions: {initial_positions}")
    results["steps"].append({"step": "portfolio_before", "ok": True})

    # Step 2: Get price
    _step(2, f"PRICE ({TEST_SYMBOL})")
    price = read_price()
    if not price["success"]:
        print(f"  ERROR: {price.get('error')}")
        results["success"] = False
        return results
    print(f"  {TEST_SYMBOL}: ${price['mid']:,.2f}")
    results["steps"].append({"step": "price", "ok": True})

    # Step 3: Open minimal position
    _step(3, f"OPEN BUY ${MIN_AMOUNT} {TEST_SYMBOL}")
    t0 = time.time()
    order = open_test_order()
    open_lat = round((time.time() - t0) * 1000)
    if not order["success"]:
        print(f"  ERROR: {order.get('error')} {order.get('details', '')}")
        results["success"] = False
        results["steps"].append({"step": "open", "ok": False, "error": order.get("error")})
        return results
    position_id = order.get("position_id") or order.get("order_id")
    print(f"  ✓ Order executed | POS={position_id} | {open_lat}ms")
    print(f"  Environment: {order.get('environment')}")
    results["steps"].append({"step": "open", "ok": True, "latency_ms": open_lat, "position_id": position_id})

    # Step 4: Verify position exists
    _step(4, "VERIFY POSITION")
    time.sleep(1)  # brief wait for settlement
    portfolio2 = read_portfolio()
    if portfolio2["success"]:
        new_positions = portfolio2["n_positions"]
        found = any(
            str(p.get("positionID")) == str(position_id)
            for p in portfolio2["positions"]
        )
        print(f"  Positions: {initial_positions} → {new_positions}")
        print(f"  Test position found: {found}")
        # If position_id from order was actually an order_id, find the position
        if not found and new_positions > initial_positions:
            # Find the new position
            for p in portfolio2["positions"]:
                if p.get("instrumentID") == TEST_INSTRUMENT_ID:
                    position_id = p.get("positionID")
                    print(f"  Found by instrument: POS={position_id}")
                    found = True
                    break
        results["steps"].append({"step": "verify_open", "ok": found})
    else:
        print(f"  ERROR reading portfolio: {portfolio2.get('error')}")
        results["steps"].append({"step": "verify_open", "ok": False})

    # Step 5: Close position immediately
    _step(5, f"CLOSE POS={position_id}")
    t0 = time.time()
    close = close_test_order(str(position_id))
    close_lat = round((time.time() - t0) * 1000)
    if close["success"]:
        print(f"  ✓ Position closed | {close_lat}ms")
        results["steps"].append({"step": "close", "ok": True, "latency_ms": close_lat})
    else:
        print(f"  ✗ Close failed: {close.get('error')} {close.get('details', '')}")
        results["steps"].append({"step": "close", "ok": False, "error": close.get("error")})
        results["success"] = False

    # Step 6: Verify closed
    _step(6, "VERIFY CLOSED")
    time.sleep(1)
    portfolio3 = read_portfolio()
    if portfolio3["success"]:
        final_positions = portfolio3["n_positions"]
        still_open = any(
            str(p.get("positionID")) == str(position_id)
            for p in portfolio3["positions"]
        )
        print(f"  Positions: {final_positions} (was {initial_positions})")
        print(f"  Test position still open: {still_open}")
        print(f"  Credit: ${portfolio3['credit']:.2f}")
        results["steps"].append({"step": "verify_closed", "ok": not still_open})
    else:
        results["steps"].append({"step": "verify_closed", "ok": False})

    # Summary
    results["total_ms"] = round((time.time() - t_total) * 1000)
    _step(7, "SUMMARY")
    all_ok = all(s["ok"] for s in results["steps"])
    results["success"] = all_ok
    print(f"  Result:    {'✓ PASS' if all_ok else '✗ FAIL'}")
    print(f"  Open lat:  {open_lat}ms")
    print(f"  Close lat: {close_lat}ms")
    print(f"  Total:     {results['total_ms']}ms")
    print(f"  Steps:     {len(results['steps'])}/{len(results['steps'])} OK" if all_ok
          else f"  Failed:    {[s['step'] for s in results['steps'] if not s['ok']]}")

    return results


if __name__ == "__main__":
    import os
    # Ensure .env is loaded
    try:
        from dotenv import load_dotenv
        from pathlib import Path
        _env = Path(__file__).resolve().parent.parent.parent / ".env"
        if _env.exists():
            load_dotenv(_env)
    except ImportError:
        pass

    if "--execute" in sys.argv:
        print("\n⚠  ROUND-TRIP TEST — REAL MONEY")
        print(f"   Will open ${MIN_AMOUNT} {TEST_SYMBOL} and close immediately.")
        print(f"   Environment: {os.environ.get('ETORO_ENVIRONMENT', 'demo')}\n")
        run_round_trip()
    elif "--close-only" in sys.argv:
        print("\n🔒 CLOSING ALL OPEN POSITIONS\n")
        r = run_close_all()
        print(f"\n  Closed: {r['closed']} | Errors: {r['errors']}")
    else:
        print("\n📊 DRY-RUN TEST (read-only)\n")
        print("   Use --execute for round-trip trade test")
        print("   Use --close-only to close open positions\n")
        run_dry_test()
