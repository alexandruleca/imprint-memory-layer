"use client";

import { useEffect, useRef, useState } from "react";
import { useUiMode } from "@/lib/ui-mode";
import Link from "next/link";
import { ArrowDown, AudioLines, CircleAlert, Info, PanelLeft } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button, buttonVariants } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { LogoMark } from "@/components/logo-mark";
import { cn } from "@/lib/utils";
import {
  createChatSession, deleteChatSession, followChat, getChatSession, getChatSessions, getChatStatus,
  answerChat, renameChatSession, stopChat, streamChat,
} from "@/lib/api";
import type { ChatEvent, ChatMessage, ChatSession, ChatStatus, ChatSummary } from "@/lib/types";
import { answerStep, applyEvent, pendingReply, turnsFromHistory, waitingQuestion, type AssistantTurn, type Turn } from "./_lib/turns";
import { Composer } from "./_components/composer";
import { Conversation } from "./_components/conversation";
import { SessionList } from "./_components/session-list";
import { useCached } from "@/lib/use-cached";

/** The chat this browser had open last; reopened on the next visit. */
const LAST_CHAT = "imprint.chat.last";

const SUGGESTIONS = [
  "What did we decide recently?",
  "Which projects are indexed?",
  "What do you know about this workspace?",
];

function storedChat(): string | null {
  try { return localStorage.getItem(LAST_CHAT); } catch { return null; }
}

function rememberChat(id: string | null) {
  try {
    if (id) localStorage.setItem(LAST_CHAT, id);
    else localStorage.removeItem(LAST_CHAT);
  } catch {}
}

/** The stored messages of a chat whose reply is still being written, without
 * what that reply already stored: watching it replays those from its start. */
function beforeReply(messages: ChatMessage[]): ChatMessage[] {
  const last = messages.findLastIndex((message) => message.role === "user");
  return last < 0 ? messages : messages.slice(0, last + 1);
}

/** The server titles a chat after its first line; show the same at once. */
const titleFrom = (text: string) => text.trim().split("\n")[0].slice(0, 60);

function modelLabel(status: ChatStatus | null): string {
  if (!status) return "";
  if (status.provider !== "local") return `${status.provider} · ${status.model ?? ""}`;
  const file = status.model_file || status.model_path.split(/[\\/]/).pop() || "";
  return `Local · ${file.replace(/\.gguf$/i, "")}`;
}

function SetupNotice({ status }: { status: ChatStatus | null }) {
  if (!status) return null;
  if (status.error && status.provider === "local") {
    return (
      <Alert variant="destructive">
        <CircleAlert />
        <AlertTitle>Local chat can&apos;t run</AlertTitle>
        <AlertDescription>{status.error}</AlertDescription>
      </Alert>
    );
  }
  if (status.error) {
    return (
      <Alert variant="destructive">
        <CircleAlert />
        <AlertTitle>Chat can&apos;t reach {status.provider}</AlertTitle>
        <AlertDescription>
          {status.error}. Set it in <Link href="/settings">Settings</Link>, or switch <code>chat.provider</code> to local.
        </AlertDescription>
      </Alert>
    );
  }
  if (status.provider !== "local" || status.model_ready) return null;
  return (
    <p className="flex items-start justify-center gap-2 text-xs text-muted-foreground">
      <Info className="mt-px size-3.5 shrink-0" aria-hidden />
      <span>Your first message sets up local chat: Imprint downloads the model (a few GB) on this device, once.</span>
    </p>
  );
}

const NO_CHATS: ChatSession[] = [];

export default function ChatPage() {
  // The chat list shows at once from the last answer while it is fetched
  // again; kept in memory only, as titles are conversation text (ADR 0152).
  const chats = useCached("chat-sessions", () => getChatSessions().then(({ sessions }) => sessions), { persist: false });
  const sessions = chats.data ?? (chats.error ? NO_CHATS : null);
  const chatsRef = useRef(chats);
  useEffect(() => { chatsRef.current = chats; });
  const [activeId, setActiveId] = useState<string | null>(null);
  const { simple } = useUiMode();
  const [turns, setTurns] = useState<Turn[]>([]);
  // What Imprint reads in place of the chat's earliest exchanges (ADR 0141).
  const [summary, setSummary] = useState<ChatSummary | null>(null);
  const [loadingChat, setLoadingChat] = useState(false);
  const [status, setStatus] = useState<ChatStatus | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [listOpen, setListOpen] = useState(false);
  const [atBottom, setAtBottom] = useState(true);
  // Watching the open chat's reply. Aborting it only stops watching; the
  // reply goes on in the API until it is answered or stopped (ADR 0145).
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  // Bumped whenever another chat is shown, so late responses for the
  // previous one are dropped.
  const view = useRef(0);

  function refreshSessions() {
    void chatsRef.current.refresh();
  }

  /** Change the shown list at once, ahead of the server (rename, delete, a new chat). */
  function setSessions(update: (prev: ChatSession[] | null) => ChatSession[] | null) {
    const next = update(chatsRef.current.data ?? null);
    if (next) chatsRef.current.mutate(next);
  }

  async function openChat(id: string) {
    abortRef.current?.abort();
    const current = ++view.current;
    setActiveId(id);
    rememberChat(id);
    setListOpen(false);
    setLoadingChat(true);
    setStreaming(false);
    setAtBottom(true);
    let replying = false;
    try {
      const chat = await getChatSession(id);
      if (current === view.current) {
        // A reply still being written is shown from its start and followed (ADR 0145).
        replying = !!chat.replying;
        const messages = chat.messages ?? [];
        setTurns(replying ? [...turnsFromHistory(beforeReply(messages)), pendingReply()] : turnsFromHistory(messages));
        setSummary(chat.summary?.text ? chat.summary : null);
      }
    } catch {
      if (current === view.current) startNew();
    } finally {
      if (current === view.current) setLoadingChat(false);
    }
    composerRef.current?.focus();
    if (replying && current === view.current) await watch(current, (onEvent, signal) => followChat(id, onEvent, signal));
  }

  function startNew() {
    abortRef.current?.abort();
    setStreaming(false);
    view.current++;
    setActiveId(null);
    setTurns([]);
    setSummary(null);
    setLoadingChat(false);
    setListOpen(false);
    composerRef.current?.focus();
  }

  useEffect(() => {
    getChatStatus().then(setStatus).catch(() => {});
  }, []);

  // Once the list is known, reopen the chat this browser was last in, else
  // the latest one; with no chats yet, the page starts as a new chat.
  const reopened = useRef(false);
  useEffect(() => {
    if (reopened.current || !sessions) return;
    reopened.current = true;
    const last = sessions.find((session) => session.id === storedChat()) ?? sessions[0];
    if (last) void openChat(last.id);
    // openChat only touches refs and state setters.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessions]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el && atBottom) el.scrollTop = el.scrollHeight;
  }, [turns, atBottom]);

  function updateReply(change: (turn: AssistantTurn) => AssistantTurn) {
    setTurns((prev) => {
      const last = prev[prev.length - 1];
      return last?.role === "assistant" ? [...prev.slice(0, -1), change(last)] : prev;
    });
  }

  /** Watch a reply in the open chat until it ends, the user stops it, or
   * another chat is shown (which stops only the watching). */
  async function watch(current: number, open: (onEvent: (event: ChatEvent) => void, signal: AbortSignal) => Promise<void>) {
    const controller = new AbortController();
    abortRef.current = controller;
    setStreaming(true);
    try {
      await open((event) => {
        if (current !== view.current) return;
        if (event.type === "summary" && event.text) {
          setSummary({ text: event.text, covers: event.covers, exchanges: event.exchanges });
        }
        updateReply((turn) => applyEvent(turn, event));
      }, controller.signal);
    } catch (error) {
      if (current !== view.current) return;
      if (controller.signal.aborted) updateReply((turn) => ({ ...turn, stopped: true }));
      else updateReply((turn) => ({ ...turn, error: error instanceof Error ? error.message : String(error) }));
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      if (current === view.current) {
        updateReply((turn) => ({ ...turn, streaming: false }));
        setStreaming(false);
      }
      refreshSessions();
      // The first reply may have downloaded the model.
      getChatStatus().then(setStatus).catch(() => {});
    }
  }

  /** Answer the question the reply waits on (ADR 0159). Any window may: the
   * reply belongs to the API, and the answer reaches it through the API. */
  async function answer(id: string, text: string) {
    if (!activeId) return;
    await answerChat(activeId, id, text);
    updateReply((turn) => answerStep(turn, id, text));
  }

  async function send(text: string) {
    const question = waitingQuestion(turns[turns.length - 1]);
    if (streaming && question?.allowOther) { await answer(question.id, text).catch(() => {}); return; }
    if (streaming) return;
    const current = view.current;
    setStreaming(true);
    setAtBottom(true);
    setTurns((prev) => [...prev, { role: "user", text }, pendingReply()]);
    let id = activeId;
    try {
      if (!id) {
        const session = await createChatSession();
        id = session.id;
        if (current !== view.current) return;
        setActiveId(id);
        rememberChat(id);
        setSessions((prev) => [{ ...session, title: titleFrom(text) }, ...(prev ?? [])]);
      }
    } catch (error) {
      if (current !== view.current) return;
      updateReply((turn) => ({ ...turn, streaming: false, error: error instanceof Error ? error.message : String(error) }));
      setStreaming(false);
      return;
    }
    const chat = id;
    await watch(current, (onEvent, signal) => streamChat(chat, text, onEvent, signal));
  }

  /** The user's stop: the API stops the reply, and this page stops watching. */
  function stop() {
    if (activeId) void stopChat(activeId);
    abortRef.current?.abort();
  }

  // Leaving the page stops watching; the reply goes on (ADR 0145).
  useEffect(() => () => abortRef.current?.abort(), []);

  // A reply that goes on in another chat shows in the list until it ends.
  useEffect(() => {
    if (!sessions?.some((session) => session.replying)) return;
    const timer = setInterval(refreshSessions, 3000);
    return () => clearInterval(timer);
  }, [sessions]);

  function retry() {
    const lastUser = turns.findLast((turn) => turn.role === "user");
    if (!lastUser || lastUser.role !== "user") return;
    setTurns((prev) => prev.slice(0, -2));
    void send(lastUser.text);
  }

  async function rename(id: string, title: string) {
    setSessions((prev) => prev?.map((session) => (session.id === id ? { ...session, title } : session)) ?? prev);
    await renameChatSession(id, title).catch(() => {});
    refreshSessions();
  }

  async function remove(id: string) {
    await deleteChatSession(id).catch(() => {});
    setSessions((prev) => prev?.filter((session) => session.id !== id) ?? prev);
    if (storedChat() === id) rememberChat(null);
    if (activeId === id) startNew();
  }

  const list = (
    <SessionList
      sessions={sessions}
      activeId={activeId}
      onSelect={(id) => void openChat(id)}
      onNew={startNew}
      onRename={(id, title) => void rename(id, title)}
      onDelete={(id) => void remove(id)}
    />
  );
  const model = modelLabel(status);
  const title = sessions?.find((session) => session.id === activeId)?.title ?? "New chat";
  const draft = !loadingChat && turns.length === 0;

  return (
    <div className="flex h-full min-h-0">
      <aside className="hidden w-64 shrink-0 border-r border-border md:block">{list}</aside>
      <Sheet open={listOpen} onOpenChange={setListOpen}>
        <SheetContent side="left" className="w-72 p-0">
          <SheetTitle className="sr-only">Chats</SheetTitle>
          {list}
        </SheetContent>
      </Sheet>

      <section aria-label="Chat" className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-11 shrink-0 items-center gap-2 border-b border-border px-3">
          <Button variant="ghost" size="icon-sm" className="md:hidden" aria-label="Show chats" onClick={() => setListOpen(true)}>
            <PanelLeft />
          </Button>
          <h2 className="min-w-0 flex-1 truncate text-sm font-medium">{title}</h2>
          {activeId && !draft && (
            // Echo carries the chat on, by voice; what it says is added here (ADR 0146).
            <Link href={`/voice/?chat=${encodeURIComponent(activeId)}`} data-tour="chat-echo"
              className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "shrink-0 text-muted-foreground")}>
              <AudioLines /> Continue with Echo
            </Link>
          )}
        </header>

        {draft ? (
          <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-6 overflow-y-auto px-4 pb-16">
            <div className="flex flex-col items-center gap-3 text-center">
              <LogoMark className="size-10" />
              <h1 className="text-2xl font-semibold tracking-tight">What do you want to recall?</h1>
              <p className="max-w-md text-sm text-muted-foreground">
                {simple ? "Imprint answers from what you added: your projects, documents and past conversations." : "Imprint answers from your indexed projects, decisions and past conversations."}
              </p>
            </div>
            <Composer ref={composerRef} autoFocus model={model} streaming={streaming} onSend={send}
              onStop={stop} className="max-w-2xl" />
            <div className="flex flex-wrap justify-center gap-2">
              {SUGGESTIONS.map((suggestion) => (
                <Button key={suggestion} variant="outline" size="sm" className="rounded-full font-normal"
                  onClick={() => void send(suggestion)}>
                  {suggestion}
                </Button>
              ))}
            </div>
            <div className="w-full max-w-2xl"><SetupNotice status={status} /></div>
          </div>
        ) : (
          <>
            <div className="relative min-h-0 flex-1">
              <div
                ref={scrollRef}
                className="h-full overflow-y-auto"
                onScroll={(event) => {
                  const el = event.currentTarget;
                  setAtBottom(el.scrollHeight - el.scrollTop - el.clientHeight < 48);
                }}
              >
                {loadingChat ? (
                  <div className="mx-auto w-full max-w-3xl space-y-6 px-4 py-6" aria-busy="true" aria-label="Loading chat">
                    <Skeleton className="ml-auto h-10 w-2/5 rounded-2xl" />
                    <div className="space-y-2"><Skeleton className="h-4 w-full" /><Skeleton className="h-4 w-4/5" /><Skeleton className="h-4 w-3/5" /></div>
                    <Skeleton className="ml-auto h-10 w-1/3 rounded-2xl" />
                    <div className="space-y-2"><Skeleton className="h-4 w-11/12" /><Skeleton className="h-4 w-2/3" /></div>
                  </div>
                ) : (
                  <Conversation turns={turns} summary={summary} onRetry={retry} onAnswer={answer} />
                )}
              </div>
              {!atBottom && (
                <Button
                  variant="outline"
                  size="icon-sm"
                  aria-label="Scroll to latest"
                  className="absolute bottom-3 left-1/2 -translate-x-1/2 rounded-full shadow-sm"
                  onClick={() => setAtBottom(true)}
                >
                  <ArrowDown />
                </Button>
              )}
            </div>
            <div className="mx-auto w-full max-w-3xl space-y-2 px-4 pb-4">
              {status?.error && <SetupNotice status={status} />}
              <Composer ref={composerRef} model={model} streaming={streaming} onSend={send}
                answering={!!waitingQuestion(turns[turns.length - 1])?.allowOther} onStop={stop} />
            </div>
          </>
        )}
      </section>
    </div>
  );
}
