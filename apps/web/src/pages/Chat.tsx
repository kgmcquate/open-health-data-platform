import {
  ActionBarPrimitive,
  AssistantRuntimeProvider,
  AttachmentPrimitive,
  AuiIf,
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useAuiState,
} from "@assistant-ui/react";
import { MarkdownTextPrimitive } from "@assistant-ui/react-markdown";
import remarkGfm from "remark-gfm";
import {
  ArrowUpIcon,
  CheckIcon,
  ChevronDownIcon,
  ClipboardIcon,
  ImagePlusIcon,
  MessageSquarePlusIcon,
  MoreHorizontalIcon,
  PanelLeftIcon,
  PencilIcon,
  RefreshCwIcon,
  ThumbsDownIcon,
  ThumbsUpIcon,
  Trash2Icon,
  XIcon,
} from "lucide-react";
import { useEffect, useRef, useState, type FC } from "react";
import { useAuth } from "../auth/AuthContext";
import { useHubChatRuntime, type HubChatRuntime } from "../chat/runtime";
import {
  archiveThread,
  deleteThread,
  fetchModels,
  fetchThreads,
  renameThread,
  unarchiveThread,
  type ChatModel,
  type ThreadSummary,
} from "../lib/api";

// MarkdownTextPrimitive reads the current text part from message-part
// context, so it needs no props — wrap it to satisfy the part-component type.
function MarkdownText() {
  return <MarkdownTextPrimitive className="prose-chat" remarkPlugins={[remarkGfm]} />;
}

const SUGGESTIONS = [
  "Which cities had the worst PM2.5 levels last year?",
  "Summarize recent literature on air pollution and asthma.",
  "What chronic disease measures can I query?",
];

const actionButtonClassName =
  "flex size-7 items-center justify-center rounded-field text-base-content/60 transition-colors hover:bg-base-300 hover:text-base-content";

// ---------------------------------------------------------------------------
// Sidebar — hand-rolled against hub-api's own /api/threads REST endpoints
// rather than assistant-ui's ThreadList primitives: those primitives expect a
// RemoteThreadListAdapter (title-generation streams and all), and we already
// need plain state here for the shell around the runtime anyway.
// ---------------------------------------------------------------------------

interface SidebarProps {
  threads: ThreadSummary[];
  activeThreadId: number | null;
  collapsed: boolean;
  onToggleCollapsed: () => void;
  onNewThread: () => void;
  onSwitchThread: (id: number) => void;
  onThreadsChanged: (threads: ThreadSummary[]) => void;
}

function ThreadMenu({
  thread,
  threads,
  activeThreadId,
  onSwitchThread,
  onThreadsChanged,
}: {
  thread: ThreadSummary;
  threads: ThreadSummary[];
  activeThreadId: number | null;
  onSwitchThread: (id: number) => void;
  onThreadsChanged: (threads: ThreadSummary[]) => void;
}) {
  const replace = (updated: ThreadSummary) =>
    onThreadsChanged(threads.map((t) => (t.id === updated.id ? updated : t)));

  const rename = async (e: React.MouseEvent) => {
    e.stopPropagation();
    const title = window.prompt("Rename conversation", thread.title);
    if (!title || !title.trim() || title === thread.title) return;
    replace(await renameThread(thread.id, title.trim()));
  };

  const toggleArchive = async (e: React.MouseEvent) => {
    e.stopPropagation();
    const updated =
      thread.status === "archived" ? await unarchiveThread(thread.id) : await archiveThread(thread.id);
    replace(updated);
  };

  const remove = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!window.confirm(`Delete "${thread.title || "New conversation"}"? This cannot be undone.`)) return;
    await deleteThread(thread.id);
    const remaining = threads.filter((t) => t.id !== thread.id);
    onThreadsChanged(remaining);
    if (activeThreadId === thread.id) {
      const next = remaining.find((t) => t.status === "regular");
      if (next) onSwitchThread(next.id);
    }
  };

  return (
    <div className="dropdown dropdown-end" onClick={(e) => e.stopPropagation()}>
      <button tabIndex={0} className={actionButtonClassName} aria-label="Conversation options">
        <MoreHorizontalIcon className="size-4" />
      </button>
      <ul className="dropdown-content menu bg-base-100 rounded-box z-50 w-44 p-1 shadow border border-base-300 text-sm">
        <li>
          <button onClick={rename}>
            <PencilIcon className="size-3.5" /> Rename
          </button>
        </li>
        <li>
          <button onClick={toggleArchive}>
            {thread.status === "archived" ? "Unarchive" : "Archive"}
          </button>
        </li>
        <li>
          <button onClick={remove} className="text-error">
            <Trash2Icon className="size-3.5" /> Delete
          </button>
        </li>
      </ul>
    </div>
  );
}

const Sidebar: FC<SidebarProps> = ({
  threads,
  activeThreadId,
  collapsed,
  onToggleCollapsed,
  onNewThread,
  onSwitchThread,
  onThreadsChanged,
}) => {
  const regular = threads.filter((t) => t.status === "regular");
  const archived = threads.filter((t) => t.status === "archived");

  if (collapsed) {
    return (
      <div className="hidden h-full w-12 shrink-0 flex-col items-center gap-2 border-r border-base-300 bg-base-200/50 py-2 md:flex">
        <button
          className={actionButtonClassName}
          onClick={onToggleCollapsed}
          aria-label="Show sidebar"
        >
          <PanelLeftIcon className="size-4" />
        </button>
        <button className={actionButtonClassName} onClick={onNewThread} aria-label="New chat">
          <MessageSquarePlusIcon className="size-4" />
        </button>
      </div>
    );
  }

  return (
    <aside className="hidden h-full w-64 shrink-0 flex-col overflow-hidden border-r border-base-300 bg-base-200/50 md:flex">
      <div className="flex h-12 shrink-0 items-center gap-1 px-2">
        <button
          className={actionButtonClassName}
          onClick={onToggleCollapsed}
          aria-label="Hide sidebar"
        >
          <PanelLeftIcon className="size-4" />
        </button>
        <span className="ml-1 truncate text-sm font-medium">Conversations</span>
        <button
          className={`${actionButtonClassName} ml-auto`}
          onClick={onNewThread}
          aria-label="New chat"
        >
          <MessageSquarePlusIcon className="size-4" />
        </button>
      </div>
      <div className="flex-1 overflow-x-hidden overflow-y-auto px-2 pb-3">
        {regular.length === 0 && (
          <p className="px-2 py-4 text-xs opacity-50">No conversations yet.</p>
        )}
        <ul className="menu menu-sm gap-0.5 p-0">
          {regular.map((thread) => (
            <li key={thread.id}>
              <a
                className={`group flex items-center gap-1 ${thread.id === activeThreadId ? "menu-active" : ""}`}
                onClick={() => onSwitchThread(thread.id)}
              >
                <span className="min-w-0 flex-1 truncate">{thread.title || "New conversation"}</span>
                <span className="opacity-0 group-hover:opacity-100">
                  <ThreadMenu
                    thread={thread}
                    threads={threads}
                    activeThreadId={activeThreadId}
                    onSwitchThread={onSwitchThread}
                    onThreadsChanged={onThreadsChanged}
                  />
                </span>
              </a>
            </li>
          ))}
        </ul>

        {archived.length > 0 && (
          <details className="mt-3">
            <summary className="cursor-pointer px-2 text-xs opacity-60">
              Archived ({archived.length})
            </summary>
            <ul className="menu menu-sm gap-0.5 p-0">
              {archived.map((thread) => (
                <li key={thread.id}>
                  <a
                    className={`group flex items-center gap-1 opacity-70 ${thread.id === activeThreadId ? "menu-active" : ""}`}
                    onClick={() => onSwitchThread(thread.id)}
                  >
                    <span className="min-w-0 flex-1 truncate">{thread.title || "New conversation"}</span>
                    <span className="opacity-0 group-hover:opacity-100">
                      <ThreadMenu
                        thread={thread}
                        threads={threads}
                        activeThreadId={activeThreadId}
                        onSwitchThread={onSwitchThread}
                        onThreadsChanged={onThreadsChanged}
                      />
                    </span>
                  </a>
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
    </aside>
  );
};

// ---------------------------------------------------------------------------
// Messages
// ---------------------------------------------------------------------------

function ChatAttachment() {
  return (
    <AttachmentPrimitive.Root className="group/thumbnail relative">
      <div className="h-16 w-16 overflow-hidden rounded-field border border-base-300 bg-base-100">
        <AttachmentPrimitive.unstable_Thumb className="h-full w-full object-cover text-[10px]" />
      </div>
      <AttachmentPrimitive.Remove
        className="absolute -top-1.5 -right-1.5 flex size-5 items-center justify-center rounded-full bg-neutral text-neutral-content opacity-0 transition-opacity group-hover/thumbnail:opacity-100"
        aria-label="Remove attachment"
      >
        <XIcon className="size-3" />
      </AttachmentPrimitive.Remove>
    </AttachmentPrimitive.Root>
  );
}

function EditComposer() {
  return (
    <ComposerPrimitive.Root className="flex w-full max-w-[80%] flex-col gap-2 self-end rounded-box border border-base-300 bg-base-100 px-3 py-2 shadow-sm">
      <ComposerPrimitive.Input
        autoFocus
        rows={2}
        className="w-full resize-none bg-transparent text-sm outline-none"
      />
      <div className="flex justify-end gap-2">
        <ComposerPrimitive.Cancel className="btn btn-ghost btn-xs">Cancel</ComposerPrimitive.Cancel>
        <ComposerPrimitive.Send className="btn btn-primary btn-xs">
          Save &amp; resend
        </ComposerPrimitive.Send>
      </div>
    </ComposerPrimitive.Root>
  );
}

function UserMessage() {
  return (
    <MessagePrimitive.Root className="group/message flex flex-col items-end gap-1 px-4 py-2">
      <div className="max-w-[80%] rounded-box bg-primary px-4 py-2 text-primary-content shadow-sm">
        <MessagePrimitive.Parts>
          {({ part }) => {
            if (part.type === "text") {
              return <span className="whitespace-pre-wrap">{part.text}</span>;
            }
            if (part.type === "image") {
              return (
                <img
                  src={part.image}
                  alt="Attachment"
                  className="mt-1 max-h-48 rounded-field"
                />
              );
            }
            return null;
          }}
        </MessagePrimitive.Parts>
      </div>
      <ActionBarPrimitive.Root className="flex items-center gap-0.5 opacity-0 transition-opacity group-hover/message:opacity-100">
        <ActionBarPrimitive.Edit className={actionButtonClassName}>
          <PencilIcon className="size-3.5" />
        </ActionBarPrimitive.Edit>
      </ActionBarPrimitive.Root>
    </MessagePrimitive.Root>
  );
}

// `render_dashboard` (hub_api.dashboards) answers with a full HTML document —
// Vega loaded from a CDN, the chart's own data table/query in a <details>,
// and a script that posts its rendered height so this frame can size itself
// (there is no same-origin access into a sandboxed iframe to measure it
// directly). `sandbox` omits allow-same-origin for exactly that isolation,
// but keeps allow-scripts (Vega must run) and allow-popups (the chart's own
// "..." export menu opens a new tab).
function DashboardEmbed({ html }: { html: string }) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(320);

  useEffect(() => {
    function onMessage(e: MessageEvent) {
      if (e.source !== iframeRef.current?.contentWindow) return;
      const data = e.data as { type?: string; height?: number } | undefined;
      if (data?.type === "iframe:height" && typeof data.height === "number") {
        setHeight(Math.max(160, Math.ceil(data.height)));
      }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  return (
    <iframe
      ref={iframeRef}
      srcDoc={html}
      sandbox="allow-scripts allow-popups"
      title="Dashboard"
      className="w-full rounded-box border border-base-300 bg-base-100"
      style={{ height }}
    />
  );
}

function ToolCallFallback({
  toolName,
  args,
  result,
  isError,
  status,
  resultFormat,
}: {
  toolName: string;
  args: unknown;
  result: unknown;
  isError?: boolean;
  status: { type: string };
  resultFormat?: string;
}) {
  const [open, setOpen] = useState(false);
  const isRunning = status.type === "running";
  const isDashboard = resultFormat === "html" && !isError && typeof result === "string";

  return (
    <div className="my-1 text-xs">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-2 text-base-content/70 transition-colors hover:text-base-content"
      >
        {isRunning ? (
          <span className="loading loading-spinner loading-xs" />
        ) : isError ? (
          <XIcon className="size-3.5 text-error" />
        ) : (
          <CheckIcon className="size-3.5 text-success" />
        )}
        <span className="font-mono">{toolName}</span>
        <ChevronDownIcon
          className={`size-3.5 opacity-60 transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>
      {isDashboard && (
        <div className="mt-1 ml-5">
          <DashboardEmbed html={result} />
        </div>
      )}
      {open && (
        <div className="mt-1 ml-5 space-y-2 rounded-field border border-base-300 bg-base-200 p-2">
          <div>
            <div className="mb-0.5 font-semibold opacity-50">Input</div>
            <pre className="overflow-x-auto font-mono whitespace-pre-wrap">
              {JSON.stringify(args, null, 2)}
            </pre>
          </div>
          {result !== undefined && !isDashboard && (
            <div>
              <div className="mb-0.5 font-semibold opacity-50">Output</div>
              <pre className="overflow-x-auto font-mono whitespace-pre-wrap">
                {typeof result === "string" ? result : JSON.stringify(result, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function FeedbackButtons() {
  const submitted = useAuiState((s) => s.message.metadata?.submittedFeedback?.type);
  return (
    <>
      <ActionBarPrimitive.FeedbackPositive
        className={`${actionButtonClassName} ${submitted === "positive" ? "text-primary" : ""}`}
      >
        <ThumbsUpIcon className="size-3.5" />
      </ActionBarPrimitive.FeedbackPositive>
      <ActionBarPrimitive.FeedbackNegative
        className={`${actionButtonClassName} ${submitted === "negative" ? "text-error" : ""}`}
      >
        <ThumbsDownIcon className="size-3.5" />
      </ActionBarPrimitive.FeedbackNegative>
    </>
  );
}

function ThinkingDots() {
  return (
    <span className="thinking-dots ml-0.5 inline-flex" aria-hidden="true">
      <span>.</span>
      <span>.</span>
      <span>.</span>
    </span>
  );
}

function AssistantMessage() {
  return (
    <MessagePrimitive.Root className="group/message flex flex-col px-4 py-2">
      <div className="prose-chat max-w-[85%] text-sm leading-relaxed">
        <MessagePrimitive.Parts>
          {({ part }) => {
            if (part.type === "text") return <MarkdownText />;
            if (part.type === "reasoning") {
              const isRunning = part.status?.type === "running";
              return (
                <details className="my-1 text-xs italic opacity-60">
                  <summary>Thinking{isRunning ? <ThinkingDots /> : "…"}</summary>
                  <p className="whitespace-pre-wrap">{part.text}</p>
                </details>
              );
            }
            if (part.type === "tool-call") {
              return (
                <ToolCallFallback
                  toolName={part.toolName}
                  args={part.args}
                  result={part.result}
                  isError={part.isError}
                  status={part.status}
                  // Set by runtime.ts's "tool_result" handler for a
                  // render_dashboard call; not part of assistant-ui's own type.
                  resultFormat={(part as { resultFormat?: string }).resultFormat}
                />
              );
            }
            return null;
          }}
        </MessagePrimitive.Parts>
        <AuiIf condition={(s) => s.message.content.length === 0}>
          <span className="loading loading-dots loading-sm opacity-60" />
        </AuiIf>
      </div>
      <ActionBarPrimitive.Root className="mt-1 flex items-center gap-0.5 opacity-0 transition-opacity group-hover/message:opacity-100">
        <ActionBarPrimitive.Copy className={actionButtonClassName}>
          <AuiIf condition={(s) => s.message.isCopied}>
            <CheckIcon className="size-3.5" />
          </AuiIf>
          <AuiIf condition={(s) => !s.message.isCopied}>
            <ClipboardIcon className="size-3.5" />
          </AuiIf>
        </ActionBarPrimitive.Copy>
        <FeedbackButtons />
        <ActionBarPrimitive.Reload className={actionButtonClassName}>
          <RefreshCwIcon className="size-3.5" />
        </ActionBarPrimitive.Reload>
      </ActionBarPrimitive.Root>
    </MessagePrimitive.Root>
  );
}

// ---------------------------------------------------------------------------
// Composer + model picker
// ---------------------------------------------------------------------------

interface ModelPickerProps {
  models: ChatModel[];
  model: string;
  onModelChange: (model: string) => void;
}

function ModelPicker({ models, model, onModelChange }: ModelPickerProps) {
  if (models.length === 0) return null;
  const current = models.find((m) => m.id === model) ?? models[0];
  return (
    <div className="dropdown">
      <button
        tabIndex={0}
        aria-label="Choose model"
        className="flex h-8 items-center gap-1 rounded-field px-2 text-sm whitespace-nowrap text-base-content/70 transition hover:bg-base-300"
      >
        <span>{current?.label ?? "Model"}</span>
        <ChevronDownIcon className="size-3.5 opacity-60" />
      </button>
      <ul className="dropdown-content menu bg-base-100 rounded-box z-50 max-h-80 w-72 flex-nowrap overflow-y-auto p-1 shadow border border-base-300 text-sm">
        {models.map((m) => (
          <li key={m.id}>
            <button
              onClick={() => onModelChange(m.id)}
              className="flex items-center gap-2"
            >
              <span className="flex size-4 items-center justify-center text-primary">
                {m.id === model ? <CheckIcon className="size-3.5" /> : null}
              </span>
              <span className="flex-1 truncate text-left">{m.label}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Composer({ models, model, onModelChange }: ModelPickerProps) {
  return (
    <ComposerPrimitive.Root className="flex w-full flex-col gap-2 rounded-box border border-base-300 bg-base-100 px-3.5 pt-3 pb-2.5 shadow-sm">
      <ComposerPrimitive.Input
        placeholder="Ask about air quality, chronic disease, drug safety…"
        rows={1}
        className="block max-h-60 min-h-6 w-full resize-none bg-transparent outline-none placeholder:text-base-content/40"
      />
      <div className="flex w-full items-center gap-2">
        <ComposerPrimitive.AddAttachment
          aria-label="Add image"
          className={actionButtonClassName}
        >
          <ImagePlusIcon className="size-4" />
        </ComposerPrimitive.AddAttachment>
        <ModelPicker models={models} model={model} onModelChange={onModelChange} />
        <div className="ml-auto flex items-center gap-1">
          <AuiIf condition={(s) => s.thread.isRunning}>
            <ComposerPrimitive.Cancel
              aria-label="Stop"
              className="flex size-8 items-center justify-center rounded-field bg-error text-error-content transition-colors hover:opacity-90"
            >
              <div className="size-2.5 rounded-[2px] bg-current" />
            </ComposerPrimitive.Cancel>
          </AuiIf>
          <AuiIf condition={(s) => !s.thread.isRunning}>
            <ComposerPrimitive.Send
              aria-label="Send"
              className="flex size-8 items-center justify-center rounded-field bg-primary text-primary-content transition-colors hover:opacity-90 disabled:pointer-events-none disabled:opacity-40"
            >
              <ArrowUpIcon className="size-4" />
            </ComposerPrimitive.Send>
          </AuiIf>
        </div>
      </div>
      <AuiIf condition={(s) => s.composer.attachments.length > 0}>
        <div className="-mx-1 -mb-1 flex flex-row gap-2 overflow-x-auto pt-1">
          <ComposerPrimitive.Attachments>{() => <ChatAttachment />}</ComposerPrimitive.Attachments>
        </div>
      </AuiIf>
    </ComposerPrimitive.Root>
  );
}

// ---------------------------------------------------------------------------
// Thread
// ---------------------------------------------------------------------------

function ChatThread({
  models,
  model,
  onModelChange,
  suggestions,
  isRunning,
}: ModelPickerProps & Pick<HubChatRuntime, "suggestions" | "isRunning">) {
  return (
    <ThreadPrimitive.Root className="flex h-full flex-col">
      <AuiIf condition={(s) => s.thread.isEmpty}>
        <div className="flex grow flex-col items-center justify-center px-4">
          <div className="mx-auto flex w-full max-w-5xl flex-col items-stretch gap-5">
            <p className="flex items-center justify-center gap-3 text-2xl font-bold sm:text-3xl">
              <span className="inline-flex h-9 w-9 items-center justify-center rounded-box bg-primary text-primary-content">
                ✚
              </span>
              <span>Ask the platform anything</span>
            </p>
            <p className="text-center text-sm opacity-70">
              Answers come only from curated metrics and literature — every
              number traces back to the semantic layer. Population-level data
              only; not medical advice.
            </p>
            <Composer models={models} model={model} onModelChange={onModelChange} />
            <div className="flex flex-wrap items-center justify-center gap-2">
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
        </div>
      </AuiIf>

      <AuiIf condition={(s) => !s.thread.isEmpty}>
        <ThreadPrimitive.Viewport className="flex grow flex-col overflow-y-auto px-4 pt-6">
          <div className="mx-auto w-full max-w-5xl">
            <ThreadPrimitive.Messages
              components={{
                UserMessage,
                AssistantMessage,
                EditComposer,
              }}
            />
          </div>
        </ThreadPrimitive.Viewport>
        <div className="sticky bottom-0 mx-auto w-full max-w-5xl bg-gradient-to-b from-transparent via-base-100/90 to-base-100 px-4 pt-4 pb-3">
          {/* Follow-ups for the answer that just landed — same chips as the
              welcome screen, but proposed by the model about its own answer
              (the `done` event's `suggestions`). Hidden while a turn runs so
              they never dangle under a streaming draft. */}
          {!isRunning && suggestions.length > 0 && (
            <div className="flex flex-wrap items-center justify-center gap-2 pb-2">
              {suggestions.map((s) => (
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
          )}
          <Composer models={models} model={model} onModelChange={onModelChange} />
          <p className="pt-2 text-center text-[11px] opacity-50">
            Locked-down agent: curated tools only, server-side token limits,
            every turn logged.
          </p>
        </div>
      </AuiIf>
    </ThreadPrimitive.Root>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function Chat() {
  const { user, signIn } = useAuth();
  const [models, setModels] = useState<ChatModel[]>([]);
  const [model, setModel] = useState("");
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  const upsertThread = (thread: ThreadSummary) =>
    setThreads((prev) => {
      const next = prev.some((t) => t.id === thread.id)
        ? prev.map((t) => (t.id === thread.id ? thread : t))
        : [thread, ...prev];
      return [...next].sort((a, b) => b.updated_at.localeCompare(a.updated_at));
    });

  const { runtime, threadId, newThread, switchThread, suggestions, isRunning } = useHubChatRuntime(
    model,
    upsertThread,
  );

  useEffect(() => {
    fetchModels()
      .then((fetched) => {
        setModels(fetched);
        // Keep whichever the server flags default; fall back to the first
        // entry so `model` is never "" while `fetched` is non-empty. The
        // server returns 503 for an empty request only when no backend is
        // configured; otherwise it rejects an empty model field with a 400.
        setModel((current) => current || fetched.find((m) => m.default)?.id || fetched[0]?.id || "");
      })
      .catch(() => setModels([]));
  }, []);

  useEffect(() => {
    if (user) fetchThreads().then(setThreads).catch(() => setThreads([]));
  }, [user]);

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
    <div className="flex h-[calc(100vh-4.5rem)] w-full overflow-hidden">
      <Sidebar
        threads={threads}
        activeThreadId={threadId}
        collapsed={sidebarCollapsed}
        onToggleCollapsed={() => setSidebarCollapsed((c) => !c)}
        onNewThread={newThread}
        onSwitchThread={(id) => void switchThread(id)}
        onThreadsChanged={setThreads}
      />
      <div className="min-w-0 flex-1">
        <AssistantRuntimeProvider runtime={runtime}>
          <ChatThread
            models={models}
            model={model}
            onModelChange={setModel}
            suggestions={suggestions}
            isRunning={isRunning}
          />
        </AssistantRuntimeProvider>
      </div>
    </div>
  );
}
