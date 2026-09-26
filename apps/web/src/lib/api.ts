/** Thin fetch wrappers for hub-api. All same-origin; the session cookie does
 * the talking (no tokens in JS). */

export interface User {
  email: string;
  name: string;
  tier: string;
  /** Whether this user holds the admin role. Derived server-side from the
   * deployment's configured admin list on every request (`hub_api.auth`), so
   * it is a fresh answer rather than something baked into a session — but it
   * is still only a *display* signal here. Hiding a button is not access
   * control; every admin route checks the role itself. */
  is_admin: boolean;
}

/** A Consumer-aligned OpenMetadata domain — the subject a Topics page groups
 * everything else (metrics, data assets, sources, dashboards, literature) by. */
export interface Topic {
  id: string;
  name: string;
  description: string;
  catalog_url: string;
}

/** One table or metric attached to a topic, or one Source-aligned domain a
 * topic's tables trace back to — same shape either way, see `hub_api.content`. */
export interface CatalogAsset {
  id: string;
  name: string;
  description: string;
  entity_type: string;
  catalog_url: string;
}

/** The full bundle `GET /api/topics/{name}` returns. */
export interface TopicDetail {
  topic: Topic;
  metrics: CatalogAsset[];
  assets: CatalogAsset[];
  sources: Topic[];
}

/** One published dashboard, as `/api/dashboards` lists it.
 *
 * The chart is NOT here: it is a whole HTML page, rendered server-side from
 * the dashboard's spec and served by `dashboardHtmlUrl` one at a time.
 * `last_rendered`/`stale` say how old the numbers in that page are — a spec
 * cannot go stale, a rendered picture of it can. `query` is here because the
 * card shows it; it is what the numbers are traceable to.
 *
 * `source` says who published it — see the field's own comment below. */
export interface Dashboard {
  id: number;
  /** The kebab-case key it was saved under — also its URL. */
  name: string;
  title: string;
  description: string;
  caption: string | null;
  topics: string[];
  featured: boolean;
  /** "chat" (the agent saved it), "user" (someone published it from the
   * builder) or "curated" (ours). None is trusted more than another — all
   * three are the same validated spec — it is there so a reader knows where a
   * chart came from. */
  source: "chat" | "curated" | "user";
  query: Record<string, unknown>;
  upvotes: number;
  downvotes: number;
  score: number;
  hidden: boolean;
  /** When the stored page was last rendered, and whether that is old enough
   * that viewing it schedules a fresh render server-side. */
  last_rendered: string | null;
  stale: boolean;
  /** This viewer's own vote: 1, -1, or 0 when they have not voted (or are
   * signed out, who cannot vote at all). */
  my_vote: number;
  created_at: string | null;
  updated_at: string | null;
}

/** Where a dashboard's rendered page lives. Loaded into a sandboxed iframe by
 * `src`, never fetched into this app: it is a full HTML document served under
 * a CSP `sandbox` header, and it has no business running on the hub's own
 * origin (`hub_api.content`'s `dashboard_html`). */
export const dashboardHtmlUrl = (name: string) =>
  `/api/dashboards/${encodeURIComponent(name)}/html`;

/** --- the dashboard builder (`hub_api.builder`) ---------------------------
 *
 * Everything below needs a signed-in session: the preview runs a Cube query the
 * caller composed, and drafts and published lists are scoped server-side to the
 * session's own email — this app never sends an author identity.
 *
 * The unit is YAML text, not a parsed object. A `DashboardSpec` is written and
 * committed as YAML (`ohdp_agent.dashboard`), every rendered card shows its own
 * YAML source, and a draft is stored as typed — so the editor can round-trip a
 * published chart's source without this app needing a YAML parser at all. The
 * server parses, validates, runs and renders; what comes back is a page.
 */

/** A draft: private, this author's, and not necessarily valid. `name`/`title`
 * are read out of the YAML by the server when it parses, and empty when it does
 * not. */
export interface DashboardDraft {
  id: number;
  name: string;
  title: string;
  spec_yaml: string;
  created_at: string | null;
  updated_at: string | null;
}

/** A drawn-but-unsaved dashboard. `html` is a whole document for a sandboxed
 * iframe's `srcDoc` — never inserted into this app's own DOM. The row counts
 * are here because the picture cannot show that it was truncated. */
export interface DashboardPreview {
  name: string;
  title: string;
  html: string;
  row_count: number;
  truncated: boolean;
}

export interface PublishResult {
  name: string;
  title: string;
  /** False when this replaced the author's own dashboard of the same name. */
  created: boolean;
  topics: string[];
  url: string;
}

/** One cube's members, as the field-reference panel lists them — the same
 * `/meta` the chat agent's `list_metrics` reads, so what you can write in a
 * spec is exactly what the agent can ask for. */
export interface CubeRef {
  name: string;
  title: string;
  description: string;
  measures: { name: string; title: string; description: string; agg_type: string }[];
  dimensions: { name: string; title: string; description: string; type: string }[];
}

/** Draw a spec without saving it. Rejections are 4xx with a readable `detail`
 * — a YAML syntax error, a field path pydantic rejected, or the semantic
 * layer's own message about a member that does not exist — so the editor shows
 * the server's reason rather than inventing one. */
export const previewDashboard = (specYaml: string) =>
  sendJson<DashboardPreview>("/api/builder/preview", "POST", { spec_yaml: specYaml });

export const fetchDrafts = () => getJson<DashboardDraft[]>("/api/builder/drafts");
export const createDraft = (specYaml: string) =>
  sendJson<DashboardDraft>("/api/builder/drafts", "POST", { spec_yaml: specYaml });
export const updateDraft = (id: number, specYaml: string) =>
  sendJson<DashboardDraft>(`/api/builder/drafts/${id}`, "PUT", { spec_yaml: specYaml });
export const deleteDraft = (id: number) =>
  sendJson<{ ok: true }>(`/api/builder/drafts/${id}`, "DELETE");

/** Publish to the **public** library, under the spec's own `name`. Overwrites
 * your own dashboard of that name (keeping its votes); 409 for a name someone
 * else — or the chat agent — published. */
export const publishDashboard = (specYaml: string) =>
  sendJson<PublishResult>("/api/builder/publish", "POST", { spec_yaml: specYaml });

/** An author's own published dashboard: a public entry plus the source of it.
 *
 * `spec_yaml` is what makes correcting one possible — the spec goes back into
 * the editor and is published again under the same name, replacing the row and
 * keeping its votes. It is `null` only for a stored spec this deployment can no
 * longer revalidate, and absent entirely from the public listing. */
export interface MyDashboard extends Dashboard {
  spec_yaml: string | null;
}

/** The dashboards this session's user published, including any voted below the
 * hide threshold: they have dropped off the public page, and their author is
 * who can republish them fixed. */
export const fetchMyDashboards = () => getJson<MyDashboard[]>("/api/builder/published");

/** Delete one of this session's user's own published dashboards — its stored
 * render and votes go with it, for good. 404 for any name that is not theirs;
 * the admin-only `deleteDashboard` is the one that reaches anybody's. */
export const deleteMyDashboard = (name: string) =>
  sendJson<{ ok: true }>(`/api/builder/published/${encodeURIComponent(name)}`, "DELETE");

export const fetchCubes = () => getJson<CubeRef[]>("/api/semantic/cubes");

export interface LiteratureItem {
  id: number;
  title: string;
  authors: string;
  venue: string;
  year: number | null;
  url: string;
  doi: string;
  summary: string;
  tags: string[];
  trending: boolean;
}

/** The error a failed request should throw.
 *
 * FastAPI puts the reason in `detail`, and for some routes that reason is the
 * whole point of the response: the builder's 422 names the YAML line or the
 * spec field to fix, and its 400 carries the semantic layer's own message about
 * a member that does not exist. Showing "failed: 422" instead would mean the
 * page knows why and refuses to say. Falls back to the status for a response
 * with no JSON body (a proxy error page, a 502 from the ingress). */
async function failure(path: string, response: Response): Promise<Error> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string" && body.detail) return new Error(body.detail);
  } catch {
    // Not JSON. The status line is all there is.
  }
  return new Error(`${path} failed: ${response.status}`);
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path, { credentials: "same-origin" });
  if (!response.ok) throw await failure(path, response);
  return (await response.json()) as T;
}

/** Current user, or null when signed out. 401 is a state, not an error. */
export async function fetchMe(): Promise<User | null> {
  const response = await fetch("/auth/me", { credentials: "same-origin" });
  if (response.status === 401) return null;
  if (!response.ok) throw new Error(`/auth/me failed: ${response.status}`);
  return (await response.json()) as User;
}

export const fetchTopics = () => getJson<Topic[]>("/api/topics");
export const fetchTopic = (name: string) =>
  getJson<TopicDetail>(`/api/topics/${encodeURIComponent(name)}`);

export const fetchDashboards = (topic?: string) =>
  getJson<Dashboard[]>(topic ? `/api/dashboards?topic=${encodeURIComponent(topic)}` : "/api/dashboards");
export const fetchDashboard = (name: string) =>
  getJson<Dashboard>(`/api/dashboards/${encodeURIComponent(name)}`);
export const fetchLiterature = (topic?: string) =>
  getJson<LiteratureItem[]>(topic ? `/api/literature?topic=${encodeURIComponent(topic)}` : "/api/literature");

/** What `GET /api/search` returns: one query, four kinds of hit.
 *
 * The four sections come from two different backends — topics and assets from
 * OpenMetadata's search index, dashboards and literature from hub-api's own
 * tables (see `hub_api.content.search`) — and each can be empty on its own
 * because that backend was unreachable rather than because nothing matched.
 * That is why the UI never says "no results" per section, only overall. */
export interface SearchResults {
  query: string;
  topics: Topic[];
  assets: CatalogAsset[];
  dashboards: Dashboard[];
  literature: LiteratureItem[];
}

/** `limit` is per section, and the server caps it (`hub_api.content`'s
 * `SEARCH_LIMIT_MAX`). Omitted for the header dropdown, which wants the small
 * default; the `/search` page asks for more. */
export const fetchSearch = (q: string, limit?: number) =>
  getJson<SearchResults>(
    `/api/search?q=${encodeURIComponent(q)}${limit ? `&limit=${limit}` : ""}`,
  );

export interface ChatModel {
  id: string;
  label: string;
  /** Who serves it, e.g. "Z.ai via OpenRouter" (`hub_api.models.default_provider`). */
  provider: string;
  default: boolean;
}

/** The models explicitly configured in config/models.yaml, for the picker.
 * Auto-discovered models are usable by id but omitted from the picker. */
export const fetchModels = () => getJson<ChatModel[]>("/api/models");

/** `GET /api/chat`'s sibling quota readout: what this signed-in user has used
 * today against the daily question, token, and `render_dashboard`/
 * `save_dashboard` caps (`hub_api.chat.me`). Every cap is enforced
 * server-side regardless of whether this is ever fetched — this is display
 * only. Shown in the Settings dropdown (`Navbar.tsx`), next to the signed-in
 * identity it already shows there. */
export interface ChatAllowance {
  email: string;
  tier: string;
  questions_used_today: number;
  questions_allowed_per_day: number;
  tokens_used_today: number;
  tokens_allowed_per_day: number;
  renders_used_today: number;
  renders_allowed_per_day: number;
  saves_used_today: number;
  saves_allowed_per_day: number;
  issues_used_today: number;
  issues_allowed_per_day: number;
}

export const fetchChatAllowance = () => getJson<ChatAllowance>("/api/me");

/** Start a Stripe Plus subscription Checkout session (`hub_api.billing`). Returns
 * the embedded Checkout Session's `client_secret`, which the client hands to
 * Stripe.js' embedded Checkout page (`stripe.initEmbeddedCheckout`) to render the
 * Stripe-hosted page in-page. 401 when signed out, 503 when billing is not
 * configured. */
export const createCheckoutSession = () =>
  sendJson<{ client_secret: string }>("/api/billing/checkout", "POST");

/** Open the Stripe Billing Portal for the signed-in Plus subscriber
 * (`hub_api.billing`). Returns a Stripe-hosted URL — unlike Checkout, this is
 * not embedded, so the caller navigates the browser there directly (Stripe
 * sends them back to /billing when they're done). 401 when signed out, 404
 * if this account has never checked out, 503 when billing is not
 * configured. */
export const createBillingPortalSession = () =>
  sendJson<{ url: string }>("/api/billing/portal", "POST");

export async function logout(): Promise<void> {
  await fetch("/auth/logout", { method: "POST", credentials: "same-origin" });
}

export interface ThreadSummary {
  id: number;
  title: string;
  status: "regular" | "archived";
  created_at: string;
  updated_at: string;
}

/** One call+result pair from `ohdp_agent.loop`'s `turn.tool_calls` — `result`/
 * `is_error`/`format` are absent for a call whose result never arrived (the
 * turn was cancelled or errored mid-call). */
export interface ThreadTurnToolCall {
  tool_call_id: string;
  name: string;
  input: Record<string, unknown>;
  result?: unknown;
  is_error?: boolean;
  format?: "html";
}

/** One entry in a turn's chronological order — a text segment or a pointer
 * (by `tool_call_id`) into that turn's own `tool_calls` — recorded live by
 * `ohdp_agent.loop.Turn.timeline`. `null`/absent on a turn logged before this
 * column existed; `runtime.ts`'s `turnToMessages` falls back to grouping
 * tool calls before the answer text for those. */
export type ThreadTurnTimelineEntry =
  | { type: "text"; text: string }
  | { type: "tool_call"; tool_call_id: string };

export interface ThreadTurn {
  id: number;
  question: string;
  answer: string;
  plan: string;
  error: string | null;
  feedback: "positive" | "negative" | null;
  tool_calls: ThreadTurnToolCall[];
  timeline: ThreadTurnTimelineEntry[] | null;
}

export interface ThreadDetail extends ThreadSummary {
  turns: ThreadTurn[];
}

async function sendJson<T>(
  path: string,
  method: "POST" | "PUT" | "PATCH" | "DELETE",
  body?: unknown,
): Promise<T> {
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw await failure(`${method} ${path}`, response);
  return (await response.json()) as T;
}

export const fetchThreads = () => getJson<ThreadSummary[]>("/api/threads");
export const fetchThread = (id: number) => getJson<ThreadDetail>(`/api/threads/${id}`);
export const createThread = (title = "") =>
  sendJson<ThreadDetail>("/api/threads", "POST", { title });
export const renameThread = (id: number, title: string) =>
  sendJson<ThreadDetail>(`/api/threads/${id}`, "PATCH", { title });
export const archiveThread = (id: number) =>
  sendJson<ThreadDetail>(`/api/threads/${id}/archive`, "POST");
export const unarchiveThread = (id: number) =>
  sendJson<ThreadDetail>(`/api/threads/${id}/unarchive`, "POST");
export const deleteThread = (id: number) => sendJson<{ ok: true }>(`/api/threads/${id}`, "DELETE");

export const submitFeedback = (turnId: number, rating: "positive" | "negative") =>
  sendJson<{ ok: true }>("/api/feedback", "POST", { turn_id: turnId, rating });

/** A question the agent is blocked on mid-answer — the `ask_user` tool
 * (ohdp_agent.loop). Lives only for as long as that run holds its SSE stream
 * open, so it is never part of a loaded thread's history. */
export interface PendingAsk {
  ask_id: string;
  question: string;
  options: string[];
  allow_other: boolean;
}

/** Unblock the `/api/chat` stream waiting on `askId`. A second request rather
 * than a reply on the stream, because SSE only goes one way. 404 means the
 * question stopped waiting (answered elsewhere, timed out, run cancelled). */
export const answerAsk = (askId: string, answer: string) =>
  sendJson<{ ok: true }>("/api/chat/answer", "POST", { ask_id: askId, answer });

/** Vote a dashboard up (1), down (-1), or withdraw the vote (0). Requires a
 * signed-in session — votes are one per person per dashboard, which only
 * means anything with a verified identity behind it. */
export const voteDashboard = (name: string, value: 1 | 0 | -1) =>
  sendJson<Dashboard>(`/api/dashboards/${encodeURIComponent(name)}/vote`, "POST", { value });

/** Delete a dashboard, its stored render and every vote on it. Admin only and
 * irreversible — the spec lives nowhere else, so there is nothing to restore
 * it from. 403 for a non-admin, which is the real wall; the UI only decides
 * whether to offer the button. */
export const deleteDashboard = (name: string) =>
  sendJson<{ ok: true }>(`/api/dashboards/${encodeURIComponent(name)}`, "DELETE");

/** One row of the admin console's user list (`hub_api.admin`) — a user, their
 * tier, and what they've used today against it. Admin only; 403 for anyone
 * else, the same wall `deleteDashboard` sits behind. */
export interface AdminUser {
  email: string;
  name: string;
  tier: string;
  created_at: string;
  last_login_at: string;
  questions_used_today: number;
  questions_allowed_per_day: number;
  tokens_used_today: number;
  tokens_allowed_per_day: number;
  renders_used_today: number;
  renders_allowed_per_day: number;
  saves_used_today: number;
  saves_allowed_per_day: number;
  issues_used_today: number;
  issues_allowed_per_day: number;
  api_cube_used_this_month: number;
  api_cube_allowed_per_month: number;
  api_mcp_used_this_month: number;
  api_mcp_allowed_per_month: number;
}

export const fetchAdminUsers = () => getJson<AdminUser[]>("/api/admin/users");

/** The Support page's own request/response — mirrors `hub_api.issues`'s
 * `IssueRequest`/`IssueResponse`. The same route also backs the chat
 * assistant's "file an issue" tool, so a report from either surface can
 * come back `duplicate: true` if one matching it is already open. */
export type IssueKind = "bug" | "data-quality" | "feature";

export interface IssueResponse {
  number: number;
  url: string;
  duplicate: boolean;
}

export const submitIssue = (title: string, body: string, kind: IssueKind) =>
  sendJson<IssueResponse>("/api/support/issue", "POST", { title, body, kind });

/** A personal key for the paid data API (`hub_api.api_keys`). The key itself
 * is only ever in `CreatedApiKey`, the response to creating one. */
export interface ApiKey {
  id: number;
  name: string;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
}

export interface CreatedApiKey extends ApiKey {
  key: string;
}

/** This month's data API usage per pool (`hub_api.api_usage`). */
export interface ApiUsage {
  tier: string;
  pools: Record<"cube" | "mcp", { used: number; allowance: number }>;
  resets_at: string;
}

export const fetchApiKeys = () => getJson<ApiKey[]>("/api/keys");
export const fetchApiUsage = () => getJson<ApiUsage>("/api/keys/usage");
export const createApiKey = (name: string) => sendJson<CreatedApiKey>("/api/keys", "POST", { name });

/** 204 with no body, so not `sendJson`, which parses one. */
export async function revokeApiKey(id: number): Promise<void> {
  const path = `/api/keys/${id}`;
  const response = await fetch(path, { method: "DELETE", credentials: "same-origin" });
  if (!response.ok) throw await failure(`DELETE ${path}`, response);
}
