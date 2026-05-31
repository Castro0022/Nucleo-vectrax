# eToro API — Configuración de Credenciales

## Requisitos

El motor de mercado eToro de Vectrax requiere dos claves de la API pública de eToro:

| Variable de entorno | Descripción | Header HTTP |
|---|---|---|
| `ETORO_API_KEY` | Clave pública de aplicación | `x-api-key` |
| `ETORO_USER_KEY` | Clave privada del usuario | `x-user-key` |

Ambas claves son **obligatorias en cada request**. Son credenciales de larga duración vinculadas a tu cuenta eToro.

---

## Cómo generar las claves

1. Inicia sesión en [api-portal.etoro.com](https://api-portal.etoro.com)
2. Ve a **Settings → Trading → API Key Management**
3. Haz clic en **Create New Key**
4. Selecciona:
   - **Environment:** Virtual (demo) o Real — debe coincidir con `ETORO_ENVIRONMENT` en `.env`
   - **Permissions:** `Read` para observación de mercado + `Write` para ejecución de órdenes
5. Copia ambas claves inmediatamente — el `User Key` solo se muestra una vez

> **Importante:** El `User Key` equivale a una contraseña. Cualquiera con ambas claves puede ejecutar operaciones en tu nombre.

---

## Scopes requeridos por funcionalidad

| Funcionalidad | Scope mínimo |
|---|---|
| Observar mercado, precios, velas | `Read` (Market Data) |
| Ver portfolio, posiciones, PnL | `Read` (Portfolio) |
| Abrir/cerrar posiciones | `Write` (Trading Execution) |
| Motor de aprendizaje (señales/outcomes) | `Read` (Market Data) |
| Auto-executor PAPER | `Read` solo (simulación) |
| Auto-executor LIVE | `Write` (Trading Execution) |

---

## Configuración en el servidor

### Opción 1 — Desde Telegram (recomendado)

```
# Verificar conectividad actual
/vx etoro connect

# Si hay error 401, actualizar claves en el servidor:
# 1. Conéctate al servidor
# 2. Edita /opt/vectrax/.env
# 3. Reinicia con: docker restart vectrax-core
```

### Opción 2 — SSH directo

```bash
ssh -i ~/.ssh/vectrax_server root@140.82.28.181 \
  "sed -i 's|ETORO_API_KEY=.*|ETORO_API_KEY=TU_NUEVA_CLAVE|' /opt/vectrax/.env && \
   sed -i 's|ETORO_USER_KEY=.*|ETORO_USER_KEY=TU_NUEVO_USER_KEY|' /opt/vectrax/.env && \
   docker restart vectrax-core"
```

---

## Variables de entorno completas

Agrega al archivo `.env` del servidor (`/opt/vectrax/.env`):

```bash
# eToro API — Credenciales
ETORO_API_KEY=eyJ...          # tu Public API Key
ETORO_USER_KEY=sdg...         # tu User Key (solo se muestra al crear)

# Entorno de trading (demo por defecto — NUNCA cambiar a real sin fase PAPER completada)
ETORO_ENVIRONMENT=demo        # opciones: demo | real
```

---

## Verificación de conectividad

Desde Telegram (solo creador):

```
/vx etoro connect
```

Respuesta esperada con claves válidas:
```
✅ eToro API conectada
Entorno: 🟢 DEMO
Latencia: XXXms
N instruments found for BTCUSD
```

Respuesta con claves inválidas:
```
❌ Error: HTTP 401
```

---

## Error HTTP 401 — Causas y soluciones

| Causa | Solución |
|---|---|
| Clave expirada o revocada | Regenerar en api-portal.etoro.com |
| Environment incorrecto (Real key en endpoint Demo) | Crear clave del tipo correcto |
| Scope insuficiente (Read-only intentando Write) | Crear clave con permisos Write |
| `ETORO_API_KEY` vacía o malformada | Verificar `.env` en el servidor |
| KYC incompleto en cuenta eToro | Completar verificación en etoro.com |

---

## Flujo de activación del motor de aprendizaje

```
1. Configurar credenciales válidas (Read scope)
2. /vx etoro connect  →  ✅ conectada
3. /vx etoro auto paper  →  activar simulación
4. /vx etoro learn run  →  primer ciclo de observación
5. [Esperar señales acumuladas — mínimo 30, WR ≥ 60%]
6. /vx etoro auto status  →  verificar requisitos LIVE
7. /vx etoro env real  →  cambiar entorno (requiere clave Real + Write)
8. /vx etoro auto live  →  activar ejecución real
```

---

## Límites de riesgo por defecto

| Parámetro | Valor | Cambiar con |
|---|---|---|
| Máximo por operación | $100 | `/vx etoro auto config max_position_usd 200` |
| Pérdida máxima diaria | $50 | `/vx etoro auto config max_daily_loss_usd 100` |
| Stop-loss obligatorio | 1.5% | `/vx etoro auto config stop_loss_pct 2.0` |
| Pérdidas consecutivas → shutdown | 3 | `/vx etoro auto config max_consecutive_losses 5` |
| Posiciones simultáneas máx | 2 | `/vx etoro auto config max_positions_open 3` |
| Señales PAPER mínimas para LIVE | 30 | `/vx etoro auto config min_paper_signals 50` |
| Win rate PAPER mínimo para LIVE | 60% | `/vx etoro auto config min_paper_win_rate 65` |

---

## Referencia de comandos

```
/vx etoro connect          — verificar API
/vx etoro portfolio        — cash, PnL, equity
/vx etoro price BTCUSD     — precio actual
/vx etoro status BTCUSD buy — evaluar 4 condiciones
/vx etoro learn status     — estado del motor de aprendizaje
/vx etoro learn run        — ejecutar ciclo completo
/vx etoro learn signals    — señales recientes
/vx etoro learn outcomes   — predicción vs resultado
/vx etoro learn patterns   — memoria estadística
/vx etoro learn proposals  — propuestas generadas
/vx etoro auto status      — estado del auto-executor
/vx etoro auto paper       — activar simulación
/vx etoro auto live        — activar ejecución real
/vx etoro auto config      — ver/cambiar límites de riesgo
```
