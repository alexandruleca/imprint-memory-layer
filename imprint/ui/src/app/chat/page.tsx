"use client";

import { useEffect, useRef, useState } from "react";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  getChatSessions,
  createChatSession,
  getChatSession,
  deleteChatSession,
  streamChat,
} from "@/lib/api";
import { ChatSessionsSkeleton } from "@/components/loaders";
import { ThinkingDots, ToolWorkingIndicator, TypingCursor } from "@/components/chat-indicators";
import type { ChatSession, ChatMessage } from "@/lib/types";

export default function ChatPage() {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(true);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [streamText, setStreamText] = useState("");
  const [thinking, setThinking] = useState(false);
  const [workingTool, setWorkingTool] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    getChatSessions()
      .then((d) => setSessions(d.sessions))
      .finally(() => setSessionsLoading(false));
  }, []);

  async function selectSession(id: string) {
    setActiveId(id);
    const sess = await getChatSession(id) as { messages?: ChatMessage[] };
    setMessages(sess.messages || []);
    setStreamText("");
  }

  async function newSession() {
    const sess = await createChatSession();
    setSessions((prev) => [sess, ...prev]);
    setActiveId(sess.id);
    setMessages([]);
  }

  async function delSession(id: string) {
    await deleteChatSession(id);
    setSessions((prev) => prev.filter((s) => s.id !== id));
    if (activeId === id) {
      setActiveId(null);
      setMessages([]);
    }
  }

  function sendMessage(e: React.FormEvent) {
    e.preventDefault();
    if (!input.trim() || !activeId || streaming) return;
    const msg = input.trim();
    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: msg }]);
    setStreaming(true);
    setStreamText("");
    setThinking(true);
    setWorkingTool(null);

    let accumulated = "";
    streamChat(activeId, msg, (ev) => {
      if (ev.type === "token") {
        setThinking(false);
        setWorkingTool(null);
        accumulated += ev.text as string;
        setStreamText(accumulated);
      } else if (ev.type === "tool_call") {
        setThinking(false);
        setWorkingTool(ev.name as string);
        accumulated += `\n[Tool: ${ev.name}]\n`;
        setStreamText(accumulated);
      } else if (ev.type === "tool_result") {
        setWorkingTool(null);
        accumulated += `[Result: ${(ev.result as string || "").slice(0, 200)}]\n`;
        setStreamText(accumulated);
      } else if (ev.type === "assistant_message") {
        setMessages((prev) => [...prev, { role: "assistant", content: ev.text as string }]);
        setStreamText("");
        setStreaming(false);
        setThinking(false);
        setWorkingTool(null);
      } else if (ev.type === "done") {
        if (accumulated && !streaming) return;
        setMessages((prev) => [...prev, { role: "assistant", content: accumulated }]);
        setStreamText("");
        setStreaming(false);
        setThinking(false);
        setWorkingTool(null);
      } else if (ev.type === "error") {
        setMessages((prev) => [...prev, { role: "assistant", content: `Error: ${ev.error}` }]);
        setStreamText("");
        setStreaming(false);
        setThinking(false);
        setWorkingTool(null);
      }
    });
  }

  useEffect(() => {
    scrollRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamText, thinking, workingTool]);

  return (
    <div className="flex h-screen">
      <div className="w-56 border-r border-border flex flex-col">
        <div className="p-3 border-b border-border">
          <button onClick={newSession} className="w-full text-sm bg-primary text-primary-foreground rounded px-3 py-1.5">
            New Chat
          </button>
        </div>
        <ScrollArea className="flex-1">
          {sessionsLoading ? (
            <ChatSessionsSkeleton count={5} />
          ) : (
            <div className="p-2 space-y-0.5">
              {sessions.map((s) => (
                <div
                  key={s.id}
                  className={`p-2 rounded text-xs cursor-pointer flex justify-between group ${
                    activeId === s.id ? "bg-accent" : "hover:bg-muted"
                  }`}
                  onClick={() => selectSession(s.id)}
                >
                  <span className="truncate">{s.title || "New chat"}</span>
                  <button
                    className="opacity-0 group-hover:opacity-100 text-destructive text-xs"
                    onClick={(e) => { e.stopPropagation(); delSession(s.id); }}
                  >
                    x
                  </button>
                </div>
              ))}
            </div>
          )}
        </ScrollArea>
      </div>

      <div className="flex-1 flex flex-col">
        <ScrollArea className="flex-1 p-4">
          <div className="space-y-3 max-w-3xl mx-auto">
            {messages.map((m, i) => (
              <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                <div className={`max-w-[80%] rounded-lg p-3 text-sm ${
                  m.role === "user"
                    ? "bg-primary text-primary-foreground"
                    : m.role === "tool"
                    ? "bg-muted text-xs font-mono"
                    : "bg-card border border-border"
                }`}>
                  <pre className="whitespace-pre-wrap font-sans">{m.content}</pre>
                </div>
              </div>
            ))}

            {thinking && !streamText && (
              <div className="flex justify-start">
                <ThinkingDots />
              </div>
            )}

            {workingTool && (
              <div className="flex justify-start">
                <ToolWorkingIndicator toolName={workingTool} />
              </div>
            )}

            {streamText && (
              <div className="flex justify-start">
                <div className="max-w-[80%] rounded-lg p-3 text-sm bg-card border border-border">
                  <pre className="whitespace-pre-wrap font-sans">
                    {streamText}
                    {streaming && <TypingCursor />}
                  </pre>
                </div>
              </div>
            )}
            <div ref={scrollRef} />
          </div>
        </ScrollArea>

        <form onSubmit={sendMessage} className="p-4 border-t border-border">
          <div className="flex gap-2 max-w-3xl mx-auto">
            <Input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={activeId ? "Ask about your codebase..." : "Create a new chat first"}
              disabled={!activeId || streaming}
              className="flex-1"
            />
            <button
              type="submit"
              className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm"
              disabled={!activeId || streaming || !input.trim()}
            >
              Send
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
