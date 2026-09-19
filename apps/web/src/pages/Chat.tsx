import {
  AssistantRuntimeProvider,
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
} from "@assistant-ui/react";
import { MarkdownTextPrimitive } from "@assistant-ui/react-markdown";
import { useEffect, useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { useHubChatRuntime } from "../chat/runtime";
import { fetchModels, type ChatModel } from "../lib/api";

// MarkdownTextPrimitive reads the current text part from message-part
// context, so it needs no props — wrap it to satisfy the part-component type.
function MarkdownText() {
  return <MarkdownTextPrimitive className="prose-chat" />;
}

const SUGGESTIONS = [
  "Which cities had the worst PM2.5 levels last year?",
  "Summarize recent literature on air pollution and asthma.",
  "What chronic disease measures can I query?",
];

function UserMessage() {
  return (
    <MessagePrimitive.Root className="flex justify-end px-4 py-2">
      <div className="max-w-[80%] rounded-box bg-primary text-primary-content px-4 py-2 shadow-sm">
        <MessagePrimitive.Content />
      </div>
    </MessagePrimitive.Root>
  );
}

function ToolCallFallback({ toolName }: { toolName: string }) {
  return (
    <div className="my-1 flex items-center gap-2 text-xs opacity-70">
      <span className="loading loading-spinner loading-xs" />
      <span className="font-mono">{toolName}</span>
    </div>
  );
}

function AssistantMessage() {
  return (
    <MessagePrimitive.Root className="flex justify-start px-4 py-2">
      <div className="max-w-[85%] rounded-box bg-base-200 px-4 py-2 shadow-sm">
        <div className="prose-chat text-sm leading-relaxed">
          <MessagePrimitive.Content
            components={{
              Text: MarkdownText,
              Reasoning: ({ text }) => (
                <details className="text-xs opacity-60 italic my-1">
                  <summary>Thinking…</summary>
                  <p className="whitespace-pre-wrap">{text}</p>
                </details>
              ),
              tools: { Fallback: ToolCallFallback },
            }}
          />
        </div>
        <MessagePrimitive.If hasContent={false}>
          <span className="loading loading-dots loading-sm opacity-60" />
        </MessagePrimitive.If>
      </div>
    </MessagePrimitive.Root>
  );
}

interface ChatThreadProps {
  models: ChatModel[];
  model: string;
  onModelChange: (model: string) => void;
}

function ChatThread({ models, model, onModelChange }: ChatThreadProps) {
  return (
    <ThreadPrimitive.Root className="flex h-full flex-col">
      <ThreadPrimitive.Viewport className="flex-1 overflow-y-auto py-4">
        <ThreadPrimitive.Empty>
          <div className="mx-auto max-w-xl px-4 pt-16 text-center">
            <h2 className="text-2xl font-bold">Ask the platform anything</h2>
            <p className="mt-2 opacity-70 text-sm">
              Answers come only from curated metrics and literature — every
              number traces back to the semantic layer. Population-level data
              only; not medical advice.
            </p>
            <div className="mt-6 flex flex-col gap-2">
              {SUGGESTIONS.map((s) => (
                <ThreadPrimitive.Suggestion
                  key={s}
                  prompt={s}
                  method="replace"
                  autoSend
                  className="btn btn-outline btn-sm normal-case"
                >
                  {s}
                </ThreadPrimitive.Suggestion>
              ))}
            </div>
          </div>
        </ThreadPrimitive.Empty>
        <ThreadPrimitive.Messages
          components={{ UserMessage, AssistantMessage }}
        />
      </ThreadPrimitive.Viewport>

      <div className="border-t border-base-300 bg-base-100 p-3">
        {models.length > 1 && (
          <div className="mx-auto mb-2 flex max-w-3xl items-center gap-2">
            <label htmlFor="chat-model" className="text-xs opacity-60">
              Model
            </label>
            <select
              id="chat-model"
              className="select select-bordered select-xs"
              value={model}
              onChange={(e) => onModelChange(e.target.value)}
            >
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label}
                </option>
              ))}
            </select>
          </div>
        )}
        <ComposerPrimitive.Root className="mx-auto flex max-w-3xl items-end gap-2">
          <ComposerPrimitive.Input
            autoFocus
            placeholder="Ask about air quality, chronic disease, drug safety…"
            className="textarea textarea-bordered flex-1 min-h-12 max-h-40"
          />
          <ThreadPrimitive.If running={false}>
            <ComposerPrimitive.Send className="btn btn-primary">
              Send
            </ComposerPrimitive.Send>
          </ThreadPrimitive.If>
          <ThreadPrimitive.If running>
            <ComposerPrimitive.Cancel className="btn btn-error btn-outline">
              Stop
            </ComposerPrimitive.Cancel>
          </ThreadPrimitive.If>
        </ComposerPrimitive.Root>
        <p className="mx-auto mt-1 max-w-3xl text-[11px] opacity-50">
          Locked-down agent: curated tools only, server-side token limits,
          every turn logged.
        </p>
      </div>
    </ThreadPrimitive.Root>
  );
}

export default function Chat() {
  const { user, signIn } = useAuth();
  const [models, setModels] = useState<ChatModel[]>([]);
  const [model, setModel] = useState("");
  const runtime = useHubChatRuntime(model);

  useEffect(() => {
    fetchModels()
      .then((fetched) => {
        setModels(fetched);
        // Keep whichever the server flags default, rather than always the
        // first entry — model order isn't guaranteed to put it there.
        setModel((current) => current || fetched.find((m) => m.default)?.id || "");
      })
      .catch(() => setModels([]));
  }, []);

  if (user === undefined) {
    return (
      <div className="flex justify-center py-24">
        <span className="loading loading-spinner loading-lg" />
      </div>
    );
  }

  if (user === null) {
    return (
      <div className="hero py-24">
        <div className="hero-content text-center">
          <div className="max-w-md">
            <h1 className="text-3xl font-bold">Sign in to chat</h1>
            <p className="py-4 opacity-70">
              The chatbot burns real tokens, so it sits behind sign-in with a
              monthly question budget. Free tier included.
            </p>
            <button className="btn btn-primary" onClick={signIn}>
              Sign in
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto flex h-[calc(100vh-12rem)] max-w-5xl flex-col px-4 py-4">
      <AssistantRuntimeProvider runtime={runtime}>
        <ChatThread models={models} model={model} onModelChange={setModel} />
      </AssistantRuntimeProvider>
    </div>
  );
}
