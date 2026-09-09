# Deploying

Three workflows, all runnable locally with [`act`](https://github.com/nektos/act).

| Workflow | Does | Needs a cluster? |
|---|---|---|
| [`build-images.yml`](../.github/workflows/build-images.yml) | Builds `ohdp-hub-api` and `ohdp-pipeline`, pushes to GHCR | no |
| [`deploy-infra.yml`](../.github/workflows/deploy-infra.yml) | Terraform: Hetzner VM, firewall, Cloudflare DNS + R2 | no |
| [`deploy-platform.yml`](../.github/workflows/deploy-platform.yml) | cert-manager, Postgres, OpenSearch, OpenMetadata, Dagster, authz proxy, hub-api | `deploy` mode only |

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

1. **Create the Terraform state bucket by hand.** An R2 bucket named
   `ohdp-tfstate`. Terraform does not manage it — a tool storing state in a
   bucket it also creates cannot destroy that bucket. Create an R2 API token
   (Object Read & Write) and put the key pair in `.env`.

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
   act workflow_dispatch -W .github/workflows/deploy-platform.yml \
     --input mode=deploy --input image_tag=sha-abc123def456
   ```

## Day-to-day

Validate everything without touching a cluster — this is the fast loop:

```bash
act workflow_dispatch -W .github/workflows/deploy-platform.yml   # mode=preflight
```

Preflight lints and renders both charts, asserts the Dagster authz allowlist is
still default-deny, parses every values file and static manifest, and reports
which secrets are missing. No kubeconfig required.

Deploy one component:

```bash
act workflow_dispatch -W .github/workflows/deploy-platform.yml \
  --input mode=deploy --input component=dagster --input image_tag=sha-abc123def456
```

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
- `deploy-platform` defaults to `preflight`, not `deploy`.
- Both use `concurrency` groups with `cancel-in-progress: false`, so two applies
  cannot race on Terraform state or the same Helm release.
- `deploy-platform` refuses to deploy Dagster or hub-api without an explicit
  `image_tag`; the hub-api chart additionally fails to render on `latest`.
- After deploying the proxy or Dagster, the workflow asserts a mutation is
  rejected with 403. A Dagster upgrade that breaks the allowlist fails the deploy
  rather than silently opening the UI up.

## What is still missing

- **Superset** has no chart values yet (M1) though Postgres provisions its database.
- **Cube** and **hub-web** are not deployed — M2.
- **No rollback step.** `helm rollback <release>` by hand; snapshot rollback is a
  pointer change, see the [runbook](runbook.md).
