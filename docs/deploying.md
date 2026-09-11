# Deploying

Three workflows, all runnable locally with [`act`](https://github.com/nektos/act).

| Workflow | Does | Needs a cluster? |
|---|---|---|
| [`build-images.yml`](../.github/workflows/build-images.yml) | Builds `ohdp-hub-api` and `ohdp-pipeline`, pushes to GHCR | no |
| [`deploy-infra.yml`](../.github/workflows/deploy-infra.yml) | Terraform: DigitalOcean Kubernetes cluster, Spaces bucket, Cloudflare DNS records | no |
| [`deploy-platform.yml`](../.github/workflows/deploy-platform.yml) | `helm upgrade` for each chart: platform-base, Traefik, cert-manager, external secrets, OpenSearch, OpenMetadata, authz proxy, Dagster, hub-api | when `deploy` ticked |

## Setup

```bash
brew install act            # or: https://github.com/nektos/act#installation
cp .env.example .env        # fill in the DEPLOY SECRETS half
```

`.actrc` points act at `.env` as its secrets store, so every `secrets.NAME` in a
workflow resolves to `NAME` in `.env`. The file is gitignored. Names in
`.env.example` match the workflows exactly — if you add a secret to a workflow,
add it there too.

## First-time bootstrap

Order matters, and two steps are deliberately manual.

1. **Create the Terraform state bucket by hand.** A DigitalOcean Space matching
   `bucket`/`endpoints.s3` in [`platform/terraform/backend.tf`](../platform/terraform/backend.tf).
   Terraform does not manage it — a tool storing state in a bucket it also
   creates cannot destroy that bucket. Create a Spaces access key (DO console →
   API → Spaces Keys) and put the pair in `.env` as `SPACES_ACCESS_KEY_ID` /
   `SPACES_ACCESS_KEY`.

2. **Provision infrastructure.**
   ```bash
   act workflow_dispatch -W .github/workflows/deploy-infra.yml --input action=plan
   act workflow_dispatch -W .github/workflows/deploy-infra.yml --input action=apply
   ```
   `plan` is the default; `apply` creates billed resources.

3. **Capture the kubeconfig** into `KUBECONFIG_B64` in `.env`:
   ```bash
   ssh root@<node-ip> 'cat /etc/rancher/k3s/k3s.yaml' \
     | sed "s#127.0.0.1#<node-ip>#" | base64 | tr -d '\n'
   ```

4. **Build images** and note the `sha-<12>` tag it prints.

5. **Deploy the platform.**
   ```bash
   act workflow_dispatch -W .github/workflows/deploy-platform.yml --input deploy=true
   ```
   Dagster + hub-api deploy the `sha-<12>` of the commit being run (the tag
   `build-images.yml` pushed). Pass `--input image_tag=sha-…` to pin an older
   build.

6. **Point DNS at the load balancer.** `open-health-data-platform.org` is a Cloudflare zone;
   `deploy-infra` manages the `*.open-health-data-platform.org` A records there via the
   `cloudflare` provider. They are **DNS-only** (not proxied) so the Let's
   Encrypt HTTP-01 challenge reaches the origin unproxied.

   The records target the DigitalOcean load balancer Traefik provisioned in
   step 5, which does not exist until then — so on the first `deploy-infra` run
   `loadbalancer_ip` is blank and the records are skipped. Read the IP:
   ```bash
   kubectl -n infra get svc traefik -o jsonpath='{.status.loadBalancer.ingress[0].ip}'
   ```
   Set it as the `LOADBALANCER_IP` secret (`.env` for `act`, wired to
   `TF_VAR_loadbalancer_ip`) and re-run `deploy-infra` with `action=apply`. It
   creates one A record per entry in `dns_hostnames` (`app`, `dagster`,
   `catalog`, `cube`, `superset`).

   cert-manager then issues a cert per host on the first request; check with
   `kubectl get certificate -A`. Switching a record to proxied (orange cloud)
   later needs cert-manager moved to a DNS-01 solver or a Cloudflare Origin CA
   cert — HTTP-01 breaks behind the proxy.

## Day-to-day

Validate everything without touching a cluster — this is the fast loop:

```bash
act workflow_dispatch -W .github/workflows/deploy-platform.yml   # preflight only
```

Preflight lints and renders the charts we own, asserts the Dagster authz
allowlist is still default-deny, and parses every upstream values file. No
kubeconfig required.

The deploy job runs `helm upgrade` for each chart in dependency order — there is
no per-component switch; deploy one thing by running its `make` target (or
`helm upgrade`) directly against the cluster.

List what act sees:

```bash
act -l
```

## act quirks worth knowing

- **`type: choice` inputs.** act does honour defaults, but pass `--input k=v`
  explicitly when you want a non-default.
- **`secrets.GITHUB_TOKEN` is not injected.** Real GitHub provides it; act does
  not. Put a PAT with `write:packages` in `.env` as `GITHUB_TOKEN`, or run
  `build-images` with `--input push=false`.
- **GHA build cache does not exist locally.** `cache-from/to: type=gha` in
  `build-images` degrades to a warning under act. Harmless.
- **Docker-in-Docker.** `build-images` needs the daemon socket. act mounts it by
  default; if buildx misbehaves, build directly instead:
  ```bash
  docker build -f data/Dockerfile -t ohdp-pipeline .
  docker build -f apps/api/Dockerfile -t ohdp-hub-api .
  ```
- **Apple Silicon.** `.actrc` forces `linux/amd64` because most actions publish
  amd64 only. Remove that line for native speed if nothing breaks.
- **`--secret-file .env` also loads as act's default `--env-file`,** so these
  values appear as plain environment variables in the job too. Fine locally;
  don't write a workflow that depends on it, or it will break on real GitHub.

## Safety rails

- `deploy-infra` defaults to `plan`. `apply` and `destroy` require
  `workflow_dispatch`, and `destroy` also requires typing the project name.
- `deploy-platform` runs preflight only unless `deploy` is ticked.
- Both use `concurrency` groups with `cancel-in-progress: false`, so two applies
  cannot race on Terraform state or the same Helm release.
- `deploy-platform` deploys Dagster and hub-api at `sha-<commit>` by default;
  the hub-api chart fails to render on `latest`, so the running image is always
  a specific build.
- After deploying the proxy, the workflow asserts a mutation is rejected with
  403. A Dagster upgrade that breaks the allowlist fails the deploy rather than
  silently opening the UI up.

## What is still missing

- **Superset** has no chart values yet (M1) though Postgres provisions its database.
- **Cube** and **hub-web** are not deployed — M2.
- **No rollback step.** `helm rollback <release>` by hand; snapshot rollback is a
  pointer change, see the [runbook](runbook.md).
