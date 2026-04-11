# Changelog

All notable changes to Vectrax are documented in this file.

## [2026-04-11] — Gateway Debug Logging + Bottleneck Analysis

### Changes
- Added `RotatingFileHandler` to gateway (`~/.vectrax/gateway.log`, 5MB x3)
- Instrumented poll cycles (POLL), message receipt (RECV), fast-path (FAST),
  queue-path (QUEUED), handler errors (HANDLE ERROR), slow handlers (HANDLE SLOW)
- Added periodic STATUS summary every 5 min with all operational counters
- Fixed supervisor `stdout=DEVNULL` → `stdout=None` for docker log visibility
- Commit: `175d63f`

### Bottleneck Analysis (from real traffic load test)

**LLM cold-start**: First invocation after deploy takes ~15s (vs 1-4s warm).
Affects first user post-restart. Consider pre-warming the LLM provider on startup.

**Language leak**: "En chile ?" (Spanish) got an English response from the
pipeline worker. The `language_gate` / `enforce_final_answer` didn't catch it
on the worker path. The gate only applies to fast-path responses in the gateway.
Needs enforcement in `pipeline_worker.py` post-processing.

**Duplicate processing**: "Yo vivo en Miami" processed twice (49s apart).
`should_accept_job` dedup window is too narrow. Consider extending to 120s
or hashing by (user_id + content).

**No bottlenecks found in**:
- Queue wait time: 0.13-0.25s (excellent)
- Poll cycle: avg 28.6s, no anomalies
- Thread pool: 1/6 used, zero saturation
- Error rate: 0 handler errors, 0 poll errors

## [2026-04-10] — Gateway Heartbeat Stability Fix

### Problem
The `telegram_gateway` process restarted every ~3 hours due to heartbeat stale
detection (REPEAT FAILURE #68-69 `gateway_stale`). The supervisor killed the
gateway when its heartbeat exceeded 90 seconds of age.

### Root Cause
The heartbeat was written only at the top of each polling cycle, **before** the
blocking `getUpdates` call (~30-40s). Two consecutive polls with any network
latency pushed the interval past the 90s threshold:

```
t=0s   → write heartbeat → poll (40s) → process
t=42s  → write heartbeat → poll (40s + network delay = 50s)
t=92s  → STALE → supervisor kills gateway
```

### Fix
- **`vectrax/telegram_gateway.py`**: Added a dedicated daemon thread
  (`gw-heartbeat`) that writes the heartbeat every 10 seconds, fully decoupled
  from the polling loop. Same strategy the `pipeline_worker` already uses.
- **`vectrax_supervisor.py`**: Increased `GATEWAY_HEARTBEAT_MAX_AGE` from 90s
  to 120s as additional safety margin.

### Verification
1-hour stability monitor (13 checks, 3 samples each):

```
[01] 01:56 UTC | gw_max=0.1s  | OK
[02] 02:00 UTC | gw_max=0.2s  | OK
[03] 02:05 UTC | gw_max=0.4s  | OK
[04] 02:10 UTC | gw_max=0.4s  | OK
[05] 02:15 UTC | gw_max=0.5s  | OK
[06] 02:20 UTC | gw_max=0.6s  | OK
[07] 02:25 UTC | gw_max=0.7s  | OK
[08] 02:29 UTC | gw_max=0.9s  | OK
[09] 02:34 UTC | gw_max=1.0s  | OK
[10] 02:39 UTC | gw_max=1.0s  | OK
[11] 02:44 UTC | gw_max=1.1s  | OK
[12] 02:49 UTC | gw_max=1.3s  | OK
[13] 02:54 UTC | gw_max=1.4s  | OK
```

- Before fix: heartbeat age reached 90-102s → kill every ~3h
- After fix: heartbeat age stays under 1.5s → zero restarts
- Commit: `e1acd80`
