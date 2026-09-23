/** Thin fetch wrappers for hub-api. All same-origin; the session cookie does
 * the talking (no tokens in JS). */

export interface User {
  email: string;
  name: string;
  tier: string;
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
 * `source` is "chat" for a dashboard the assistant saved and "curated" for one
 * of ours. Neither is trusted more than the other — both are the same
 * validated spec — it is there so a reader knows where a chart came from. */
export interface Dashboard {
  id: number;
  /** The kebab-case key it was saved under — also its URL. */
  name: string;
  title: string;
  description: string;
  caption: string | null;
  topics: string[];
  featured: boolean;
  source: "chat" | "curated";
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

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path, { credentials: "same-origin" });
  if (!response.ok) throw new Error(`${path} failed: ${response.status}`);
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
export const fetchLiterature = (topic?: string) =>
  getJson<LiteratureItem[]>(topic ? `/api/literature?topic=${encodeURIComponent(topic)}` : "/api/literature");

export interface ChatModel {
  id: string;
  label: string;
  default: boolean;
}

/** The models explicitly configured in config/models.yaml, for the picker.
 * Auto-discovered models are usable by id but omitted from the picker. */
export const fetchModels = () => getJson<ChatModel[]>("/api/models");

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
  method: "POST" | "PATCH" | "DELETE",
  body?: unknown,
): Promise<T> {
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`${method} ${path} failed: ${response.status}`);
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
