# Terraform — Kamatera + Cloudflare

Provisions the single VM, its DNS, and R2 buckets, and bootstraps k3s + the host
firewall via the server startup script. Everything above the cluster is Helm's
job, not Terraform's — see
[ADR-0005](../../docs/decisions/0005-hetzner-k3s-over-managed-kubernetes.md).

## What it creates

| Resource | Notes |
|---|---|
| `kamatera_server` | CPU type B, 8 vCPU / 32 GB / 300 GB (§4) |
| `cloudflare_record.subdomain` / `.apex` | Proxied A records for the apex and each subdomain |
| `cloudflare_record.k3s_api` | **Unproxied** `k3s.<domain>` — the API server (6443) can't be proxied |
| `cloudflare_r2_bucket` | `ohdp-warehouse` (snapshots + pg_dump); `ohdp-tfstate` is manual |

Kamatera has no cloud firewall or reserved-IP resource, so:

- **The firewall is nftables on the host**, rendered into the startup script:
  `admin_ip_ranges` reach 22/6443, Cloudflare ranges reach 80/443, nothing else.
  A `refresh-cloudflare-nft.timer` re-pulls the Cloudflare ranges daily, since
  the startup script runs only at first boot.
- **The public IP is assigned at create time**, not reserved. It only changes if
  the server is recreated; the Cloudflare records are Terraform-managed and
  repoint automatically on the next apply.

Cloudflare-only ingress on 80/443 is what makes the edge WAF and rate limiting in
§5 unbypassable, and it means **cert-manager must use the DNS-01 challenge, not
HTTP-01** — Let's Encrypt cannot reach the origin directly.

## Usage

```bash
cp terraform.tfvars.example terraform.tfvars   # gitignored; fill it in
export KAMATERA_API_CLIENT_ID=... KAMATERA_API_SECRET=...
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

`kubectl` talks to `https://k3s.<domain>:6443`, which resolves (unproxied) to the
node and is gated by nftables to `admin_ip_ranges`.

State is local until the R2 buckets exist. After the first apply, `terraform init`
with the `backend "s3"` config (the deploy-infra workflow passes it) and
`-migrate-state`.

## Things that will bite you

- **`admin_ip_ranges` gates SSH and the Kubernetes API.** A validation rule
  rejects `0.0.0.0/0`. If your IP is dynamic, expect to update this — and note a
  change only takes effect on a rebuild (it's baked into the startup script),
  unlike the daily-refreshed Cloudflare set.
- **`startup_script` and `image_id` are in `ignore_changes`.** Editing the
  startup template will *not* re-bootstrap an existing node; a change there would
  otherwise recreate the server and wipe the cluster. Re-run it by hand, or
  `terraform taint kamatera_server.node` to take the rebuild deliberately.
- **Cloudflare provider is pinned to v4.** v5 renamed `cloudflare_record` to
  `cloudflare_dns_record` and reshaped R2 resources. Bumping means rewriting
  `dns.tf` and `r2.tf`.
- **`datacenter_name` is matched as a string.** If Kamatera exposes several NY
  datacenters, the data source may error on ambiguity — check with
  `terraform console` against `data.kamatera_datacenter.node`.
