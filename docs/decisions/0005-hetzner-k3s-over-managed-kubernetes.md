# 0005 — Hetzner + k3s over managed Kubernetes

**Status:** Accepted
**Supersedes the reasoning in:** [0001](0001-single-vm-k3s.md), ARCHITECTURE.md §9

## Context

ARCHITECTURE.md §9 says "do not add managed Kubernetes — the control plane cost
exceeds the total budget," and ADR-0001 repeats it. That reasoning was drawn from
EKS/GKE/AKS pricing (~$70/month for the control plane alone).

**It does not generalise.** Vultr Kubernetes Engine offers a free control plane;
you pay only for worker nodes. Several providers now do. The stated objection to
managed Kubernetes is therefore wrong as written, and re-deriving it from the
same premise would keep producing the wrong answer.

We also asked whether Kubernetes is needed at all, given a single node.

## Decision

**Keep Kubernetes, keep k3s, stay on Hetzner.** Provision with Terraform, deploy
with Helm.

### Why not Vultr VKE, despite the free control plane

The control plane was never the cost driver — compute is. For 32 GB of RAM:

| | Spec | Monthly |
|---|---|---|
| Hetzner `cx52` | 16 vCPU / 32 GB / 320 GB | ~$35 |
| Vultr Regular | 8 vCPU / 32 GB | ~$161 |
| Vultr Regular | 4 vCPU / 16 GB | ~$80 |

VKE at 32 GB is ~5x the ceiling in §1.3. The 16 GB tier fits the budget better
but only by dropping OpenSearch (3 GB) and OpenMetadata (2 GB), which removes the
catalog from M2 and the discovery MCP from M3 — a product cut made for
infrastructure reasons. At $5/mo per user, $35 of infrastructure breaks even at
7 paying users; $161 needs 32.

The correct statement of the constraint is therefore: **the budget rules out
expensive compute, not managed control planes.** If a provider ever offers both a
free control plane and Hetzner-class compute pricing, revisit this.

### Why Kubernetes at all on one node

- Dagster, OpenMetadata, Superset and OpenSearch all ship maintained **Helm
  charts** as their supported deployment path. Hand-translating those to Compose
  is real work, repeated on every upgrade, off the supported path.
- Dagster's `K8sRunLauncher` runs each pipeline run as an ephemeral Job. That is
  exactly the memory-capped, concurrency-1 dbt build pod §3 and §4 describe —
  for free, rather than as something we build.
- The publish-and-replicate loop (§3) is init container + Deployment annotation
  patch + rolling restart. All native.
- Per-pod memory limits, which §4 calls mandatory, are a first-class primitive.

### Why k3s specifically

Single binary, ~1 GB, CNCF-conformant so upstream charts work unchanged, and it
bundles Traefik (the §4 ingress), local-path storage (PVCs for Postgres and
OpenSearch) and ServiceLB. kubeadm costs more RAM for nothing here; Talos is a
better long-term answer but a harder one on Hetzner cloud images.

## Consequences

- Cluster upgrades are ours to run. On one node this is one command, and §1.4
  already accepts downtime during them.
- `platform/terraform/` is the only provider-specific code. The Helm charts and
  values are portable to any conformant cluster, so a future provider move is a
  contained change — this decision is reversible.
- ARCHITECTURE.md §9's phrasing is corrected in place to say *compute cost*
  rather than *control plane cost*.
- Server type in §4 corrected: **`cx52`**, not "CX53" — no such type exists.
  Specs and price quoted in the doc were right; the name was not.
