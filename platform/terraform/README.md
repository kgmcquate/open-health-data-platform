# Terraform — DigitalOcean Kubernetes

Provisions the managed cluster, its public DNS records, and the Spaces bucket, then leaves the application stack to Helm. The old self-hosted k3s-on-droplet bootstrap has been removed.

## What it creates

| Resource | Notes |
|---|---|
| `digitalocean_kubernetes_cluster` | Managed cluster in the chosen region |
| `digitalocean_kubernetes_node_pool` | Default worker pool (`size`, `node_count`) |
| `digitalocean_record` | A records for the apex and each subdomain |
| `digitalocean_spaces_bucket` | `ohdp-warehouse` for snapshots + backups |

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
- **DigitalOcean does not have the Cloudflare edge WAF.** If you want the same protection, add nginx/Traefik-rate limiting or a separate ingress layer in front of the node.
