job "aplist-nginx" {
  type = "service"

  datacenters = ["home"]

  # Matches this cluster's other Traefik-routed services (e.g. pyload) --
  # required for Nomad-native service discovery + the traefik.* tags below
  # to be picked up.
  constraint {
    attribute = "${attr.nomad.service_discovery}"
    value     = "true"
  }

  constraint {
    attribute = "${attr.nomad.version}"
    operator  = "semver"
    value     = ">= 1.4.0"
  }

  group "aplist-nginx" {
    count = 1

    network {
      port "http" {
        to = 80
      }
    }

    # Same host volume aplist-gen writes aplist.xml to and aplist-plan
    # writes index.html to -- this job only serves it, never writes.
    volume "aplist-web" {
      type      = "host"
      read_only = true
      source    = "aplist-web"
    }

    task "aplist-nginx" {
      driver = "podman"

      config {
        image = "docker.io/library/nginx:1.27-alpine"
        ports = ["http"]
      }

      volume_mount {
        volume      = "aplist-web"
        # nginx's default document root -- serves aplist.xml and
        # index.html directly with zero custom nginx config needed.
        destination = "/usr/share/nginx/html"
        read_only   = true
      }

      service {
        name         = "aplist"
        port         = "http"
        provider     = "nomad"
        address_mode = "host"

        tags = [
          "traefik.enable=true",
          # No "platform@file" here (forcessl/secureheaders/compression) --
          # this route deliberately allows plain HTTP, unlike this
          # cluster's other Traefik-routed services. Still restricted to
          # the internal network via "internal@file" (IP allowlist).
          "traefik.http.routers.aplist.middlewares=internal@file",
          "traefik.http.routers.aplist.rule=Host(`aplist.example.com`)",
        ]

        check {
          type     = "http"
          path     = "/"
          interval = "10s"
          timeout  = "5s"
        }
      }

      resources {
        cpu    = 50
        memory = 32
      }
    }
  }
}
