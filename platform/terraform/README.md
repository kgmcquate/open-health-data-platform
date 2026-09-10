# Terraform — DigitalOcean Kubernetes

Provisions the managed cluster and the Spaces bucket, then leaves the application stack to Helm. The old self-hosted k3s-on-droplet bootstrap has been removed.

DNS lives in Cloudflare (`kevinmcquate.com` is a Cloudflare zone). This stack manages the `*.ohdp.kevinmcquate.com` A records through the `cloudflare` provider, DNS-only (not proxied), pointing each host at the Traefik load balancer IP configured in `loadbalancer_ip`. There is no reserved DigitalOcean IP fallback here; the value must be set explicitly in `terraform.tfvars` or via `TF_VAR_loadbalancer_ip`.

## What it creates

| Resource | Notes |
|---|---|
| `digitalocean_kubernetes_cluster` | Managed cluster in the chosen region |
| `digitalocean_kubernetes_node_pool` | Default worker pool (`size`, `node_count`) |
| `digitalocean_spaces_bucket` | `ohdp-warehouse` for snapshots + backups |
| `cloudflare_dns_record` | One A record per `dns_hostnames` entry under `dns_base`, once `loadbalancer_ip` is set |

This setup intentionally uses a managed DigitalOcean Kubernetes cluster instead of a single self-hosted k3s node. The cluster endpoint and kubeconfig are surfaced via Terraform outputs.

## Usage

```bash
cp terraform.tfvars.example terraform.tfvars   # gitignored; fill it in
terraform init
terraform plan
terraform apply
```

Then pull the kubeconfig:

```bash
terraform output -raw kubeconfig > kubeconfig
chmod 600 kubeconfig
export KUBECONFIG=$PWD/kubeconfig
kubectl get nodes
```

State is local until the Spaces bucket exists. After the first apply, uncomment the `backend "s3"` block in `backend.tf` and run `terraform init -migrate-state`.

## Things that will bite you

- **`admin_ip_ranges` gates the Kubernetes API.** If your IP is dynamic, expect to update this.
- **`user_data` is in `ignore_changes`.** Editing the cloud-init template will not re-bootstrap an existing node.
- **Spaces and S3-compatible backends use the AWS S3 compatibility layer.** Keep `force_path_style = true` and the access keys in GitHub secrets.
- **DigitalOcean does not have the Cloudflare edge WAF.** The DNS records are DNS-only (grey cloud), so the origin LB IP is public and there is no edge WAF or rate limiting. If you want that, switch the records to proxied (orange cloud) — which also requires moving cert-manager to a DNS-01 solver or a Cloudflare Origin CA cert, since HTTP-01 breaks behind the proxy.
- **The DNS records point to the configured Traefik public IP.** Set `loadbalancer_ip` explicitly in `terraform.tfvars` or `TF_VAR_loadbalancer_ip`; if the ingress IP changes, update the variable and re-apply this stack.
- **`CLOUDFLARE_API_TOKEN` must be exported and have `DNS:Edit` on the zone.** The provider reads it from the environment, not a tfvar. A token scoped to the wrong zone or missing the permission fails at apply, not plan.
