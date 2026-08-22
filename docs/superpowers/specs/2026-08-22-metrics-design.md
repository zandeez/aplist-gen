# Metrics recording (VictoriaMetrics) — design

Date: 2026-08-22
Status: approved

## Goal

Record observational history — channel state, interference costs, external
network activity, connected-user counts — into a queryable time-series
store, as the prerequisite for eventually making channel planning more
data-driven (e.g. deriving the DFS-avoidance windows from real observed
patterns instead of hand-guessing them, per `192.0.2.78`/`192.0.2.149`
in `planner.py`).

This is explicitly a recording-only phase. It does not change any
channel-planning behavior, and it does not add any prediction/learning —
see "Future phases" below for why that's deliberately deferred.

## Background / constraints

- `aplist-gen` (every 5 min) and `aplist-plan` (hourly) already exist and
  are unaffected by this work — **zero code changes to either job**. The
  trust-cache gating in `aplist-plan` (require a sighting on 2 separate
  runs before it affects a recommendation) exists specifically to protect
  channel *recommendations* from flapping on noisy single-poll blips.
  Observational metrics don't need that protection — recording the raw,
  occasionally-noisy signal is the point of a time-series store; that's
  exactly where "here's a one-off blip" vs. "here's a real pattern"
  becomes visible over time.
- The RF neighbor-scan SNMP table (~110+ rows per host) is already
  documented as unreliable on this hardware (see `poller.py`'s
  docstring and the original channel-planning spec's trust-filtering
  section), and `poll_ap`/`poll_neighbors` use generous
  `timeout=3, retries=1` because these HP MSM APs can be slow. A
  dedicated metrics-polling cadence adds real SNMP load on top of
  `aplist-plan`'s existing hourly walk — deliberately chosen as
  **15 minutes** (4x the existing hourly load) rather than a more
  aggressive interval, to keep this proportionate. It's a single cron
  string, trivially adjustable later once real-world load is observed.

## Components

### `aplist-metrics` (new Nomad job)

A new batch/periodic job, `aplist-metrics.nomad.hcl`, cron `*/15 * * * *`,
following the existing `aplist-gen`/`aplist-plan` pattern exactly (own
Nomad variable for `SNMP_COMMUNITY`, `AP_HOSTS`/`TARGET_ESSID` env vars).

Its job, every run:
1. Poll own-radio state for all cluster APs (`poller.poll_all` — reused
   as-is).
2. Poll the RF neighbor-scan table for external networks
   (`neighbor_scan.poll_all_neighbors` — reused as-is). No trust-cache
   filtering; every sighting this run gets recorded.
3. Format everything into Prometheus exposition format and push it to
   VictoriaMetrics's HTTP import endpoint.

No channel-planning computation (`plan_channels` is never called), no
file writes, no dependency on `aplist-plan`'s trust cache.

### `metrics.py` (new module in `src/aplist_gen/`)

Pure formatting functions, no I/O — same style/testability as
`core.py`/`planner.py`:

- `format_own_radio_metrics(records: list[APRecord]) -> str` — per-AP
  current channel, keyed by host/bssid/essid, as a
  `aplist_own_channel` gauge.
- `format_neighbor_metrics(sightings: list[ScanEntry]) -> str` — aggregated
  **per channel**, not per sighting: `aplist_external_network_count` and
  `aplist_external_signal_dbm_max` (strongest signal seen on that channel
  this poll), labeled by channel/band only. Deliberately not labeled by
  BSSID or ESSID — this cluster sees 100+ unique external devices in
  range (confirmed via live polling during the original channel-planning
  work), most appearing briefly and never again as neighbours' devices
  come and go. A label with that many distinct, churning values is a
  well-known way to blow up a time-series database's storage
  (one series per unique label combination, most of which are seen once
  and then abandoned) — aggregating to "how congested is this channel"
  is also the actually useful question for the stated goal, not "which
  specific device."
- `format_poll_failures(hosts: list[str], failed_hosts: list[str]) -> str`
  — `aplist_poll_failure` gauge (1/0) per host, covering both the
  own-radio and neighbor-scan poll attempts.
- `format_user_counts(...) -> str` — `aplist_connected_users` gauge per
  radio, sourced from `coDot11NumberOfUsers`
  (`1.3.6.1.4.1.8744.5.4.1.1.1.6` — same table as the already-fixed
  `coDot11BSSID`, same `<radio>.<vap>` indexing, so this reuses the
  existing SNMP walk pattern directly). This is the "busiest AP" data
  point from the earlier `planner.py` work, finally wired up.
- `push_metrics(url: str, text: str) -> None` — a thin wrapper around a
  plain HTTP POST (Python's stdlib `urllib.request` — no new dependency)
  to VictoriaMetrics's `/api/v1/import/prometheus` endpoint.

### `victoria-metrics` (new Nomad job)

A new Nomad **service** job, `victoria-metrics.nomad.hcl`, running the
single-node VictoriaMetrics image, with its own host volume for on-disk
time-series data (default retention: VictoriaMetrics's own default,
1 month — no strong constraint here, revisit once real disk usage is
observed). Registered via Nomad-native service discovery only — **no
Traefik exposure** in this phase, since the only consumers
(`aplist-metrics`, pushing) run on the same Nomad cluster and can reach
it by Nomad service DNS. Add a Traefik route later if/when a Grafana
dashboard or direct browser access is needed.

## Data flow

```
poller.poll_all(AP_HOSTS)         neighbor_scan.poll_all_neighbors(AP_HOSTS, own_essid)
        |                                         |
   list[APRecord]                          list[ScanEntry] (external only,
        |                                   untrusted/unfiltered)
        |                                         |
        +-------------------+  +--------------------+
                             |  |
                    metrics.py formatting functions
                             |
                  Prometheus exposition text
                             |
              HTTP POST to VictoriaMetrics import API
                             |
                    (Nomad-internal service DNS)
```

## Error handling

- Per-host SNMP failures follow the same warn-and-skip pattern already
  established in `poll_all`/`poll_all_neighbors` — a failed host is
  recorded as a `aplist_poll_failure` metric point (1) rather than
  blocking the run.
- A failed push to VictoriaMetrics (network error, endpoint down) is a
  warning, not fatal — matches the established pattern for the trust
  cache and plan-report writes elsewhere in this codebase. There's
  nothing else for this job to do if the push fails (no file to write,
  no fallback), so it just logs and exits.
- VictoriaMetrics being down doesn't affect `aplist-gen` or
  `aplist-plan` at all — they have no dependency on it.

## Testing

- `metrics.py`'s formatting functions: unit tests in the same
  pure-function style as `core.py`/`planner.py` — fixed
  `APRecord`/`ScanEntry` inputs, asserting exact Prometheus-format
  output strings.
- `push_metrics`'s actual HTTP call: untested against a live endpoint,
  matching this codebase's existing convention for network-touching
  glue (`poller.py`, `neighbor_scan.py`).
- `aplist-metrics.nomad.hcl`/`victoria-metrics.nomad.hcl`: validated
  with `nomad job validate`, matching every other job file in this repo.

## Future phases (not in this spec)

- **River-based online learning**, once there's real accumulated
  history to learn from — e.g. deriving DFS-avoidance windows per host
  from actual observed `aplist_connected_users` patterns instead of the
  hand-guessed windows in `planner.py`'s `CRITICAL_DFS_HOST_WINDOWS`, or
  predicting per-channel external congestion likelihood. Deliberately
  scoped out of this spec — brainstorm it as its own sub-project once
  this recording pipeline has been running long enough to have data
  worth learning from.
- Traefik exposure for VictoriaMetrics + a Grafana dashboard, if/when
  browsing the data directly (rather than just feeding a future learning
  component) becomes valuable.
