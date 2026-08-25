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
        args  = ["-storageDataPath=/storage"]
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
          "traefik.http.routers.victoria-metrics.middlewares=platform@file,internal@file",
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
