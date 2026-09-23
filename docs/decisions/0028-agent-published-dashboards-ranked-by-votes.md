# 0028 — The chat agent publishes dashboards; readers vote them down

**Status:** Accepted
**Amends:** [ADR-0025](0025-dashboards-as-code-in-chat.md) (a saved dashboard is a database
row, published on the public page, not a YAML file in a PR) and
[docs/chatbot.md](../chatbot.md) §5/§6's "curated content is written by us, never by a user
and never by the chat agent".

## Context

ADR-0025 made a dashboard a reviewable spec and drew it into the chat turn. Keeping one was
supposed to be a copy-and-PR: the rendered card carries its own YAML, someone commits it.
Nobody ever did — the `ohdp_agent/dashboards/` package and its two "saved dashboard" tools
were removed unused, and `curated_dashboards` (the table behind the public Dashboards page)
has had an insert path written for it exactly never. So the platform had two dashboard
surfaces, one of which was empty and one of which forgot everything at the end of the turn.

`save_dashboard`/`get_dashboard` gave the agent a library again, as a table. The open
question was what that library *is*: a private scratchpad the agent reads back, or the
Dashboards page itself. Keeping them separate means someone has to promote rows by hand,
which is the step that did not happen last time.

The reason they were separate is real. `hub_api.content`'s docstring stated it plainly:
curated rows are written by us, so what a public page shows cannot be steered by a
prompt-injected chat turn. Merging the two hands a model a publish button.

## Decision

One table, `dashboards` (`hub_api.library`, replacing the orphaned `curated_dashboards`),
written by the agent's `save_dashboard` and read by the public `/api/dashboards` routes. Saving *is*
publishing. Quality is handled after the fact rather than before it:

- **Votes.** One row per signed-in voter per dashboard (`dashboard_votes`), changeable and
  withdrawable. `HIDE_AT_SCORE` (-2) drops a dashboard off the public page; `featured` still
  sorts ours to the top.
- **Topics from the catalog, not from the model.** A save resolves the query's cubes to
  their curated tables and asks OpenMetadata which Consumer-aligned domains own them
  (`DomainsClient.topics_for_tables`). A dashboard lands on a topic page because of what it
  queries. An unmatched cube gets no topic rather than a guessed one.
- **The row is a spec, plus a picture rendered from it.** A save stores the `DashboardSpec`
  *and* the HTML page `render_html` produced from it while validating, and the public route
  serves that page rather than re-querying per view. The spec stays authoritative — the page
  is rebuilt from it, never edited — and `last_rendered` is published so a reader knows how
  old the numbers are; a stale view schedules a fresh render behind the response.

What makes this tolerable rather than reckless is that a dashboard has nowhere to put a lie.
A `DashboardSpec` forbids literal `data` at any depth, `save_dashboard` runs the query and
renders the page itself before writing, and every number on that page came out of Cube. The
worst a prompt-injected save produces is a real query, drawn badly, with a misleading title
— which is what votes are for.

## Consequences

- **A bad dashboard is public before anyone reviews it.** Votes are a quality signal, not a
  security control: the first person to see a bad chart is a visitor, not a reviewer. The
  ceiling on the damage is `MAX_DASHBOARDS` and the fact that the numbers themselves are
  Cube's. If this turns out to bite, the fix is a `published` flag defaulting to false, not
  a return to hand-committed YAML.
- **Titles and captions are model-written text on a public page.** They are the one part of
  a dashboard that is not validated by anything. A misleading title over an honest chart is
  the realistic failure mode here, and it is the one votes are slowest to catch.
- **An overwrite keeps the old row's votes.** Re-saving a name is meant to mean "this same
  chart, corrected"; a rewrite into a different chart inherits its predecessor's standing.
  Resetting votes on every save was the alternative and it punishes typo fixes.
- **A published dashboard shows numbers as old as its last render.** That is the price of
  serving stored HTML instead of querying per view, and it is the one thing this design must
  not hide: the age is on every listing, and viewing an hour-old page refreshes it for the
  next visitor. A dashboard nobody opens stops being refreshed, which is the correct
  priority.
- **A row is now hundreds of kilobytes.** The rendered page carries the chart's own data
  table, so `MAX_DASHBOARDS` bounds a storage cost as well as a spam one.
- **Nothing promotes anything.** There is no review queue, no draft state, and no admin
  surface — deliberately, since the last design's manual step is why the page stayed empty.
- **`curated_dashboards` is orphaned, not migrated.** Nothing ever wrote to it, so there is
  no data to carry over and no migration code to maintain; `dashboards` is created fresh,
  which is what lets every column that matters be NOT NULL. The old table stays in the
  database until someone drops it by hand.
