job "aplist-metrics" {
  type = "batch"

  datacenters = ["home"]

  periodic {
    cron             = "*/15 * * * *"
    prohibit_overlap = true
  }

  group "aplist-metrics" {
    task "aplist-metrics" {
      driver = "podman"

      config {
        # TEMPORARY: same locally-built image as aplist-gen/aplist-plan --
        # see their config blocks for why (registry auth not yet set up
        # on this Nomad client for the podman driver).
        image        = "localhost/aplist:latest"
        network_mode = "host"
        args         = ["metrics"]
      }

      env {
        AP_HOSTS     = "192.0.2.80,192.0.2.149,192.0.2.78"
        TARGET_ESSID = "My Network"
        # Single-node cluster (confirmed 2026-08-22): aplist-metrics and
        # victoria-metrics run on the same host. NOT 127.0.0.1 though --
        # victoria-metrics's group `network { port }` stanza uses Nomad's
        # bridge/CNI networking, which DNAT-publishes the static port only
        # on the node's advertised LAN address (192.0.2.1), not on
        # loopback -- confirmed live 2026-08-22 (`ss -tlnp` showed the
        # listener bound to 192.0.2.1:8428 only). Revisit if this
        # cluster ever goes multi-node or victoria-metrics's job switches
        # to host networking.
        METRICS_URL  = "http://192.0.2.1:8428/api/v1/import/prometheus"
      }

      # SNMP community pulled from a Nomad variable, not a flat file or
      # systemd EnvironmentFile -- this job needs its OWN Nomad variable
      # set before first deploy (separately from aplist-gen/aplist-plan's,
      # per Nomad's workload-identity ACL policy). Set it with:
      #   nomad var put nomad/jobs/aplist-metrics community=public
      template {
        data        = <<EOH
SNMP_COMMUNITY={{ with nomadVar "nomad/jobs/aplist-metrics" }}{{ .community }}{{ end }}
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
