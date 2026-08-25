# Metrics Expansion + Dashboard — Design

**Spec date:** 2026-08-25
**Status:** approved (user direction: "add everything, plus a dashboard")

## Background

The `aplist-metrics` pipeline (built 2026-08-22, live since) currently records
own-radio channel/connected-users, external-network-count-by-channel, and
poll failures. Live observation since deploy surfaced two follow-on
questions:

1. What else, cheaply available from the same MIB, would help explain *why*
   a radio's ARM/DFS behavior does what it does (not just *that* it changed)?
2. Now that `victoria-logs` exists (syslog capture, live since 2026-08-25),
   can any of its content usefully become a metric too?

This spec covers the answer to both, plus a dashboard so a human can look at
the result without hand-writing PromQL each time.

## New metrics (SNMP-sourced)

All four candidate MIB objects below were verified **live** against
`192.0.2.80` on 2026-08-25 (not just derived from the `.my` file) using the
same method as every other OID in this project (see
`reference_colubris_mib_source` memory) — chain-derive from
`COLUBRIS-802DOT11-MIB`, verify with a real `snmpwalk` before trusting it.

| Object | OID | Table | Index shape | Verified live value |
|---|---|---|---|---|
| `coDot11CurrentSNRLevel` | `1.3.6.1.4.1.8744.5.4.1.1.1.10` | `coDot11AccessPointConfigTable` (same table as `BSSID_OID`/`USERS_OID`) | `<radio>.<vap>` | `35`, `30` (dBm) |
| `coDot11NoiseLevel` | `1.3.6.1.4.1.8744.5.4.1.5.1.5` | `coDot11AssociationTable` | `<radio>.<associationIndex>` | `-93`, `-86` (dBm) |
| `coDot11RetryCount` | `1.3.6.1.4.1.8744.5.4.2.2.1.4` | `coDot11CountersTable` | `<radio>` (ifIndex only) | `0`, `106268` |
| `coDot11MultipleRetryCount` | `1.3.6.1.4.1.8744.5.4.2.2.1.5` | `coDot11CountersTable` | `<radio>` | `0`, `11018` |
| `coDot11FailedCount` | `1.3.6.1.4.1.8744.5.4.2.2.1.3` | `coDot11CountersTable` | `<radio>` | `0`, `113060` |
| `coDot11ACKFailureCount` | `1.3.6.1.4.1.8744.5.4.2.2.1.9` | `coDot11CountersTable` | `<radio>` | `0`, `113060` |

**Indexing note, important for implementation:** three different shapes exist
across these six OIDs:
- SNR: `<radio>.<vap>` — same shape as `BSSID_OID`/`ESSID_OID`, keyed by
  `suffix` in `parse_own_radio_tables`.
- The four counters: `<radio>` only (no VAP component at all) — same shape
  as `CHANNEL_OID`, keyed by `radio` (`suffix.split(".", 1)[0]`). This
  hardware has exactly one VAP per physical radio (confirmed structurally
  throughout this project — `bssids` never has more than one suffix per
  radio prefix), so attaching a radio-level counter to "the" VAP's
  `APRecord` for that radio is unambiguous. If a second VAP per radio is
  ever added, this assumption breaks and needs revisiting.
- Noise: `<radio>.<associationIndex>` — one row **per currently-connected
  client**, not one per radio. Live-verified the value is identical across
  every client of the same radio (it's an ambient noise-floor reading, not
  a per-client one) — confirmed by seeing 5 identical `-86` rows for one
  radio's 5 clients. **Poller-side handling:** collapse this to a
  radio-keyed dict by taking the first value seen per radio prefix (order
  doesn't matter since they're identical), same normalization step, not
  passed through as association-indexed data. When a radio has zero
  connected clients, no rows exist for it at all — the resulting metric is
  simply absent for that radio that poll, exactly like `num_users`'s
  existing `None` handling, no error.

**Dropped, not implemented:** `coDot11StaChannelWidth`
(`coDot11StationHTEntry`) was investigated and rejected — it reports a
*connected client's* HT channel-width capability/negotiation, not the AP
radio's own configured operating bandwidth. It doesn't answer "is my radio
running 40MHz," so it isn't useful for this project's purposes. The
existing hardcoded 40MHz assumption in `planner.py` is unaffected and stays
as-is.

### New `APRecord` fields

```python
@dataclass(frozen=True)
class APRecord:
    bssid: str
    essid: str
    channel: int | None
    host: str | None = None
    num_users: int | None = None
    snr_db: int | None = None
    noise_dbm: int | None = None
    retry_count: int | None = None
    multiple_retry_count: int | None = None
    failed_count: int | None = None
    ack_failure_count: int | None = None
```

All six new fields are optional (`None` default), matching `num_users`'s
existing pattern — every new metric line in `metrics.py` is only emitted
when its field is not `None`.

### `parse_own_radio_tables` signature

```python
def parse_own_radio_tables(
    bssids: dict[str, str],
    essids: dict[str, str],
    channels: dict[str, str],
    host: str | None = None,
    users: dict[str, str] | None = None,
    snr: dict[str, str] | None = None,
    noise: dict[str, str] | None = None,
    retry_count: dict[str, str] | None = None,
    multiple_retry_count: dict[str, str] | None = None,
    failed_count: dict[str, str] | None = None,
    ack_failure_count: dict[str, str] | None = None,
) -> list[APRecord]:
```

`snr` is looked up by `suffix` (VAP-keyed, same as `users` — and same
lesson as the num_users bug the final review caught 2026-08-22: **do not**
look this one up by `radio` alone). `noise`, `retry_count`,
`multiple_retry_count`, `failed_count`, `ack_failure_count` are all looked
up by `radio` (same as `channels`) since their source tables are
radio-only-indexed, already collapsed/normalized to that shape before
reaching this function (`noise` specifically arrives pre-collapsed by
`poller.py`, per the indexing note above — `parse_own_radio_tables` itself
does no association-index handling, it just does a `radio`-keyed lookup
like the other counters).

### `poller.py` additions

Six new `_OID` constants (values above) with the same MIB-citation comment
style as the existing ones. `poll_ap` walks all six, converts each raw
result the same way as the existing `raw_channels`/`raw_users` handling
(`.prettyPrint()`), with one extra step for noise: collapse the
association-indexed raw dict down to a radio-keyed dict before passing to
`parse_own_radio_tables`:

```python
noise = {}
for suffix, value in raw_noise.items():
    radio = suffix.split(".", 1)[0]
    noise.setdefault(radio, value.prettyPrint())
```

### `metrics.py`: `format_own_radio_metrics` additions

Six new metric names, each gated on its field being non-`None`, following
the exact existing pattern for `num_users`/`aplist_connected_users`:

- `aplist_snr_db`
- `aplist_noise_floor_dbm`
- `aplist_retry_count`
- `aplist_multiple_retry_count`
- `aplist_failed_count`
- `aplist_ack_failure_count`

All six share the same `host`/`bssid`/`essid` label set as the existing
`aplist_own_channel`/`aplist_connected_users` lines. The four counter
metrics are genuine SNMP `Counter32` values (monotonically increasing,
resets only on device reboot) — pushed as-is (no rate/delta math on the
`aplist-gen` side); `rate()`/`increase()` in PromQL at query time is the
correct way to interpret them, same as any other Prometheus counter. No
special handling needed in this codebase beyond exposing the raw value.

## New metric (syslog-sourced): config-change event count

`victoria-logs` already has real `eventmgr: ... Configuration has changed`
lines flowing in from all 3 APs (confirmed live 2026-08-25). This adds a
derived counter, queried from VictoriaLogs each `aplist-metrics` poll cycle
and pushed to VictoriaMetrics as a normal metric line —
`aplist_config_change_count`.

**New function in `metrics.py`:**

```python
def query_config_change_count(logs_url: str, window: str = "15m") -> int:
    query = f'eventmgr AND "Configuration has changed" AND _time:{window} | stats count() as cnt'
    request = urllib.request.Request(
        logs_url, data=f"query={query}".encode("utf-8"), method="POST"
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        body = response.read().decode("utf-8").strip()
    if not body:
        return 0
    return int(json.loads(body.splitlines()[0])["cnt"])
```

`window` defaults to `"15m"`, matching `aplist-metrics`'s poll cadence
exactly. **Known, accepted limitation:** because polls don't fire at exact
15-minute boundaries (jitter, missed runs), this can occasionally
double-count or miss an event right at the window edge. Not worth
compensating for — this is a coarse "how much config-change activity is
happening" signal, not an exact audit log; the raw syslog lines remain the
source of truth in VictoriaLogs itself if precision is ever needed.

**Wiring in `__init__.py`'s `run_metrics`:** a new `LOGS_URL` environment
variable (optional, same pattern as `METRICS_URL`) pointing at
`http://192.0.2.1:9428/select/logsql/query`. **Must be the node's real
LAN address, not `127.0.0.1`** — this project already hit and fixed exactly
this mistake once (`aplist-metrics.nomad.hcl`'s `METRICS_URL`, final review
2026-08-22): `victoria-logs`, like `victoria-metrics`, uses a Nomad group
`network`+`port` stanza (bridge/CNI), which only publishes on the node's
advertised LAN address, never on loopback, regardless of what's calling it.

If `LOGS_URL` is unset, skip this metric entirely and warn (same pattern as
missing `METRICS_URL`) — do not fail the run. If the query itself fails
(network error, malformed response), catch `OSError` and any JSON-parsing
error, warn, and skip just this one metric line — the rest of the push
(own-radio metrics, external-network metrics, poll failures) must still
succeed even if VictoriaLogs is briefly unreachable. This mirrors
`run_metrics`'s existing `push_metrics` error handling exactly (catch,
warn to stderr, continue).

## Explicitly not built (blocked)

**DFS-vs-ARM channel-change-reason classification** — the original
motivation for building `victoria-logs` at all — is **not** part of this
plan. No real DFS-triggered or genuine-ARM-reoptimization syslog message
has been captured yet (only routine RRM scan-complete and config-change
events observed so far, as of 2026-08-25). Building a classifier against
guessed message formats risks silently misclassifying real events, which
is worse than not having the feature yet. **Revisit once a real channel
change happens after `victoria-logs` was live** (any `aplist_own_channel`
transition from this point forward has syslog coverage) — check what
actually got logged around that timestamp, then design the classifier
against real data.

## Dashboard

VictoriaMetrics's `vmui` supports custom dashboards via
`-vmui.customDashboardsPath=<dir>`, pointed at a directory of JSON files.
Schema confirmed against VictoriaMetrics's own shipped example
(`perJobUsage.json`):

```json
{
  "title": "...",
  "rows": [
    { "panels": [ { "title": "...", "width": 6, "expr": ["<promql>", "..."] } ] }
  ]
}
```

`width` is out of 12 (2 panels per row at width 6). No new host volume
needed — the dashboard JSON is static, rendered via a Nomad `template`
block into the task's `local/` directory (same mechanism `aplist-gen`
already uses for `secrets/snmp.env`, which needs no explicit
`volume_mount` either — Nomad auto-mounts `local/`/`secrets/`/`alloc/`
into podman tasks).

**`victoria-metrics.nomad.hcl` changes:**
- Add `-vmui.customDashboardsPath=/local/dashboards` to the task's `args`.
- Add a `template` block rendering the dashboard JSON (static content, no
  Nomad variable interpolation needed) to `local/dashboards/aplist.json`.

**Panel layout** (5 rows):
1. `aplist_own_channel` (width 6) | `aplist_connected_users` (width 6)
2. `aplist_snr_db` (width 6) | `aplist_noise_floor_dbm` (width 6)
3. `rate(aplist_retry_count[1h])`, `rate(aplist_failed_count[1h])`,
   `rate(aplist_ack_failure_count[1h])` — one combined panel (width 12)
4. `topk(10, aplist_external_network_count)` (width 6) |
   `aplist_poll_failure` (width 6)
5. `aplist_config_change_count` (width 12)

## Global Constraints (carried into the plan)

- `dataclass(frozen=True)` stays on `APRecord` — no mutation.
- Minimal, WHY-only comments (this codebase's established convention).
- `uv run pytest` must pass throughout; zero changes to `run_generate`,
  `run_plan`, `poll_all`, `poll_all_neighbors`, `_gather_plan_data` (same
  boundary as the original metrics plan — this is observability-only,
  additive).
- New SNMP fields follow the exact indexing-shape rules above — this is
  the part a previous final review caught a real bug in
  (`num_users`/`suffix` vs `radio`); implementers must not guess.
- `LOGS_URL`/`METRICS_URL` both point at the node's real LAN address
  (`192.0.2.1`), never `127.0.0.1` — this project already paid for that
  mistake once.
- No new host volumes. No new Nomad jobs. Only
  `core.py`/`poller.py`/`metrics.py`/`__init__.py`/`victoria-metrics.nomad.hcl`
  change.
