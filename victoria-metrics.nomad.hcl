job "victoria-metrics" {
  type = "service"

  datacenters = ["home"]

  group "victoria-metrics" {
    count = 1

    network {
      port "http" {
        static = 8428
        to     = 8428
      }
    }

    # Runs as root by default (confirmed via `podman run --rm
    # --entrypoint id <image>`), unlike aplist-gen's UID 999 -- no
    # chown needed on this host volume.
    volume "victoria-metrics-data" {
      type      = "host"
      read_only = false
      source    = "victoria-metrics-data"
    }

    task "victoria-metrics" {
      driver = "podman"

      config {
        image = "docker.io/victoriametrics/victoria-metrics:v1.102.0"
        ports = ["http"]
        args  = ["-storageDataPath=/storage", "-vmui.customDashboardsPath=/local/dashboards"]
      }

      template {
        data        = <<EOH
{
  "title": "aplist",
  "rows": [
    {
      "panels": [
        { "title": "Own channel per radio", "width": 6, "expr": ["aplist_own_channel"] },
        {
          "title": "Connected users by AP and band",
          "width": 6,
          "expr": ["sum(aplist_connected_users) by (host, band)"]
        }
      ]
    },
    {
      "panels": [
        { "title": "SNR per radio (dB)", "width": 6, "expr": ["aplist_snr_db"] },
        { "title": "Noise floor per radio (dBm)", "width": 6, "expr": ["aplist_noise_floor_dbm"] }
      ]
    },
    {
      "panels": [
        {
          "title": "Retry/failure rate per radio",
          "width": 12,
          "expr": [
            "rate(aplist_retry_count[1h])",
            "rate(aplist_failed_count[1h])",
            "rate(aplist_ack_failure_count[1h])"
          ]
        }
      ]
    },
    {
      "panels": [
        { "title": "Busiest external channels", "width": 6, "expr": ["topk(10, aplist_external_network_count)"] },
        { "title": "Poll failures", "width": 6, "expr": ["aplist_poll_failure"] }
      ]
    },
    {
      "panels": [
        { "title": "Config-change events (per aplist-metrics poll)", "width": 12, "expr": ["aplist_config_change_count"] }
      ]
    },
    {
      "panels": [
        {
          "title": "Uplink bandwidth by AP (bytes/sec, both radios combined)",
          "width": 12,
          "expr": ["rate(aplist_uplink_rx_bytes[1h])", "rate(aplist_uplink_tx_bytes[1h])"]
        }
      ]
    }
  ]
}
EOH
        destination = "local/dashboards/aplist.json"
      }

      # wan-monitor's metrics (separate project, see its own repo) --
      # bare metric names so vmui shows all label combinations (one line
      # per target/interface) automatically, same pattern as aplist.json.
      template {
        data        = <<EOH
{
  "title": "wan",
  "rows": [
    {
      "panels": [
        { "title": "Latency by target (ms)", "width": 12, "expr": ["wan_latency_ms"] }
      ]
    },
    {
      "panels": [
        { "title": "Packet loss by target (%)", "width": 12, "expr": ["wan_packet_loss_percent"] }
      ]
    },
    {
      "panels": [
        {
          "title": "bond0.100 throughput (bytes/sec)",
          "width": 6,
          "expr": ["rate(wan_interface_rx_bytes[5m])", "rate(wan_interface_tx_bytes[5m])"]
        },
        {
          "title": "bond0.100 errors/drops per sec",
          "width": 6,
          "expr": [
            "rate(wan_interface_rx_errors[5m])",
            "rate(wan_interface_tx_errors[5m])",
            "rate(wan_interface_rx_dropped[5m])",
            "rate(wan_interface_tx_dropped[5m])"
          ]
        }
      ]
    }
  ]
}
EOH
        destination = "local/dashboards/wan.json"
      }

      volume_mount {
        volume      = "victoria-metrics-data"
        destination = "/storage"
        read_only   = false
      }

      service {
        name         = "victoria-metrics"
        port         = "http"
        provider     = "nomad"
        address_mode = "host"

        tags = [
          "traefik.enable=true",
          # Bare-root redirect to the built-in UI -- regex only matches
          # when the path is exactly "/", so /vmui/* itself and the API
          # endpoints pass through untouched. internal@file/platform@file
          # run first so access control and forced-HTTPS are enforced
          # before the redirect ever fires.
          "traefik.http.middlewares.victoria-metrics-root.redirectregex.regex=^(https?://[^/]+)/$",
          "traefik.http.middlewares.victoria-metrics-root.redirectregex.replacement=$${1}/vmui/",
          "traefik.http.middlewares.victoria-metrics-root.redirectregex.permanent=true",
          "traefik.http.routers.victoria-metrics.middlewares=internal@file,platform@file,victoria-metrics-root",
          "traefik.http.routers.victoria-metrics.rule=Host(`victoria-metrics.example.com`)",
        ]

        check {
          type     = "http"
          path     = "/health"
          interval = "10s"
          timeout  = "5s"
        }
      }

      resources {
        cpu    = 200
        memory = 256
      }
    }
  }
}
