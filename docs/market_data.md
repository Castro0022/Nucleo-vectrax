# Vectrax — Módulo de Mercado (Market Data)

## Descripción
Módulo de datos de mercado en tiempo real para crypto y bolsa. Solo lectura, sin ejecución de órdenes.

## Fuentes de datos

| Fuente | Tipo | Requiere API Key | Límites |
|--------|------|-------------------|---------|
| Binance REST | Crypto spot, ticker, OHLCV | No | Sin límite público |
| Binance WebSocket | Crypto real-time stream | No | Sin límite |
| CoinGecko | Crypto fallback, market cap | Opcional (Demo) | 30 req/min (demo) |
| Alpha Vantage | Stocks, ETFs, índices | Sí | 25 req/día (free) |

## Activación

### 1. Variables de entorno
Copiar `.env.example` a `.env` y configurar:

```bash
# Obligatoria para stocks:
ALPHAVANTAGE_API_KEY=tu_clave_aquí

# Opcional para CoinGecko:
COINGECKO_API_KEY=tu_clave_demo
```

### 2. Dependencia WebSocket (opcional)
Para streaming en tiempo real:
```bash
pip install websocket-client
```

## Uso

### Funciones principales (Python)
```python
from services.market_router import (
    get_crypto_spot,
    get_stock_quote,
    get_ohlcv,
    get_market_snapshot,
    market_status,
)
from services.market_signals import detect_trend, detect_momentum, detect_breakout

# Precio spot de BTC
result = get_crypto_spot("BTCUSDT")

# Quote de SPY
result = get_stock_quote("SPY")

# OHLCV con timeframe
result = get_ohlcv("BTCUSDT", "1h", limit=100)
result = get_ohlcv("SPY", "1d")

# Snapshot completo
result = get_market_snapshot()

# Análisis técnico
trend = detect_trend("BTCUSDT", "1h")
momentum = detect_momentum("BTCUSDT", "4h")
breakout = detect_breakout("BTCUSDT", "1h")
```

### Comandos VX (intents)
```
vx market status     — estado de todas las fuentes
vx market test       — smoke test de conectividad
vx market watch      — iniciar stream WebSocket
vx market snapshot   — resumen del mercado
```

### Consultas naturales (vía intents)
- "precio de bitcoin"
- "cómo está BTC ahora"
- "tendencia de BTC 15m"
- "cómo está NVDA hoy"
- "resumen del mercado"

### Alertas
```python
from services.market_alerts import get_market_alerts

alerts = get_market_alerts()
alerts.add_alert("BTCUSDT", 70000, "above", "BTC sobre 70k")
alerts.add_alert("BTCUSDT", 60000, "below", "BTC bajo 60k")
alerts.list_active()
```

## Arquitectura

```
connectors/market/
  __init__.py          — Gobernanza, allowlist, watchlists
  binance_rest.py      — Binance API pública (GET)
  binance_stream.py    — Binance WebSocket (real-time)
  coingecko_client.py  — CoinGecko (fallback)
  alphavantage_client.py — Alpha Vantage (stocks)

services/
  market_cache.py      — Cache TTL, thread-safe
  market_router.py     — Router unificado con fallback
  market_signals.py    — Análisis técnico (SMA, ROC, breakout)
  market_alerts.py     — Sistema de alertas por nivel

intents/
  market_intents.py    — Handlers de lenguaje natural

tests/
  test_market_data.py  — Tests de conectores, cache, alertas
  test_market_router.py — Tests de routing y fallback
```

## Gobernanza y seguridad

- **Dominio**: `market_data_authorized`
- **Modo**: `read_only` — solo GET y WebSocket de lectura
- **Sin trading**: no se ejecutan órdenes, no se accede a endpoints de trading
- **Allowlist** de hosts:
  - api.binance.com
  - stream.binance.com
  - data-stream.binance.vision
  - api.coingecko.com
  - www.alphavantage.co
- **Ledger**: cada llamada registra endpoint, latencia, resultado y errores

## Watchlist inicial

**Crypto**: BTCUSDT, ETHUSDT
**Stocks**: SPY, QQQ, AAPL, TSLA, NVDA

## Limitaciones del free tier

- **Alpha Vantage**: 25 requests/día. Usar con moderación.
- **CoinGecko Demo**: 30 requests/min. Suficiente como fallback.
- **Binance**: Sin límite para endpoints públicos de market data.

## Tests
```bash
python -m pytest tests/test_market_data.py tests/test_market_router.py -v
```
