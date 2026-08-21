# Channel planning & interference avoidance — design

Date: 2026-08-21
Status: approved, phase 1 (dry-run) scope

## Goal

`aplist-gen` currently only reads and exports each AP's *own* configured
channel. This adds a channel-planning capability that recommends channel
assignments to reduce both:

- **Co-channel/adjacent-channel interference within the cluster** (the 3
  My Network APs stepping on each other), and
- **Interference from external networks** (neighbouring households'
  Wi-Fi visible over RF).

This is phase 1 of a deliberately incremental path toward controller-style
automation: this phase computes and reports recommendations only. It does
not write anything back to the APs. A later phase adds an `--apply` mode
that pushes changes via SNMP SET, and a further phase could swap the
scoring algorithm for a more globally-optimal cost-minimization search —
see "Future phases" below.

## Background / constraints

- Cluster is 3 HP MSM APs (`AP_HOSTS`), each with a 2.4GHz and a 5GHz
  radio, polled today by the existing `aplist-gen` batch job every 5
  minutes (`aplist-gen.nomad.hcl`, `cron = "*/5 * * * *"`). That job and
  its cadence are unchanged by this work.
- `poller.py` currently deliberately avoids the RF neighbor-scan SNMP
  table for own-radio state, because self-hearing was found unreliable on
  this hardware (see its docstring). This design reintroduces that table,
  but only to detect *external* networks, and treats single-poll sightings
  as untrustworthy (see "Trust filtering" below) rather than assuming the
  table is reliable.
- Deployment is a home Nomad cluster, coastal location. DFS channels are
  allowed as scoring candidates on 5GHz (per explicit request) but are
  penalized in scoring: coastal areas see more weather/maritime radar
  traffic, which increases the odds of a forced DFS radar-vacate (the AP
  must abandon the channel and re-run a Channel Availability Check, up to
  ~10 minutes, before reusing it). Handling radar-vacate events and CAC
  timing is explicitly out of scope for phase 1 (dry-run only, so a
  forced vacate doesn't interact with this feature yet) — it becomes
  relevant when the `--apply` phase lands.

## Components

### `neighbor_scan.py` (new)

Polls each host's RF neighbor-scan SNMP table — the one `poller.py`
currently avoids — but only to find *external* networks, not for
self-hearing of the cluster's own state.

- `poll_neighbors(host: str, community: str, own_essid: str) -> list[ScanEntry]`
  — same SNMP walk pattern as `poller.poll_ap`, filtered to exclude
  entries matching `own_essid` (the inverse of `core.filter_cluster_aps`).
- `poll_all_neighbors(hosts, community, own_essid) -> list[ScanEntry]` —
  concurrent per-host polling via `asyncio.gather(..., return_exceptions=True)`,
  matching the existing `poll_all` in `__init__.py`: a failed host is
  logged as a warning and skipped, never fatal.

### Trust filtering (new, small persistent cache)

External sightings are noisy (that's exactly why self-hearing was
abandoned for own-radio state). A sighting only counts toward interference
scoring once it has appeared in more than one `plan` run.

- On-disk cache (path via env var, e.g. `PLAN_CACHE_PATH`) keyed by
  BSSID, storing last-seen channel and a seen-count.
- Missing or corrupt cache is treated as empty (cold start), not an
  error — this is a best-effort trust signal, not a source of truth.
- A sighting is "trusted" (included in scoring) once seen-count ≥ 2.

### `planner.py` (new)

Pure functions, same style/testability as `core.py` — no I/O.

```python
@dataclass(frozen=True)
class ChannelRecommendation:
    bssid: str
    essid: str
    current_channel: int
    recommended_channel: int
    reason: str

def plan_channels(
    own_records: list[APRecord],
    trusted_external: list[ScanEntry],
    candidates_24g: list[int],
    candidates_5g: list[int],
    dfs_channels: set[int],
) -> list[ChannelRecommendation]: ...
```

- Candidate channels: `{1, 6, 11}` for 2.4GHz; a fixed list for 5GHz that
  includes DFS channels, sourced from the local regulatory domain.
- Scoring (per candidate channel, per AP): cost = co-channel/adjacent-
  channel overlap with the *other* cluster APs' currently-assigned or
  already-recommended channels (assigned greedily, one AP at a time) +
  weighted cost from trusted external sightings on/near that channel,
  weighted by their signal strength. DFS channels get an added fixed
  penalty so they're only recommended when clearly better than the best
  non-DFS option.
- Greedy assignment: process own APs in a stable order (sorted by BSSID),
  pick the lowest-cost candidate for each in turn, so later APs account
  for earlier APs' picks.
- `reason` is a short human-readable string, e.g. `"channel 6 overlaps
  cluster AP AA:BB:...:02 and external network 'NeighboursWifi' (-52dBm)"`,
  so dry-run output is auditable rather than a black box.

### CLI: `aplist-gen plan`

New subcommand, dry-run only in phase 1.

- Runs `poll_all` (existing, own-radio) and `poll_all_neighbors` (new)
  concurrently, updates the trust cache, filters to trusted sightings,
  calls `plan_channels`, and prints a plain-text report to stdout: one
  row per AP with BSSID, ESSID, current → recommended channel, and reason
  (blank/omitted reason when no change is recommended).
- No file writes, no SNMP SET. Exit code 0 regardless of whether changes
  are recommended (this is a report, not a check).
- If neighbor-scan data is entirely unavailable (e.g. every host's
  neighbor-scan poll fails), the planner still runs using only
  own-cluster co-channel scoring — external cost contributes zero rather
  than blocking the report.

### Deployment

New Nomad periodic batch job (e.g. `aplist-plan.nomad.hcl`), same
container image as `aplist-gen`, different command/args, its own
(infrequent, e.g. hourly) cron schedule — decoupled from the existing
5-minute `aplist-gen` export job so a bug in the new, less-proven code
path can't affect the working exporter.

## Data flow

```
poll_all(AP_HOSTS)              poll_all_neighbors(AP_HOSTS, own_essid)
        |                                    |
   list[APRecord]                    list[ScanEntry] (external only)
        |                                    |
        |                          update trust cache; filter to
        |                          seen-count >= 2
        |                                    |
        +--------------> plan_channels <-----+
                               |
                    list[ChannelRecommendation]
                               |
                     plain-text report -> stdout
```

## Error handling

- Per-host SNMP failures (own-radio or neighbor-scan) are logged as
  warnings and that host is skipped — never fatal, matching the existing
  `poll_all` philosophy.
- Neighbor-scan being fully unavailable degrades scoring gracefully
  (own-cluster-only) rather than failing the run.
- Trust cache read/write failures are non-fatal; treated as a cold cache.

## Testing

- `planner.py`: unit tests in the same pure-function style as
  `tests/test_core.py` — fixed `APRecord`/`ScanEntry` inputs, asserting
  exact `ChannelRecommendation` output including `reason` text. Covers:
  2.4GHz co-channel overlap, DFS penalty behavior (a DFS channel is only
  recommended when strictly better than the best non-DFS candidate), and
  external-sighting trust filtering (single-poll sighting excluded,
  repeated sighting included).
- `neighbor_scan.py`: only its pure parsing/filtering helpers are unit
  tested; the live SNMP walk itself is untested against real hardware,
  matching `poller.py`'s current pattern.

## Future phases (not in this spec)

- `--apply` mode: push recommended channels back via SNMP SET, gated
  behind an explicit flag/manual trigger before ever running unattended.
- Cost-minimization search (approach B from brainstorming): replace the
  greedy per-AP assignment with a brute-force/local-search minimization
  over the full assignment space — feasible at this scale (3 APs, small
  candidate lists) — once dry-run recommendations have been trusted for a
  while.
- DFS radar-vacate handling: once `--apply` exists, track forced
  channel-vacate events and CAC timing so automation doesn't fight the
  AP's own radar-safety behavior.
- **5GHz channel-width awareness** (known limitation, flagged in final
  review of the phase 1 implementation): `_overlap_units` in `planner.py`
  only treats two 5GHz channels as overlapping when they're identical,
  which implicitly assumes every 5GHz radio runs 20MHz channels. At
  40MHz or 80MHz bonding, channels the scorer treats as independent
  (e.g. 36/40, or the whole 36-48 block at 80MHz) actually overlap or are
  literally the same channel — so a `plan` recommendation could collapse
  a well-separated cluster onto one bonded block if a radio is running
  wider than 20MHz. Fixing this properly needs a new per-radio
  channel-width SNMP OID, discovered the same way Task 6 discovered the
  neighbor-scan OIDs. Until that's done: **do not act on a `plan` 5GHz
  recommendation without first manually confirming the affected radios'
  configured channel width is 20MHz.**
