import { useMemo, useRef, useState } from "react";
import {
  SimpleImageAttachmentAdapter,
  useExternalStoreRuntime,
  type AppendMessage,
  type AssistantRuntime,
  type FeedbackAdapter,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import {
  createThread,
  fetchThread,
  submitFeedback,
  type ThreadSummary,
  type ThreadTurn,
} from "../lib/api";

/**
 * Bridges hub-api's SSE stream (hub_api.chat) into assistant-ui's external
 * store. The backend owns the agent, the tools, the quota, the token
 * accounting, and the thread/turn log; this client only renders the event
 * stream and reflects the persisted history back once a turn lands. Events
 * the backend emits (ohdp_agent.loop):
 *   status, thinking, text, plan, tool_call, tool_result, rows, sql,
 *   articles, tool_error, warning, done, error
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
// fresh SimpleImageAttachmentAdapter / feedback closure / convertMessage
// function on every render meant `store` never had a stable reference —
// every unrelated re-render (every keystroke while editing a message, among
// others) forced a resync, which was silently closing the edit composer
// before its `onEdit` ever fired. None of these three need per-render state,
// so hoisting them is what actually fixes it, not a memo with a chance of a
// wrong dependency.
const attachmentAdapter = new SimpleImageAttachmentAdapter();

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

function turnToMessages(turn: ThreadTurn): ThreadMessageLike[] {
  const messages: ThreadMessageLike[] = [
    { id: `u${turn.id}`, role: "user", content: [{ type: "text", text: turn.question }] },
  ];
  if (turn.answer || turn.error) {
    let text = turn.answer;
    if (turn.error) text += `\n\n> ❌ ${turn.error}`;
    messages.push({
      id: `a${turn.id}`,
      role: "assistant",
      content: [{ type: "text", text }],
      metadata: turn.feedback
        ? { custom: {}, submittedFeedback: { type: turn.feedback } }
        : undefined,
    });
  }
  return messages;
}

function textOf(content: readonly { type: string; text?: string }[]): string {
  return content
    .filter((p): p is { type: "text"; text: string } => p.type === "text")
    .map((p) => p.text)
    .join("\n");
}

function imagesOf(content: readonly { type: string; image?: string }[]): string[] {
  return content
    .filter((p): p is { type: "image"; image: string } => p.type === "image")
    .map((p) => p.image);
}

function appendText(parts: Part[], text: string): Part[] {
  const last = parts[parts.length - 1];
  if (last && last.type === "text") {
    return [...parts.slice(0, -1), { ...last, text: last.text + text }];
  }
  return [...parts, { type: "text", text }];
}

/** Fold one SSE event into the streaming message's content parts. */
function applyEvent(parts: Part[], event: ServerEvent): Part[] {
  switch (event.type) {
    case "text":
      return appendText(parts, String(event.text ?? ""));
    case "thinking":
      return [...parts, { type: "reasoning", text: String(event.text ?? "") } as Part];
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
  isRunning: boolean;
}

/** `model` is a GET /api/models id, or "" for the built-in Claude default.
 * `onThreadChanged` fires once a thread exists to report — on lazy creation,
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
  // A ref, not state: aborting a stream is an imperative action, not
  // something the UI renders off of, and — as with the module-level adapters
  // above — anything reached through the store object passed to
  // useExternalStoreRuntime forces a runtime resync when its identity
  // changes, so this must not be part of what triggers a re-render.
  const abortControllerRef = useRef<AbortController | null>(null);

  const newThread = () => {
    setThreadId(null);
    setMessages([]);
    setSuggestions([]);
  };

  const switchThread = async (id: number) => {
    const detail = await fetchThread(id);
    setMessages(detail.turns.flatMap(turnToMessages));
    setThreadId(id);
    setSuggestions([]);
  };

  const runTurn = async (
    question: string,
    images: string[],
    truncateFromTurnId: number | undefined,
  ) => {
    if (!question.trim()) return;
    // The previous answer's follow-ups stop being relevant the moment a new
    // question is on its way — chips mid-generation would be for the old turn.
    setSuggestions([]);

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
      content: [
        { type: "text", text: question },
        ...images.map((image): Part => ({ type: "image", image })),
      ],
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
          images,
        }),
        signal: controller.signal,
      });
      if (!response.ok || !response.body) {
        const detail = await response.text().catch(() => "");
        throw new Error(
          response.status === 401
            ? "Sign in to use the chat."
            : response.status === 429
              ? "Monthly question limit reached."
              : `Chat failed (${response.status}). ${detail}`,
        );
      }
      for await (const event of readSse(response)) {
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

  const onNew = async (message: AppendMessage) => {
    await runTurn(textOf(message.content), imagesOf(message.content), undefined);
  };

  const onEdit = async (message: AppendMessage) => {
    const turnId = message.sourceId ? turnIdFromMessageId(message.sourceId) : undefined;
    await runTurn(textOf(message.content), imagesOf(message.content), turnId);
  };

  const onReload = async (parentId: string | null) => {
    if (parentId === null) return;
    const turnId = turnIdFromMessageId(parentId);
    if (turnId === undefined) return;
    const original = messages.find((m) => m.id === parentId);
    const content = original ? original.content : "";
    const question = typeof content === "string" ? content : textOf(content);
    await runTurn(question, [], turnId);
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
      adapters: {
        attachments: attachmentAdapter,
        feedback: feedbackAdapter,
      },
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- onNew/onEdit/onReload
    // are recreated each render but only ever close over threadId/model/messages,
    // already listed below; adding the closures themselves would defeat the memo.
    [messages, isRunning, threadId, model],
  );

  const runtime = useExternalStoreRuntime<ThreadMessageLike>(store);

  return { runtime, threadId, newThread, switchThread, suggestions, isRunning };
}
