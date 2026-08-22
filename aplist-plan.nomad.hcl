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
    # Host volume "aplist-cache" -- holds only the small trust-cache JSON
    # file (BSSID -> seen-count) used to require repeat sightings before
    # trusting an external network -- not the served aplist.xml, which
    # stays on aplist-gen's separate "aplist-web" volume.
    volume "aplist-plan-cache" {
      type      = "host"
      read_only = false
      source    = "aplist-cache"
    }

    # Same host volume aplist-gen.nomad.hcl serves aplist.xml from -- the
    # plan report is written alongside it as index.html, so nginx serves
    # it as the directory's default page. Needs write access for that
    # file; this job never touches aplist.xml itself.
    volume "aplist-web" {
      type      = "host"
      read_only = false
      source    = "aplist-web"
    }

    task "aplist-plan" {
      driver = "podman"

      config {
        # TEMPORARY: same locally-built image as aplist-gen -- see its
        # config block for why (registry auth not yet set up on this
        # Nomad client for the podman driver).
        image = "localhost/aplist:latest"
        args  = ["plan"]
      }

      volume_mount {
        volume      = "aplist-plan-cache"
        destination = "/cache"
        read_only   = false
      }

      volume_mount {
        volume      = "aplist-web"
        destination = "/output"
        read_only   = false
      }

      env {
        AP_HOSTS         = "192.0.2.80,192.0.2.149,192.0.2.78"
        TARGET_ESSID     = "My Network"
        PLAN_CACHE_PATH  = "/cache/plan_cache.json"
        PLAN_REPORT_PATH = "/output/index.html"
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
