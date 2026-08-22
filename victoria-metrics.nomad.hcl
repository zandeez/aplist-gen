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

    # TODO: create this host volume on the Nomad client before first
    # deploy, owned by whatever UID the victoria-metrics image runs
    # as (check with `podman run --rm --entrypoint id <image>` the
    # same way aplist-gen's UID 999 was confirmed -- this is a
    # different image, likely a different UID).
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
