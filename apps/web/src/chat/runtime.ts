import { useRef, useState } from "react";
import {
  useExternalStoreRuntime,
  type AppendMessage,
  type ThreadMessageLike,
} from "@assistant-ui/react";

/**
 * Bridges hub-api's SSE stream (hub_api.chat) into assistant-ui's external
 * store. The backend owns the agent, the tools, the quota and the token
 * accounting; this client only renders the event stream. Events the backend
 * emits (ohdp_agent.loop):
 *   status, thinking, text, plan, tool_call, rows, sql, articles,
 *   tool_error, warning, done, error
 */

type Part = Exclude<ThreadMessageLike["content"], string>[number];

interface ServerEvent {
  type: string;
  [key: string]: unknown;
}

let nextId = 0;
const uid = () => `m${++nextId}`;

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
      return [
        ...parts,
        { type: "reasoning", text: String(event.text ?? "") } as Part,
      ];
    case "tool_call":
      return [
        ...parts,
        {
          type: "tool-call",
          toolCallId: uid(),
          toolName: String(event.name ?? "tool"),
          args: (event.input ?? {}) as Record<string, never>,
          argsText: JSON.stringify(event.input ?? {}),
        } as Part,
      ];
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

export function useHubChatRuntime() {
  const [messages, setMessages] = useState<readonly ThreadMessageLike[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const onNew = async (message: AppendMessage) => {
    const question = message.content
      .filter((p): p is { type: "text"; text: string } => p.type === "text")
      .map((p) => p.text)
      .join("\n");
    if (!question.trim()) return;

    const userMessage: ThreadMessageLike = {
      id: uid(),
      role: "user",
      content: [{ type: "text", text: question }],
      createdAt: new Date(),
    };
    const assistantId = uid();
    const assistantMessage: ThreadMessageLike = {
      id: assistantId,
      role: "assistant",
      content: [],
      createdAt: new Date(),
      status: { type: "running" },
    };
    setMessages((prev) => [...prev, userMessage, assistantMessage]);
    setIsRunning(true);

    const update = (parts: Part[], status?: ThreadMessageLike["status"]) =>
      setMessages((prev) =>
        prev.map((m) => (m.id === assistantId ? { ...m, content: parts, status } : m)),
      );

    let parts: Part[] = [];
    abortRef.current = new AbortController();
    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
        signal: abortRef.current.signal,
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
      abortRef.current = null;
    }
  };

  return useExternalStoreRuntime<ThreadMessageLike>({
    messages,
    isRunning,
    onNew,
    onCancel: async () => abortRef.current?.abort(),
    convertMessage: (m) => m,
  });
}
