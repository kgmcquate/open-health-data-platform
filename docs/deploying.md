# Deploying

Three workflows, all runnable locally with [`act`](https://github.com/nektos/act).

| Workflow | Does | Needs a cluster? |
|---|---|---|
| [`build-images.yml`](../.github/workflows/build-images.yml) | Rebuilds whichever of `ohdp-hub-api`, `ohdp-pipeline`, `ohdp-cube` changed since the last successful run (unchanged ones are just re-tagged `sha-<12>`), pushes to GHCR, then (on a real merge to main) auto-deploys only the chart(s) whose image or chart/values changed — hub-api + mcp-cube, dagster, cube | on push to main, to auto-deploy |
| [`deploy-infra.yml`](../.github/workflows/deploy-infra.yml) | Terraform: DigitalOcean Kubernetes cluster, Spaces buckets, Cloudflare DNS records, and the Iceberg lakehouse — Snowflake as the catalog, S3 as its external volume | no |
| [`deploy-platform.yml`](../.github/workflows/deploy-platform.yml) | `helm upgrade` for each chart: platform-base, Traefik, cert-manager, external secrets, OpenSearch, OpenMetadata, authz proxy, dagster-monitoring, oauth2-proxy, Dagster, Cube, hub-api, mcp-cube | when `deploy` ticked |

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

   This also creates the Iceberg lakehouse (ADR-0019). Snowflake is the
   catalog, so most of it is Snowflake resources; the AWS half is just the
   external volume's bucket, and needs AWS admin credentials in `.env` as
   `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` — which the workflow passes as
   `TF_VAR_aws_*`, *not* under those names, since the Spaces state backend
   already claims them.

   Two follow-ups the apply prints and does not do for you:

   - publish the pipeline's credentials into `SNOWFLAKE_PIPELINE_PAT`,
     `AWS_PIPELINE_ACCESS_KEY_ID` and `AWS_PIPELINE_SECRET_ACCESS_KEY` (repo
     secrets, read by `deploy-platform.yml`);
   - finish the external volume's AWS trust policy — a **second apply** with
     two values only Snowflake can generate. See
     [`platform/terraform/README.md`](../platform/terraform/README.md),
     "The second apply". Until it is done Snowflake cannot reach the bucket, so
     nothing can write a table.

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
   `catalog`, `cube`).

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

- **hub-web** is not deployed and not scaffolded. hub-api serves the chat page
  itself at `/` on `app.open-health-data-platform.org` (`hub_api/main.py`) — a
  deliberate shortcut while the UI is one static page, to be replaced by
  `apps/web` when the hub grows a landing page, billing, and dashboards.
- **The chat agent needs one thing set by hand** before it works, which a deploy
  cannot do for you:
  1. An `OPENROUTER_API_KEY` repo secret. Without it the app deploys fine and
     `/api/chat` answers 503 because no model backend is configured.
  2. `https://app.open-health-data-platform.org/oauth2/callback` added to the
     authorized redirect URIs of the Google OAuth client that
     `DAGSTER_OIDC_CLIENT_ID` names — the hub app's wall reuses it. Without it,
     login fails with `redirect_uri_mismatch`.
- **No real OIDC provider.** `app.open-health-data-platform.org` sits behind an
  `oauth2-proxy` Google wall restricted to `kgmcquate@gmail.com`, the same
  posture as Dagster, and every user past it is tier `free`.
  ARCHITECTURE.md §5's hosted IdP with a `tier` claim is what billing needs and
  is still unbuilt.
- **No rollback step.** `helm rollback <release>` by hand; snapshot rollback is a
  pointer change, see the [runbook](runbook.md).
