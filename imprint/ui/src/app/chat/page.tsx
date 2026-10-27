"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ArrowDown, CircleAlert, Info, PanelLeft } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { LogoMark } from "@/components/logo-mark";
import {
  createChatSession, deleteChatSession, getChatSession, getChatSessions, getChatStatus,
  renameChatSession, streamChat,
} from "@/lib/api";
import type { ChatSession, ChatStatus } from "@/lib/types";
import { applyEvent, pendingReply, turnsFromHistory, type AssistantTurn, type Turn } from "./_lib/turns";
import { Composer } from "./_components/composer";
import { Conversation } from "./_components/conversation";
import { SessionList } from "./_components/session-list";

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

export default function ChatPage() {
  const [sessions, setSessions] = useState<ChatSession[] | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [loadingChat, setLoadingChat] = useState(false);
  const [status, setStatus] = useState<ChatStatus | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [listOpen, setListOpen] = useState(false);
  const [atBottom, setAtBottom] = useState(true);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  // Bumped whenever another chat is shown, so late responses for the
  // previous one are dropped.
  const view = useRef(0);

  function refreshSessions() {
    getChatSessions().then(({ sessions }) => setSessions(sessions)).catch(() => {});
  }

  async function openChat(id: string) {
    abortRef.current?.abort();
    const current = ++view.current;
    setActiveId(id);
    rememberChat(id);
    setListOpen(false);
    setLoadingChat(true);
    setAtBottom(true);
    try {
      const chat = await getChatSession(id);
      if (current === view.current) setTurns(turnsFromHistory(chat.messages ?? []));
    } catch {
      if (current === view.current) startNew();
    } finally {
      if (current === view.current) setLoadingChat(false);
    }
    composerRef.current?.focus();
  }

  function startNew() {
    abortRef.current?.abort();
    view.current++;
    setActiveId(null);
    setTurns([]);
    setLoadingChat(false);
    setListOpen(false);
    composerRef.current?.focus();
  }

  useEffect(() => {
    getChatStatus().then(setStatus).catch(() => {});
    getChatSessions()
      .then(({ sessions }) => {
        setSessions(sessions);
        // Reopen the chat this browser was last in, else the latest one;
        // with no chats yet, the page starts as a new chat.
        const last = sessions.find((session) => session.id === storedChat()) ?? sessions[0];
        if (last) void openChat(last.id);
      })
      .catch(() => setSessions([]));
    // Runs once: openChat only touches refs and state setters.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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

  async function send(text: string) {
    if (streaming) return;
    const current = view.current;
    const controller = new AbortController();
    abortRef.current = controller;
    setStreaming(true);
    setAtBottom(true);
    setTurns((prev) => [...prev, { role: "user", text }, pendingReply()]);
    try {
      let id = activeId;
      if (!id) {
        const session = await createChatSession();
        id = session.id;
        if (current !== view.current) return;
        setActiveId(id);
        rememberChat(id);
        setSessions((prev) => [{ ...session, title: titleFrom(text) }, ...(prev ?? [])]);
      }
      await streamChat(id, text, (event) => {
        if (current === view.current) updateReply((turn) => applyEvent(turn, event));
      }, controller.signal);
    } catch (error) {
      if (current !== view.current) return;
      if (controller.signal.aborted) updateReply((turn) => ({ ...turn, stopped: true }));
      else updateReply((turn) => ({ ...turn, error: error instanceof Error ? error.message : String(error) }));
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      if (current === view.current) updateReply((turn) => ({ ...turn, streaming: false }));
      setStreaming(false);
      refreshSessions();
      // The first reply may have downloaded the model.
      getChatStatus().then(setStatus).catch(() => {});
    }
  }

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
          <h2 className="truncate text-sm font-medium">{title}</h2>
        </header>

        {draft ? (
          <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-6 overflow-y-auto px-4 pb-16">
            <div className="flex flex-col items-center gap-3 text-center">
              <LogoMark className="size-10" />
              <h1 className="text-2xl font-semibold tracking-tight">What do you want to recall?</h1>
              <p className="max-w-md text-sm text-muted-foreground">
                Imprint answers from your indexed projects, decisions and past conversations.
              </p>
            </div>
            <Composer ref={composerRef} autoFocus model={model} streaming={streaming} onSend={send}
              onStop={() => abortRef.current?.abort()} className="max-w-2xl" />
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
                  <Conversation turns={turns} onRetry={retry} />
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
                onStop={() => abortRef.current?.abort()} />
            </div>
          </>
        )}
      </section>
    </div>
  );
}
