# 0007 — Move compute from Hetzner to Kamatera

**Status:** Accepted
**Amends:** [ADR-0005](0005-hetzner-k3s-over-managed-kubernetes.md) — the host, not
the "keep k3s on one VM" decision, which stands.

## Context

ADR-0005 chose Hetzner because the budget "rules out expensive compute, not
managed control planes," and closed with: *if a provider ever offers both a free
control plane and Hetzner-class compute pricing, revisit this.*

That is not quite what happened. We now have **free compute on Kamatera**. Kamatera
has no managed Kubernetes — its "Kubernetes service" is a one-click install onto a
VM you still manage — so this is not a move to a managed control plane. It is the
same single-VM k3s architecture on a different, and for us free, host.

With compute free, the entire cost table in ADR-0005 (Hetzner ~$35 vs Vultr ~$161
for 32 GB) stops being the deciding factor.

## Decision

Provision the node on **Kamatera** (`Kamatera/kamatera` Terraform provider),
datacenter **US / New York**, CPU type B, 8 vCPU / 32 GB / 300 GB — the
ARCHITECTURE.md §4 memory budget, unchanged. Everything above
`platform/terraform/` is untouched: k3s, the Helm charts, the values, the
publish-and-replicate loop.

## Consequences

- **No cloud firewall.** Hetzner's `hcloud_firewall` is replaced by nftables on
  the host, rendered into the server startup script from `admin_ip_ranges` and
  the Cloudflare IP ranges. A `systemd` timer refreshes the Cloudflare set daily;
  changing `admin_ip_ranges` needs a rebuild. The security property is the same —
  80/443 reachable only from Cloudflare, 22/6443 only from admin networks.
- **No reserved IP.** Hetzner's `hcloud_primary_ip` (with `prevent_destroy`) has
  no Kamatera equivalent. The public IP is assigned at create time and only
  changes on a server rebuild; the Cloudflare records are Terraform-managed and
  repoint on the next apply, so a rebuild costs a DNS propagation, not manual work.
- **The API server needs a hostname.** k3s can't be reached at a Cloudflare-proxied
  name on 6443, and the IP isn't known before creation, so an unproxied
  `k3s.<domain>` record fronts the API and goes into the k3s serving cert.
- **US East now, deliberately.** ADR-0001 §10.1 left region open; New York is the
  choice. Latency to a US health-data audience over the previous EU placement.
- **Provider maturity.** The `hcloud` provider is first-party; `Kamatera/kamatera`
  is vendor-published but smaller (resources: `kamatera_server`, `kamatera_network`
  only). If it stops being maintained, the fallback is the Kamatera API directly
  from a `null_resource`, or another Hetzner-class host — the Helm layer is
  portable either way, exactly as ADR-0005 notes.
- ADR-0005's "why Kubernetes / why k3s" reasoning is unaffected and still governs.
