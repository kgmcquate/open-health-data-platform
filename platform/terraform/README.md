# Terraform — Hetzner + Cloudflare

Provisions the single VM, its firewall, DNS, and R2 buckets, and bootstraps k3s
via cloud-init. Everything above the cluster is Helm's job, not Terraform's —
see [ADR-0005](../../docs/decisions/0005-hetzner-k3s-over-managed-kubernetes.md).

## What it creates

| Resource | Notes |
|---|---|
| `hcloud_server` | `cx52` — 16 vCPU / 32 GB / 320 GB NVMe (§4) |
| `hcloud_primary_ip` | Stable IPv4, `prevent_destroy` so DNS survives rebuilds |
| `hcloud_firewall` | 22 + 6443 from `admin_ip_ranges`; 80/443 **from Cloudflare ranges only** |
| `cloudflare_record` | Proxied A records for the apex and each subdomain |
| `cloudflare_r2_bucket` | `ohdp-warehouse` (snapshots + pg_dump), `ohdp-tfstate` |

The firewall admitting only Cloudflare on 80/443 is deliberate: it is what makes
the edge WAF and rate limiting in §5 unbypassable. A consequence is that
**cert-manager must use the DNS-01 challenge, not HTTP-01** — Let's Encrypt
cannot reach the origin directly.

## Usage

```bash
cp terraform.tfvars.example terraform.tfvars   # gitignored; fill it in
terraform init
terraform plan
terraform apply
```

Then pull the kubeconfig:

```bash
terraform output -raw fetch_kubeconfig   # prints the command; run it
export KUBECONFIG=$PWD/kubeconfig
kubectl get nodes
```

State is local until the R2 buckets exist. After the first apply, uncomment the
`backend "s3"` block in `versions.tf` and `terraform init -migrate-state`.

## Things that will bite you

- **`admin_ip_ranges` gates the Kubernetes API.** A validation rule rejects
  `0.0.0.0/0` outright. If your IP is dynamic, expect to update this.
- **`user_data` is in `ignore_changes`.** Editing the cloud-init template will
  *not* re-bootstrap an existing node — a change there would otherwise destroy
  and recreate the server, wiping the cluster. Re-run it by hand, or take the
  rebuild deliberately.
- **Cloudflare provider is pinned to v4.** v5 renamed `cloudflare_record` to
  `cloudflare_dns_record` and reshaped R2 resources. Bumping means rewriting
  `dns.tf` and `r2.tf`.
- **`cx52` is EU-only.** Moving to a US location (`ash`, `hil`) needs `cpx51`
  (AMD, 16 vCPU / 32 GB / 360 GB) instead. See §10.1 — region is still open.
