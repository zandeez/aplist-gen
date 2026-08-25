job "victoria-logs" {
  type = "service"

  datacenters = ["home"]

  group "victoria-logs" {
    count = 1

    network {
      port "http" {
        static = 9428
        to     = 9428
      }
      # Syslog ingest -- the 3 APs are configured (manually, on the AP
      # side) to send here directly, so unlike victoria-metrics's HTTP
      # port this one is genuinely reached from off-host, not just from
      # another Nomad job on the same node.
      port "syslog" {
        static = 514
        to     = 514
      }
    }

    # Phase 1 only: capture and store raw syslog for observation. No
    # parsing/correlation with the DFS-vs-ARM question yet -- that's
    # deferred until real message content is seen (see
    # project_aplist_overview memory for the full rationale).
    volume "victoria-logs-data" {
      type      = "host"
      read_only = false
      source    = "victoria-logs-data"
    }

    task "victoria-logs" {
      driver = "podman"

      config {
        image = "docker.io/victoriametrics/victoria-logs:v1.52.0"
        ports = ["http", "syslog"]
        args = [
          "-storageDataPath=/storage",
          "-retentionPeriod=30d",
          "-syslog.listenAddr.udp=:514",
        ]
      }

      volume_mount {
        volume      = "victoria-logs-data"
        destination = "/storage"
        read_only   = false
      }

      service {
        name         = "victoria-logs"
        port         = "http"
        provider     = "nomad"
        address_mode = "host"

        tags = [
          "traefik.enable=true",
          # Bare-root redirect to the built-in UI -- see victoria-metrics
          # for why the regex only fires on an exact "/" path.
          "traefik.http.middlewares.victoria-logs-root.redirectregex.regex=^(https?://[^/]+)/$",
          "traefik.http.middlewares.victoria-logs-root.redirectregex.replacement=$${1}/select/vmui/",
          "traefik.http.middlewares.victoria-logs-root.redirectregex.permanent=true",
          "traefik.http.routers.victoria-logs.middlewares=internal@file,platform@file,victoria-logs-root",
          "traefik.http.routers.victoria-logs.rule=Host(`victoria-logs.example.com`)",
        ]

        check {
          type     = "http"
          path     = "/health"
          interval = "10s"
          timeout  = "5s"
        }
      }

      resources {
        cpu    = 100
        memory = 128
      }
    }
  }
}
