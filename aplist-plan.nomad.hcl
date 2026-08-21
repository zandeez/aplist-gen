job "aplist-plan" {
  type = "batch"

  # Matches aplist-gen.nomad.hcl -- see its TODO note on confirming the
  # real datacenter name for the home Nomad cluster.
  datacenters = ["home"]

  periodic {
    cron             = "0 * * * *"
    prohibit_overlap = true
  }

  group "aplist-plan" {
    # TODO: create this host volume on the Nomad client before first
    # deploy. It holds only the small trust-cache JSON file (BSSID ->
    # seen-count) used to require repeat sightings before trusting an
    # external network -- not the served aplist.xml, which stays on
    # aplist-gen's separate "aplist-web" volume.
    volume "aplist-plan-cache" {
      type      = "host"
      read_only = false
      source    = "aplist-plan-cache"
    }

    task "aplist-plan" {
      driver = "podman"

      config {
        # Same image as aplist-gen -- see its TODO note on pinning a
        # real tag once the CI pipeline has built one.
        image = "registry.gitlab.com/zandeez/aplist-gen:main"
        args  = ["plan"]
      }

      volume_mount {
        volume      = "aplist-plan-cache"
        destination = "/cache"
        read_only   = false
      }

      env {
        AP_HOSTS        = "192.0.2.80,192.0.2.149,192.0.2.78"
        TARGET_ESSID    = "My Network"
        PLAN_CACHE_PATH = "/cache/plan_cache.json"
      }

      # SNMP community pulled from a Nomad variable, not a flat file or
      # systemd EnvironmentFile. Under Nomad's default workload-identity
      # ACL policy a task can only read its own job's nomad/jobs/<job-id>
      # variable path, not another job's -- so this job needs its OWN
      # Nomad variable set before first deploy (separately from
      # aplist-gen's). Set it with:
      #   nomad var put nomad/jobs/aplist-plan community=public
      template {
        data        = <<EOH
SNMP_COMMUNITY={{ with nomadVar "nomad/jobs/aplist-plan" }}{{ .community }}{{ end }}
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
