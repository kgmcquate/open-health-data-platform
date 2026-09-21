# 0027 — Chat agent writes OpenMetadata memories unreviewed

**Status:** Accepted
**Amends:** [ADR-0016](0016-chat-agent-tool-surface.md)'s consequence "any write-back to the
catalog by the agent must stay human-reviewed" and [docs/chatbot.md](../chatbot.md) §3.4/§6.

## Context

`create_context_memory` is OpenMetadata's write-back tool for §3.4's loop: an answer that
required a non-obvious join or carried a real caveat gets saved as a memory, so the next
person asking a similar question benefits. `ohdp_agent/catalog.py` excluded it from the
agent's allowlist from the start, alongside the other ten write tools OM advertises
(`patch_entity`, every `create_*`). The stated reason, repeated in ADR-0016 and chatbot.md §6:
catalog content — descriptions, glossary terms, Context Center articles, and now memories —
lands back in the system prompt as trusted context (§3.2). A model that can write a memory
unreviewed can feed itself a bad memory and then trust it, or have one planted via a
prompt-injected answer that later reads back as curated fact. M4 scoped a human-review queue
in front of the write to close that loop; it was never built.

The product decision here is to turn write-back on now, without that queue, because the
value of an accumulating institutional memory outweighs the risk at this stage — accepted
explicitly, not by default.

**A related finding, left out of scope:** the deployed model (`z-ai/glm-5.3-flash` in
`apps/api/config/models.yaml`) reaches OpenMetadata through the `mcp-openmetadata` connection
in `apps/api/config/tools.yaml`, which `hub_api/tool_connections.py` wires as a raw, unfiltered
`pydantic_ai.mcp.MCPToolset` against OM's MCP endpoint. `catalog.py`'s allowlist is never
consulted on that path — it only applies to the in-process `"catalog"` tool source, which no
configured model currently uses. In practice, every write tool OM advertises, not only
`create_context_memory`, has been reachable at the code level for as long as `mcp-openmetadata`
has been the deployed connection; the system prompt's "those calls will be rejected" line has
never been true. This was raised during the memory work and knowingly left unfixed — the
allowlist still gets extended below for the in-process path and to keep `catalog.py` an
accurate statement of intent, but the code-level gap on `mcp-openmetadata` remains. Revisit if
the risk calculus here changes.

## Decision

`create_context_memory` is added to `ohdp_agent/catalog.py`'s allowlist (a new
`WRITABLE_TOOLS` set, unioned into `ALLOWED_TOOLS`), and `models.yaml`'s system prompt now
instructs the deployed model to call it after answering a question that needed a non-obvious
join or carried a real caveat, with no review step before the write lands in OM.

Every other write tool stays refused in the allowlist and discouraged in the prompt. The
`mcp-openmetadata` gap described above is not closed by this ADR.

## Consequences

- A bad or injected answer can now persist as a memory and later feed back into the prompt as
  if it were curated truth — the exact cycle ADR-0016 and chatbot.md §6 warned against. No
  code-level control mitigates it; the only mitigation is the prompt instruction to write
  memories conservatively (verified facts only, not inference).
- Memory write-back and read-back are currently decoupled: `find_context`/`semantic_search`,
  the tools that would surface a written memory to a later turn, are inert on this deployment
  (no vector embeddings configured, chatbot.md §2.1). Memories accumulate in OM now but are not
  reliably discoverable by the agent until that is configured.
- The `mcp-openmetadata` filtering gap is a standing, documented risk independent of this
  decision — a future change to any configured model's `tools:` list inherits it silently.
  Closing it means adding tool filtering to `_build_mcp_toolset`
  (`hub_api/tool_connections.py`) or moving the deployed model onto the in-process `"catalog"`
  tool source instead of `mcp-openmetadata`.
- If the risk proves out (bad memories observed, or an injection actually exploits the cycle),
  the fix is the human-review queue M4 originally scoped: a proposal lands somewhere reviewable
  (e.g. a Postgres table alongside `chat_turns`) and only an approved proposal calls
  `create_context_memory`.

See [docs/chatbot.md](../chatbot.md) §3.4 and §6 for the design this decision sits inside.
