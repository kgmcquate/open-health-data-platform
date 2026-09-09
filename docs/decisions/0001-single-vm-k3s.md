# 0001 — Single VM with k3s, not managed Kubernetes

**Status:** Accepted

## Context

Infrastructure budget is ~$35/month total (ARCHITECTURE.md §1.3). Managed
Kubernetes control planes (EKS, GKE Autopilot, AKS uptime SLA) cost $70+/month
before any worker nodes. The workload is a batch pipeline plus a handful of
always-on read-serving pods; peak memory is ~15 GB.

## Decision

Run everything on one Hetzner CX53 (16 vCPU, 32 GB, 320 GB NVMe) with k3s,
in Germany or Finland. Namespaces (`app`, `data`, `bi`, `meta`, `infra`) provide
logical separation. Cloudflare fronts it for DNS, TLS, WAF, and rate limiting.

## Consequences

- No HA. Single replica; brief downtime during rollouts and upgrades is accepted
  (§1.4). Do not add multi-replica or autoscaling.
- Every pod gets a memory **limit** — an unbounded DuckDB query would otherwise
  take down the node (§4).
- Backups are `pg_dump` to R2 only; the warehouse rebuilds from public sources
  (§11).
- Hosting region is still an open decision pending a latency/price check (§10.1).
