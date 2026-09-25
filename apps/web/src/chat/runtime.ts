import { useMemo, useRef, useState } from "react";
import {
  useExternalStoreRuntime,
  type AppendMessage,
  type AssistantRuntime,
  type FeedbackAdapter,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import {
  answerAsk,
  createThread,
  fetchThread,
  submitFeedback,
  type PendingAsk,
  type ThreadSummary,
  type ThreadTurn,
  type ThreadTurnToolCall,
} from "../lib/api";

/**
 * Bridges hub-api's SSE stream (hub_api.chat) into assistant-ui's external
 * store. The backend owns the agent, the tools, the quota, the token
 * accounting, and the thread/turn log; this client only renders the event
 * stream and reflects the persisted history back once a turn lands. Events
 * the backend emits (ohdp_agent.loop):
 *   status, thinking, text, plan, tool_call, tool_result, rows, sql,
 *   articles, tool_error, warning, ask_user, ask_cancelled, done, error
 *
 * Thread *switching* is not this hook's job — `Chat.tsx` owns the sidebar and
 * the list of threads; this hook only drives whichever thread it is told is
 * active, via `threadId`.
 *
 * Message ids double as the pointer back into hub-api's turn log: a
 * persisted user/assistant pair is `u{turnId}`/`a{turnId}` — one turn is one
 * question and one answer, never split across more than two messages — which
 * is what lets `onEdit`/`onReload`/feedback address a specific turn without a
 * second id scheme. An in-flight turn gets a throwaway local id; once the
 * stream ends the whole thread is re-fetched, so the local id never has to
 * mean anything past that point.
 */

type Part = Exclude<ThreadMessageLike["content"], string>[number];

interface ServerEvent {
  type: string;
  [key: string]: unknown;
}

let nextLocalId = 0;
const localId = () => `pending-${++nextLocalId}`;

// Module-level, not created inline in the store object passed to
// useExternalStoreRuntime: that hook's effect that resyncs the runtime
// (`runtime.setAdapter(...)`) is keyed on that object's *identity*, and a
// fresh feedback closure / convertMessage function on every render meant
// `store` never had a stable reference — every unrelated re-render (every
// keystroke while editing a message, among others) forced a resync, which
// was silently closing the edit composer before its `onEdit` ever fired.
// Neither needs per-render state, so hoisting them is what actually fixes
// it, not a memo with a chance of a wrong dependency.
const feedbackAdapter: FeedbackAdapter = {
  submit: ({ message, type }) => {
    const turnId = turnIdFromMessageId(message.id);
    if (turnId !== undefined) void submitFeedback(turnId, type);
  },
};

const convertMessage = (m: ThreadMessageLike) => m;

function turnIdFromMessageId(id: string): number | undefined {
  const match = /^[ua](\d+)$/.exec(id);
  return match ? Number(match[1]) : undefined;
}

/** Mirrors the `tool_call`/`tool_result` cases of `applyEvent` below, but from
 * a persisted `chat_turns.tool_calls` entry (`ohdp_agent.loop`'s `Turn`)
 * instead of the two separate live SSE events it was built from — the result
 * is already folded into the call there, so this is a single step. */
function toolCallPart(call: ThreadTurnToolCall): Part {
  return {
    type: "tool-call",
    toolCallId: call.tool_call_id,
    toolName: call.name,
    args: (call.input ?? {}) as Record<string, never>,
    argsText: JSON.stringify(call.input ?? {}),
    result: call.result,
    isError: Boolean(call.is_error),
    resultFormat: call.format === "html" ? "html" : undefined,
  } as Part;
}

/** Rebuilds an assistant turn's content in the order text and tool calls
 * actually streamed in, from `turn.timeline` — falling back to the old
 * tool-calls-then-answer grouping for a turn logged before that column
 * existed (or one where it came back empty). */
function timelineContent(turn: ThreadTurn): Part[] {
  if (turn.timeline && turn.timeline.length > 0) {
    const callsById = new Map(turn.tool_calls.map((c) => [c.tool_call_id, c]));
    const content = turn.timeline.flatMap((entry): Part[] => {
      if (entry.type === "text") {
        return entry.text ? [{ type: "text", text: entry.text }] : [];
      }
      const call = callsById.get(entry.tool_call_id);
      return call ? [toolCallPart(call)] : [];
    });
    if (content.length > 0) return content;
  }
  const content: Part[] = turn.tool_calls.map(toolCallPart);
  if (turn.answer) content.push({ type: "text", text: turn.answer });
  return content;
}

function turnToMessages(turn: ThreadTurn): ThreadMessageLike[] {
  const messages: ThreadMessageLike[] = [
    { id: `u${turn.id}`, role: "user", content: [{ type: "text", text: turn.question }] },
  ];
  if (turn.tool_calls.length > 0 || turn.answer || turn.error) {
    const content = timelineContent(turn);
    if (turn.error) content.push({ type: "text", text: `\n\n> ❌ ${turn.error}` });
    messages.push({
      id: `a${turn.id}`,
      role: "assistant",
      content,
      metadata: turn.feedback
        ? { custom: {}, submittedFeedback: { type: turn.feedback } }
        : undefined,
    });
  }
  return messages;
}

export function textOf(content: readonly { type: string; text?: string }[]): string {
  return content
    .filter((p): p is { type: "text"; text: string } => p.type === "text")
    .map((p) => p.text)
    .join("\n");
}

function appendText(parts: Part[], text: string): Part[] {
  const last = parts[parts.length - 1];
  if (last && last.type === "text") {
    return [...parts.slice(0, -1), { ...last, text: last.text + text }];
  }
  return [...parts, { type: "text", text }];
}

/** Mirrors `appendText`: successive `thinking` events are streamed
 * token-by-token by the backend, so they must fold into the same reasoning
 * part rather than each becoming its own — otherwise the UI renders one
 * "Thinking..." dropdown per token. */
function appendReasoning(parts: Part[], text: string): Part[] {
  const last = parts[parts.length - 1];
  if (last && last.type === "reasoning") {
    return [...parts.slice(0, -1), { ...last, text: last.text + text }];
  }
  return [...parts, { type: "reasoning", text }];
}

/** Fold one SSE event into the streaming message's content parts. */
function applyEvent(parts: Part[], event: ServerEvent): Part[] {
  switch (event.type) {
    case "text":
      return appendText(parts, String(event.text ?? ""));
    case "thinking":
      return appendReasoning(parts, String(event.text ?? ""));
    case "tool_call":
      return [
        ...parts,
        {
          type: "tool-call",
          // Falls back to a local id only if the backend ever omits one —
          // real turns always carry pydantic-ai's `tool_call_id`, which is
          // what lets the matching `tool_result` event below find this part.
          toolCallId: event.tool_call_id ? String(event.tool_call_id) : localId(),
          toolName: String(event.name ?? "tool"),
          args: (event.input ?? {}) as Record<string, never>,
          argsText: JSON.stringify(event.input ?? {}),
        } as Part,
      ];
    case "tool_result": {
      const toolCallId = String(event.tool_call_id ?? "");
      return parts.map((part) => {
        if (part.type !== "tool-call" || part.toolCallId !== toolCallId) return part;
        return {
          ...part,
          result: event.result,
          isError: Boolean(event.is_error),
          // Set only for `render_dashboard`'s HTML embed (ohdp_agent.loop
          // ._looks_like_html_document) — everything else is plain text/JSON,
          // which the tool-call renderer shows as-is.
          resultFormat: event.format === "html" ? "html" : undefined,
        } as Part;
      });
    }
    case "tool_error":
      return appendText(
        parts,
        `\n\n> ⚠️ Tool **${String(event.name ?? "")}** failed: ${String(event.message ?? "")}\n\n`,
      );
    case "warning":
      return appendText(parts, `\n\n> ⚠️ ${String(event.message ?? "")}\n\n`);
    case "error":
      return appendText(parts, `\n\n> ❌ ${String(event.message ?? "")}\n\n`);
    // status/plan/rows/sql/articles are progress chatter the primitives
    // surface through the tool-call parts; nothing extra to render.
    default:
      return parts;
  }
}

async function* readSse(response: Response): AsyncGenerator<ServerEvent> {
  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      for (const line of frame.split("\n")) {
        if (line.startsWith("data: ")) {
          yield JSON.parse(line.slice(6)) as ServerEvent;
        }
      }
    }
  }
}

export interface HubChatRuntime {
  runtime: AssistantRuntime;
  /** `null` means a not-yet-persisted draft — sending the first message
   * creates the thread lazily, rather than littering the sidebar with one
   * empty thread per page visit. */
  threadId: number | null;
  newThread: () => void;
  switchThread: (id: number) => Promise<void>;
  /** Follow-up chips for the latest assistant answer, from the `done`
   * event's `suggestions` (the model's `<<<FOLLOW-UPS>>>` block, split off
   * server-side). Ephemeral — not persisted with the thread — so it is
   * cleared whenever a turn starts or a thread is switched, and only the
   * live stream's latest answer ever has any. */
  suggestions: readonly string[];
  /** The `ask_user` question the running turn is blocked on, if any — the
   * agent has stopped and is waiting on a click. Ephemeral in the same way
   * `suggestions` is, and more so: the run holding the question only exists
   * for as long as this stream does, so a reload abandons it rather than
   * resuming it. */
  pendingAsk: PendingAsk | null;
  /** Answer the pending question and let the agent continue. */
  answerPendingAsk: (answer: string) => Promise<void>;
  isRunning: boolean;
  /** The active thread's messages, in the same `ThreadMessageLike` shape the
   * store holds them in — exposed so `Chat.tsx` can quote recent turns into a
   * Support report (`transcriptUpTo`) without reaching into assistant-ui's
   * own runtime state, whose message-part types this module doesn't otherwise
   * depend on. */
  messages: readonly ThreadMessageLike[];
}

/** `model` is a GET /api/models id. The UI falls back to the first model
 * returned by the server while `fetched` is non-empty, so this is normally
 * a configured model id. `onThreadChanged` fires once a thread exists to report
 * and after every turn (title/updated_at can both change) — so `Chat.tsx`
 * can keep its own sidebar list in sync without re-fetching it wholesale. */
export function useHubChatRuntime(
  model: string,
  onThreadChanged: (thread: ThreadSummary) => void,
): HubChatRuntime {
  const [threadId, setThreadId] = useState<number | null>(null);
  const [messages, setMessages] = useState<readonly ThreadMessageLike[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [suggestions, setSuggestions] = useState<readonly string[]>([]);
  const [pendingAsk, setPendingAsk] = useState<PendingAsk | null>(null);
  // A ref, not state: aborting a stream is an imperative action, not
  // something the UI renders off of, and — as with the module-level adapters
  // above — anything reached through the store object passed to
  // useExternalStoreRuntime forces a runtime resync when its identity
  // changes, so this must not be part of what triggers a re-render.
  const abortControllerRef = useRef<AbortController | null>(null);
  // Bumped at the start of every runTurn call. A cancelled turn's tail (the
  // post-stream refetch below) keeps running in the background after
  // `isRunning` already flipped back to false, so if the user fires off
  // another message in that window, the stale tail must not clobber it with
  // an unconditional setMessages — this is what it checks before doing so.
  const runSeqRef = useRef(0);

  const newThread = () => {
    setThreadId(null);
    setMessages([]);
    setSuggestions([]);
    setPendingAsk(null);
  };

  const switchThread = async (id: number) => {
    const detail = await fetchThread(id);
    setMessages(detail.turns.flatMap(turnToMessages));
    setThreadId(id);
    setSuggestions([]);
    setPendingAsk(null);
  };

  const runTurn = async (question: string, truncateFromTurnId: number | undefined) => {
    if (!question.trim()) return;
    const mySeq = ++runSeqRef.current;
    // The previous answer's follow-ups stop being relevant the moment a new
    // question is on its way — chips mid-generation would be for the old turn.
    setSuggestions([]);
    setPendingAsk(null);

    let activeThreadId = threadId;
    let base = messages;
    if (activeThreadId === null) {
      const created = await createThread();
      activeThreadId = created.id;
      setThreadId(created.id);
      onThreadChanged(created);
      base = [];
    } else if (truncateFromTurnId !== undefined) {
      const cutIndex = base.findIndex((m) => m.id === `u${truncateFromTurnId}`);
      if (cutIndex !== -1) base = base.slice(0, cutIndex);
    }

    const userMessage: ThreadMessageLike = {
      id: localId(),
      role: "user",
      content: [{ type: "text", text: question }],
    };
    const assistantId = localId();
    const assistantMessage: ThreadMessageLike = {
      id: assistantId,
      role: "assistant",
      content: [],
      status: { type: "running" },
    };
    setMessages([...base, userMessage, assistantMessage]);
    setIsRunning(true);

    const update = (parts: Part[], status?: ThreadMessageLike["status"]) =>
      setMessages((prev) =>
        prev.map((m) => (m.id === assistantId ? { ...m, content: parts, status } : m)),
      );

    let parts: Part[] = [];
    const controller = new AbortController();
    abortControllerRef.current = controller;
    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question,
          model,
          thread_id: activeThreadId,
          truncate_from_turn_id: truncateFromTurnId ?? null,
        }),
        signal: controller.signal,
      });
      if (!response.ok || !response.body) {
        const raw = await response.text().catch(() => "");
        // FastAPI's HTTPException body is `{"detail": "..."}` — the 429 case
        // covers several independent daily gates (questions, tokens, dashboard
        // renders/saves) with different messages, so this reads whichever one
        // fired rather than collapsing all of them to one hardcoded string.
        const detail = (() => {
          try {
            const parsed = JSON.parse(raw) as { detail?: unknown };
            return typeof parsed.detail === "string" ? parsed.detail : "";
          } catch {
            return "";
          }
        })();
        throw new Error(
          response.status === 401
            ? "Sign in to use the chat."
            : response.status === 429
              ? detail || "You've hit a usage limit for now."
              : `Chat failed (${response.status}). ${detail || raw}`,
        );
      }
      for await (const event of readSse(response)) {
        // Handled here rather than in `applyEvent`: an ask is not content in
        // the message, it is the run stopping to wait on the reader, and the
        // chips for it live next to the composer (`Chat.tsx`) where the
        // follow-up chips already do. The matching `tool_call` part still
        // renders inline, and gains its result once the answer goes through.
        if (event.type === "ask_user") {
          setPendingAsk({
            ask_id: String(event.ask_id ?? ""),
            question: String(event.question ?? ""),
            options: Array.isArray(event.options)
              ? event.options.filter((o): o is string => typeof o === "string")
              : [],
            allow_other: Boolean(event.allow_other),
          });
          continue;
        }
        // The agent gave up waiting (`ASK_USER_TIMEOUT_SECONDS`) and is
        // answering anyway — take the chips away so nothing invites a click
        // that would now 404.
        if (event.type === "ask_cancelled") {
          setPendingAsk((current) =>
            current && current.ask_id === String(event.ask_id ?? "") ? null : current,
          );
          continue;
        }
        if (event.type === "done") {
          // The server sends the full, citation-checked answer at the end;
          // replace the streamed draft with it.
          parts = [{ type: "text", text: String(event.answer ?? "") }];
          const disclaimer = String(event.disclaimer ?? "");
          if (disclaimer) {
            parts = appendText(parts, `\n\n---\n*${disclaimer}*`);
          }
          update(parts);
          const nextSuggestions = Array.isArray(event.suggestions)
            ? event.suggestions.filter((s): s is string => typeof s === "string")
            : [];
          setSuggestions(nextSuggestions);
        } else {
          parts = applyEvent(parts, event);
          update(parts);
        }
      }
      update(parts, { type: "complete", reason: "stop" });
    } catch (exc) {
      if ((exc as Error).name === "AbortError") {
        update(parts, { type: "incomplete", reason: "cancelled" });
      } else {
        parts = appendText(parts, `\n\n> ❌ ${(exc as Error).message}`);
        update(parts, { type: "incomplete", reason: "error" });
      }
    } finally {
      setIsRunning(false);
      abortControllerRef.current = null;
      // Covers the cancel and error paths too: the run that was waiting on
      // the question is gone either way, so nothing is listening for a click.
      setPendingAsk(null);
    }

    // The server is the source of truth for ids, the final answer, and the
    // thread's title/updated_at — reload rather than trust the optimistic
    // draft, the same way a page refresh would show it. If the server hasn't
    // persisted the assistant message yet (cancellation race, DB lag), keep
    // the local draft so the bubble does not vanish.
    try {
      const detail = await fetchThread(activeThreadId);
      const serverMessages = detail.turns.flatMap(turnToMessages);
      const reversedIndex = [...serverMessages]
        .reverse()
        .findIndex((m: ThreadMessageLike) => m.role === "user");
      const lastUserIndex =
        reversedIndex === -1 ? -1 : serverMessages.length - 1 - reversedIndex;
      const lastUserContent = serverMessages[lastUserIndex]?.content;
      const lastUserText =
        typeof lastUserContent === "string"
          ? lastUserContent
          : textOf(lastUserContent as { type: string; text?: string }[]);
      const hasServerAnswer =
        lastUserIndex !== -1 &&
        lastUserText === question &&
        serverMessages[lastUserIndex + 1]?.role === "assistant";

      // A newer runTurn has since started (e.g. the user sent a follow-up
      // right after cancelling this one) and owns `messages` now — this
      // stale tail must not stomp on it with a snapshot of the thread from
      // before that follow-up existed.
      if (runSeqRef.current !== mySeq) return;

      if (!hasServerAnswer) {
        const localAssistant = messages.find((m) => m.id === assistantId);
        if (localAssistant) {
          setMessages([...serverMessages, localAssistant]);
          onThreadChanged(detail);
          return;
        }
      }

      setMessages(serverMessages);
      onThreadChanged(detail);
    } catch {
      // The turn already rendered from the stream; a failed refresh just
      // means ids stay local until the next successful switch/reload.
    }
  };

  /** Send the reader's choice and clear the chips. Cleared optimistically:
   * the agent is already moving on by the time the POST returns, and a 404
   * (the question stopped waiting while they read it) has the same right
   * outcome — the chips go away and the turn continues without them. */
  const answerPendingAsk = async (answer: string) => {
    const ask = pendingAsk;
    if (!ask || !answer.trim()) return;
    setPendingAsk(null);
    try {
      await answerAsk(ask.ask_id, answer.trim());
    } catch {
      // Nothing to recover: the run either continued without this answer or
      // has already ended, and either way the stream says what happened next.
    }
  };

  const onNew = async (message: AppendMessage) => {
    await runTurn(textOf(message.content), undefined);
  };

  const onEdit = async (message: AppendMessage) => {
    const turnId = message.sourceId ? turnIdFromMessageId(message.sourceId) : undefined;
    await runTurn(textOf(message.content), turnId);
  };

  const onReload = async (parentId: string | null) => {
    if (parentId === null) return;
    const turnId = turnIdFromMessageId(parentId);
    if (turnId === undefined) return;
    const original = messages.find((m) => m.id === parentId);
    const content = original ? original.content : "";
    const question = typeof content === "string" ? content : textOf(content);
    await runTurn(question, turnId);
  };

  // Memoized for the same reason the module-level adapters above are hoisted:
  // useExternalStoreRuntime resyncs the whole runtime whenever this object's
  // *identity* changes, so it must only get a new one when `messages`/
  // `isRunning` (or something `onNew`/`onEdit`/`onReload` close over)
  // actually changed — not on every render this hook happens to run for.
  const store = useMemo(
    () => ({
      messages,
      isRunning,
      onNew,
      onEdit,
      onReload,
      onCancel: async () => abortControllerRef.current?.abort(),
      convertMessage,
      adapters: { feedback: feedbackAdapter },
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- onNew/onEdit/onReload
    // are recreated each render but only ever close over threadId/model/messages,
    // already listed below; adding the closures themselves would defeat the memo.
    [messages, isRunning, threadId, model],
  );

  const runtime = useExternalStoreRuntime<ThreadMessageLike>(store);

  return {
    runtime,
    threadId,
    newThread,
    switchThread,
    suggestions,
    messages,
    pendingAsk,
    answerPendingAsk,
    isRunning,
  };
}
