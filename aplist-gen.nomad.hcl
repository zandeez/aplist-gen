job "aplist-gen" {
  type = "batch"

  # TODO: confirm the real datacenter name for the home Nomad cluster —
  # "home" is a placeholder, not a value pulled from your actual config.
  datacenters = ["home"]

  periodic {
    cron             = "*/5 * * * *"
    prohibit_overlap = true
  }

  group "aplist-gen" {
    # TODO: confirm this host volume name/source matches whatever the
    # existing nginx job (serving aplist.xml) already mounts.
    volume "aplist-web" {
      type      = "host"
      read_only = false
      source    = "aplist-web"
    }

    task "aplist-gen" {
      driver = "podman"

      config {
        # TODO: pin to a real tag once the CI pipeline has built one —
        # "main" matches the ref-slug tag the manifest job publishes.
        image = "registry.gitlab.com/zandeez/aplist-gen:main"
      }

      volume_mount {
        volume      = "aplist-web"
        destination = "/output"
        read_only   = false
      }

      env {
        # TODO: extend with the other two APs' IPs once deployed.
        AP_HOSTS     = "192.0.2.139"
        OUTPUT_PATH  = "/output/aplist.xml"
        TARGET_ESSID = "My Network"
      }

      # SNMP community pulled from a Nomad variable, not a flat file or
      # systemd EnvironmentFile. Set it with:
      #   nomad var put nomad/jobs/aplist-gen community=public
      template {
        data        = <<EOH
SNMP_COMMUNITY={{ with nomadVar "nomad/jobs/aplist-gen" }}{{ .community }}{{ end }}
EOH
        destination = "secrets/snmp.env"
        env         = true
      }

      resources {
        cpu    = 100
        memory = 64
      }
    }
  }
}
