# aplist-gen

A Python toolkit for operating a small cluster of HP MSM access points
over SNMP: generating the `aplist.xml` a client-side WiFi scanner needs,
recommending non-interfering channel assignments, and pushing
observability metrics to [VictoriaMetrics](https://victoriametrics.com/)
and [victoria-logs](https://docs.victoriametrics.com/victorialogs/).

```
aplist-gen generate   # poll APs, write aplist.xml (the default command)
aplist-gen plan       # dry-run channel recommendations, no state change
aplist-gen metrics    # push per-radio/uplink/interference metrics
```

## Why

Three home/office APs on the same ESSID need non-overlapping channels,
but "non-overlapping" isn't static: the right assignment depends on
what channels are already congested by neighbouring networks and on
when a DFS radar-vacate would actually disrupt someone. `plan` scores
every candidate channel combination (co-channel cost within the
cluster weighted higher than external interference, since clients
actively roam between your own APs; a DFS penalty that gets much
heavier during hours a radio is in active use) and recommends the
cheapest assignment, brute-forced per band since a few radios times a
few dozen candidate channels is a small enough search space to just
enumerate.

External interference readings are filtered through an on-disk trust
cache (`trust_cache.py`) that requires a neighbouring network to show
up in two consecutive polls before it counts — a single noisy SNMP
read shouldn't skew the plan.

## Layout

- `core.py` — `APRecord`/`ScanEntry` value types, `aplist.xml`
  rendering.
- `poller.py` / `snmp_utils.py` — async SNMP polling of each AP's own
  radio config (BSSID, ESSID, channel, SNR, retry/failure counters).
- `neighbor_scan.py` — SNMP polling of the RF neighbor-scan table, for
  *external* networks only (an AP doesn't reliably see its own
  transmissions in its own neighbor scan).
- `planner.py` — the channel-scoring/assignment algorithm.
- `trust_cache.py` — two-poll trust gate for external sightings.
- `metrics.py` — Prometheus-text-format metric formatting + push.
- `io_utils.py` — atomic write-then-`os.replace` output, so a crash
  mid-write never corrupts the last-good `aplist.xml`.

## Running it

```bash
uv sync
AP_HOSTS=192.0.2.10,192.0.2.11,192.0.2.12 \
SNMP_COMMUNITY=public \
TARGET_ESSID="My Network" \
OUTPUT_PATH=aplist.xml \
uv run aplist-gen generate
```

`SNMP_COMMUNITY` should come from a secret store in any real deployment
(the Nomad job below reads it from a Nomad variable, never a flat
file). `metrics` additionally reads `METRICS_URL` and `LOGS_URL`.

## Deploying

Four Nomad job files, each a small, separately-schedulable batch/service:

- `aplist-gen.nomad.hcl` — periodic `generate`, every 5 minutes.
- `aplist-plan.nomad.hcl` — periodic `plan`, writes an HTML report.
- `aplist-metrics.nomad.hcl` — periodic `metrics` push.
- `aplist-nginx.nomad.hcl` — serves the generated `aplist.xml`.
- `victoria-metrics.nomad.hcl` / `victoria-logs.nomad.hcl` — the metrics/log
  backends these push to.

`.gitlab-ci.yml` builds and pushes multi-arch (amd64/arm64) container
images natively on GitLab's own SaaS ARM64/AMD64 runners (buildah,
no QEMU emulation), then assembles a combined manifest per tag.

## Design docs

`docs/superpowers/` holds the spec-and-plan documents this project was
actually built from (channel planning, metrics, metrics expansion) —
left in as a record of the design reasoning and trade-offs behind
`planner.py` and the metrics pipeline, not just the resulting code.

## Tests

```bash
uv run pytest
```
