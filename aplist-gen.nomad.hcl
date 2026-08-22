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
        # TEMPORARY: locally-built image, not the CI-pushed one --
        # registry.gitlab.com/zandeez/aplist-gen:main needs registry auth
        # on this Nomad client (no per-task auth block exists for the
        # podman driver, unlike the built-in docker driver -- it's plugin-
        # level client config only). Switch back to the registry image
        # once that's set up: `podman login registry.gitlab.com` on this
        # host, or configure the nomad-driver-podman plugin's own `auth`
        # block in the client's agent config.
        image = "localhost/aplist:latest"
      }

      volume_mount {
        volume      = "aplist-web"
        destination = "/output"
        read_only   = false
      }

      env {
        AP_HOSTS     = "192.0.2.80,192.0.2.149,192.0.2.78"
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
