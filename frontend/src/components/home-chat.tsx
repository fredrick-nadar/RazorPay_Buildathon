"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { FormattedMarkdown } from "@/components/ui/formatted-markdown";
import ThreeSphere from "@/components/ui/three-sphere";
import { LineNav } from "@/components/line-nav";
import {
  IconArrowUp,
  IconCheck,
  IconCopy,
  IconMessageSquare,
  IconPlug,
  IconPlus,
  IconRefresh,
  IconSidebar,
  IconTrash,
} from "@/components/icons";

export interface ChatMessageItem {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: string;
}

export interface ChatSession {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: number;
  messages: ChatMessageItem[];
}

const SESSIONS_STORAGE_KEY = "argus_copilot_sessions_v2";
const ACTIVE_SESSION_STORAGE_KEY = "argus_copilot_active_session_id_v2";

const CRUNCHING_MESSAGES = [
  "Analyzing live SQLite ledger...",
  "Evaluating reconciliation records & variances...",
  "Formulating verified response...",
];

const STARTER_PROMPTS = [
  {
    title: "Deterministic Match Rate",
    query: "What is the current deterministic match rate?",
    description: "Inspect reconciled batch volume, auto-match ratio, and rule passes",
    badge: "Match Engine",
  },
  {
    title: "Unresolved Residuals",
    query: "How many cases are unresolved and why?",
    description: "Analyze residual exceptions requiring merchant controller review",
    badge: "Exceptions",
  },
  {
    title: "Financial Variance Breakdown",
    query: "Show the total financial variance breakdown",
    description: "Audit exact signed paise deltas across gateway and merchant ledgers",
    badge: "Ledger Audit",
  },
  {
    title: "Duplicate Ledger Postings",
    query: "Explain the duplicate ledger posting exceptions",
    description: "Review double-credited settlements and automated verification proofs",
    badge: "Safety Verifier",
  },
];

interface HomeChatProps {
  onTriggerRun: (profile: "dev" | "adversarial", mode: "rules-only" | "agent") => Promise<void>;
  onOpenConnectModal: () => void;
  telemetry?: {
    runId: string;
    matchRate: string;
    casesCount?: number;
  } | null;
}

export function HomeChat({
  onOpenConnectModal,
  telemetry,
}: HomeChatProps) {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessageItem[]>([]);
  const [inputPrompt, setInputPrompt] = useState("");
  const [loading, setLoading] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [crunchingIdx, setCrunchingIdx] = useState(0);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [hydrated, setHydrated] = useState(false);
  const [activeTurnHref, setActiveTurnHref] = useState<string>("");

  const [voiceState, setVoiceState] = useState<{
    status: "idle" | "listening" | "parsing" | "speaking" | "result" | "refused" | "error";
    transcript: string;
    assistantMessage: string | null;
    language: string;
  }>({
    status: "idle",
    transcript: "",
    assistantMessage: null,
    language: "en-IN",
  });

  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // Real-time Voice State Synchronization
  useEffect(() => {
    const onVoiceState = (event: Event) => {
      const custom = event as CustomEvent<{
        status: "idle" | "listening" | "parsing" | "speaking" | "result" | "refused" | "error";
        transcript: string;
        assistantMessage: string | null;
        language: string;
      }>;
      if (custom.detail) {
        setVoiceState(custom.detail);
      }
    };

    window.addEventListener("argus-voice-state", onVoiceState);
    return () => window.removeEventListener("argus-voice-state", onVoiceState);
  }, []);

  // 1. Initial Load: Clear legacy raw chat cache and load structured sessions
  useEffect(() => {
    try {
      // Purge legacy flat chat history to start completely clean as requested
      localStorage.removeItem("argus_copilot_chat_history_v1");

      const savedSessions = localStorage.getItem(SESSIONS_STORAGE_KEY);
      if (savedSessions) {
        const parsed = JSON.parse(savedSessions) as ChatSession[];
        if (Array.isArray(parsed) && parsed.length > 0) {
          setSessions(parsed);
          const savedActiveId = localStorage.getItem(ACTIVE_SESSION_STORAGE_KEY);
          const active = parsed.find((s) => s.id === savedActiveId) || parsed[0];
          if (active) {
            setActiveSessionId(active.id);
            setMessages(active.messages);
          }
        }
      }
    } catch {
      /* ignore storage read error */
    } finally {
      setHydrated(true);
    }
  }, []);

  // 2. Save sessions to localStorage
  useEffect(() => {
    if (!hydrated) return;
    try {
      if (sessions.length > 0) {
        localStorage.setItem(SESSIONS_STORAGE_KEY, JSON.stringify(sessions));
      } else {
        localStorage.removeItem(SESSIONS_STORAGE_KEY);
      }
      if (activeSessionId) {
        localStorage.setItem(ACTIVE_SESSION_STORAGE_KEY, activeSessionId);
      } else {
        localStorage.removeItem(ACTIVE_SESSION_STORAGE_KEY);
      }
    } catch {
      /* ignore storage write error */
    }
  }, [sessions, activeSessionId, hydrated]);

  // Auto-scroll to bottom of chat
  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, loading]);

  // Cycle thinking / crunching messages every 850ms while loading
  useEffect(() => {
    if (!loading) return;
    const interval = setInterval(() => {
      setCrunchingIdx((prev) => (prev + 1) % CRUNCHING_MESSAGES.length);
    }, 850);
    return () => clearInterval(interval);
  }, [loading]);

  const startNewChat = () => {
    setActiveSessionId(null);
    setMessages([]);
    setInputPrompt("");
  };

  const selectSession = (session: ChatSession) => {
    setActiveSessionId(session.id);
    setMessages(session.messages);
    setSidebarOpen(false);
  };

  const deleteSession = (sessionId: string, event: React.MouseEvent) => {
    event.stopPropagation();
    const updated = sessions.filter((s) => s.id !== sessionId);
    setSessions(updated);
    if (activeSessionId === sessionId) {
      if (updated.length > 0 && updated[0]) {
        setActiveSessionId(updated[0].id);
        setMessages(updated[0].messages);
      } else {
        startNewChat();
      }
    }
  };

  const clearAllSessions = () => {
    setSessions([]);
    startNewChat();
    try {
      localStorage.removeItem(SESSIONS_STORAGE_KEY);
      localStorage.removeItem(ACTIVE_SESSION_STORAGE_KEY);
    } catch {
      /* ignore */
    }
  };

  const handleSendMessage = async (rawText: string) => {
    const text = rawText.trim();
    if (!text || loading) return;

    const timestamp = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    const userMsg: ChatMessageItem = {
      id: `user-${Date.now()}`,
      role: "user",
      content: text,
      timestamp,
    };

    const updatedMessages = [...messages, userMsg];
    setMessages(updatedMessages);
    setInputPrompt("");
    setLoading(true);
    setCrunchingIdx(0);

    // If this is the start of a new chat session, initialize it in Recent Chats with first prompt as title
    let currentSessionId = activeSessionId;
    if (!currentSessionId) {
      currentSessionId = `session-${Date.now()}`;
      const sessionTitle = text.length > 44 ? `${text.slice(0, 44)}...` : text;
      const newSession: ChatSession = {
        id: currentSessionId,
        title: sessionTitle,
        createdAt: new Date().toLocaleDateString(undefined, {
          month: "short",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
        }),
        updatedAt: Date.now(),
        messages: updatedMessages,
      };
      setActiveSessionId(currentSessionId);
      setSessions((prev) => [newSession, ...prev]);
    } else {
      setSessions((prev) =>
        prev.map((s) =>
          s.id === currentSessionId
            ? { ...s, messages: updatedMessages, updatedAt: Date.now() }
            : s,
        ),
      );
    }

    try {
      const historyPayload = updatedMessages.slice(-20).map((m) => ({
        role: m.role,
        content: m.content,
      }));

      const res = await fetch("/api/v1/chat/message", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: text,
          history: historyPayload,
          page_context: {
            tab: "home",
            active_run_id: telemetry?.runId,
          },
        }),
      });

      if (!res.ok) {
        throw new Error(`Server returned ${res.status}`);
      }

      const data = (await res.json()) as {
        success: boolean;
        reply: string;
      };

      const assistantMsg: ChatMessageItem = {
        id: `assistant-${Date.now()}`,
        role: "assistant",
        content: data.reply,
        timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
      };

      const finalMessages = [...updatedMessages, assistantMsg];
      setMessages(finalMessages);

      setSessions((prev) =>
        prev.map((s) =>
          s.id === currentSessionId
            ? { ...s, messages: finalMessages, updatedAt: Date.now() }
            : s,
        ),
      );
    } catch (err) {
      const errorMsg: ChatMessageItem = {
        id: `assistant-error-${Date.now()}`,
        role: "assistant",
        content: `An error occurred: ${err instanceof Error ? err.message : String(err)}. Please try again.`,
        timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
      };
      const finalMessages = [...updatedMessages, errorMsg];
      setMessages(finalMessages);

      setSessions((prev) =>
        prev.map((s) =>
          s.id === currentSessionId
            ? { ...s, messages: finalMessages, updatedAt: Date.now() }
            : s,
        ),
      );
    } finally {
      setLoading(false);
    }
  };

  const handleCopy = (id: string, text: string) => {
    void navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const conversationTurns = useMemo(() => {
    const turns: Array<{
      id: string;
      query: string;
      responseSnippet?: string;
      timestamp?: string;
      index: number;
    }> = [];
    messages.forEach((m, idx) => {
      if (m.role === "user") {
        const nextAssistantMsg = messages[idx + 1];
        const snippet =
          nextAssistantMsg && nextAssistantMsg.role === "assistant"
            ? nextAssistantMsg.content.slice(0, 160).replace(/[#*`_]/g, "").trim()
            : undefined;
        turns.push({
          id: m.id,
          query: m.content,
          responseSnippet: snippet,
          timestamp: m.timestamp,
          index: turns.length,
        });
      }
    });
    return turns;
  }, [messages]);

  return (
    <div className="relative flex flex-col w-full max-w-5xl mx-auto min-h-[640px] h-full justify-between pb-4">
      {/* Top Header Bar: Recent Chats Toggle & New Chat Action */}
      <div className="flex items-center justify-between px-2 sm:px-3 pb-2 border-b border-slate-100/90 mb-2 min-h-[38px]">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setSidebarOpen((prev) => !prev)}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 hover:text-slate-900 shadow-2xs transition-colors"
          >
            <IconSidebar size={14} className="text-slate-600" />
            <span>Recent Chats</span>
            {sessions.length > 0 && (
              <span className="rounded-full bg-slate-100 px-1.5 py-0.2 text-[10px] font-mono text-slate-600">
                {sessions.length}
              </span>
            )}
          </button>
        </div>

        <AnimatePresence>
          {messages.length > 0 && (
            <motion.button
              key="top-new-chat-btn"
              initial={{ opacity: 0, scale: 0.92 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.92 }}
              transition={{ duration: 0.18, ease: "easeOut" }}
              type="button"
              onClick={startNewChat}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-slate-900 px-3 py-1 text-xs font-medium text-white shadow-2xs hover:bg-slate-800 transition-colors"
            >
              <IconPlus size={13} className="text-white" />
              <span>New Chat</span>
            </motion.button>
          )}
        </AnimatePresence>
      </div>

      <div className="relative flex-1 flex w-full overflow-hidden">
        {/* ================= RECENT CHATS SLIDEOUT / SIDEBAR ================= */}
        <AnimatePresence initial={false}>
          {sidebarOpen && (
            <motion.aside
              key="recent-chats-drawer"
              initial={{ width: 0, opacity: 0 }}
              animate={{ width: 288, opacity: 1 }}
              exit={{ width: 0, opacity: 0 }}
              transition={{ duration: 0.32, ease: [0.25, 1, 0.5, 1] }}
              className="relative z-30 shrink-0 overflow-hidden bg-white/95 backdrop-blur-md border-r border-slate-200 flex flex-col justify-between shadow-xs"
            >
              <div className="w-72 p-3 h-full flex flex-col justify-between shrink-0 space-y-3">
                <div className="space-y-3 flex-1 flex flex-col overflow-hidden">
                  <div className="flex items-center justify-between pb-2 border-b border-slate-100">
                    <div className="flex items-center gap-1.5 text-xs font-semibold text-slate-900">
                      <IconMessageSquare size={14} />
                      <span>Recent Chats</span>
                    </div>
                    <button
                      type="button"
                      onClick={() => setSidebarOpen(false)}
                      className="text-xs text-slate-400 hover:text-slate-700 p-1"
                    >
                      ✕
                    </button>
                  </div>

                  <div className="flex-1 overflow-y-auto space-y-1.5 pr-1">
                    {sessions.length === 0 ? (
                      <div className="text-center py-8 text-xs text-slate-400 space-y-1">
                        <p>No recent chats yet.</p>
                        <p className="text-[11px] text-slate-400">Your query history will appear here.</p>
                      </div>
                    ) : (
                      sessions.map((session) => {
                        const isActive = session.id === activeSessionId;
                        return (
                          <div
                            key={session.id}
                            onClick={() => selectSession(session)}
                            className={`group relative flex flex-col p-2.5 rounded-xl border transition-all cursor-pointer text-left ${
                              isActive
                                ? "border-slate-300 bg-slate-100/80 text-slate-900 shadow-2xs"
                                : "border-transparent hover:border-slate-200 hover:bg-slate-50 text-slate-700"
                            }`}
                          >
                            <div className="flex items-center justify-between gap-1">
                              <span className="text-xs font-semibold truncate leading-tight">
                                {session.title}
                              </span>
                              <button
                                type="button"
                                onClick={(e) => deleteSession(session.id, e)}
                                title="Delete chat"
                                className="opacity-0 group-hover:opacity-100 text-slate-400 hover:text-rose-600 transition-opacity p-0.5"
                              >
                                <IconTrash size={12} />
                              </button>
                            </div>
                            <div className="flex items-center justify-between text-[10px] text-slate-400 font-mono mt-1">
                              <span>{session.createdAt}</span>
                              <span>{session.messages.length} msgs</span>
                            </div>
                          </div>
                        );
                      })
                    )}
                  </div>
                </div>

                {sessions.length > 0 && (
                  <div className="pt-2 border-t border-slate-100">
                    <button
                      type="button"
                      onClick={clearAllSessions}
                      className="w-full text-center text-[11px] font-medium text-slate-400 hover:text-rose-600 transition-colors py-1"
                    >
                      Clear All History
                    </button>
                  </div>
                )}
              </div>
            </motion.aside>
          )}
        </AnimatePresence>

        {/* Left Hand Side Vertical Line Nav Rail */}
        {conversationTurns.length > 0 && voiceState.status === "idle" && (
          <aside className="hidden sm:flex flex-col pt-3 pl-1 pr-2 select-none shrink-0 sticky top-2 self-start z-20">
            <LineNav
              items={conversationTurns.map((turn) => ({
                title: turn.query,
                href: `#${turn.id}`,
                id: turn.id,
                timestamp: turn.timestamp,
                subtitle: turn.responseSnippet,
              }))}
              activeHref={activeTurnHref || `#${conversationTurns[conversationTurns.length - 1]?.id ?? ""}`}
              showTitleOnlyOnHover={true}
              onItemClick={(item, e) => {
                e.preventDefault();
                setActiveTurnHref(item.href);
                const targetId = item.id || item.href.replace(/^#/, "");
                const el = document.getElementById(targetId);
                el?.scrollIntoView({ behavior: "smooth", block: "center" });
              }}
            />
          </aside>
        )}

        {/* ================= CHAT STREAM / FEED ================= */}
        <motion.div
          layout
          transition={{ duration: 0.32, ease: [0.25, 1, 0.5, 1] }}
          className="flex-1 flex flex-col overflow-y-auto px-2 sm:px-4 py-2 space-y-5 min-w-0"
        >
          <AnimatePresence mode="wait">
            {voiceState.status !== "idle" ? (
              /* ================= High-End 3D Polished Sphere (Three.js) ================= */
              <motion.div
                layout
                key="voice-3d-sphere-view"
                initial={{ opacity: 0, scale: 0.94, y: 10 }}
                animate={{ opacity: 1, scale: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.94, y: -10 }}
                transition={{
                  layout: { duration: 0.32, ease: [0.25, 1, 0.5, 1] },
                  opacity: { duration: 0.25 },
                  scale: { duration: 0.25 },
                }}
                className="flex flex-col items-center justify-center text-center pt-4 pb-4 space-y-4"
              >
                {/* 3D Polished Avatar Sphere with 180° Horizontal Sweep & Gyroscopic Precession */}
                <div className="relative w-64 h-64 sm:w-72 sm:h-72 mx-auto flex items-center justify-center">
                  <ThreeSphere
                    status={voiceState.status}
                    size={280}
                  />
                </div>

                {/* Dynamic Live Subtitle Captions */}
                <div className="w-full max-w-lg px-4 min-h-[56px] flex items-center justify-center">
                  {voiceState.transcript ? (
                    <motion.p
                      key="voiceTranscript"
                      initial={{ opacity: 0, y: 4 }}
                      animate={{ opacity: 1, y: 0 }}
                      className="text-base sm:text-lg font-medium text-slate-900 leading-relaxed"
                    >
                      &ldquo;{voiceState.transcript}&rdquo;
                    </motion.p>
                  ) : voiceState.assistantMessage ? (
                    <motion.p
                      key="voiceAssistantMessage"
                      initial={{ opacity: 0, y: 4 }}
                      animate={{ opacity: 1, y: 0 }}
                      className="text-sm sm:text-base font-normal text-slate-700 leading-relaxed line-clamp-3"
                    >
                      {voiceState.assistantMessage}
                    </motion.p>
                  ) : voiceState.status === "speaking" ? (
                    <motion.p
                      key="speakingGreeting"
                      initial={{ opacity: 0, y: 4 }}
                      animate={{ opacity: 1, y: 0 }}
                      className="text-base sm:text-lg font-medium text-slate-900 leading-relaxed"
                    >
                      &ldquo;Hello, I&apos;m ARGUS. How can I assist with your reconciliation ledger today?&rdquo;
                    </motion.p>
                  ) : voiceState.status === "listening" ? (
                    <p className="text-xs sm:text-sm text-slate-500 font-mono tracking-wider uppercase animate-pulse">
                      Listening to your voice... (ask anything)
                    </p>
                  ) : (
                    <p className="text-xs sm:text-sm text-slate-500 font-mono tracking-wider uppercase">
                      Interpreting intent & checking safety policy...
                    </p>
                  )}
                </div>

                {/* Action: Stop Voice Conversation */}
                <button
                  type="button"
                  onClick={() => {
                    setVoiceState({
                      status: "idle",
                      transcript: "",
                      assistantMessage: null,
                      language: "en-IN",
                    });
                    window.dispatchEvent(new CustomEvent("argus-voice-stop"));
                    window.dispatchEvent(
                      new CustomEvent("argus-voice-mic-toggle", { detail: { action: "stop" } }),
                    );
                  }}
                  className="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-4 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50 hover:text-slate-900 shadow-2xs transition-all cursor-pointer active:scale-95"
                >
                  <span className="h-2.5 w-2.5 rounded-full bg-rose-500 animate-pulse" />
                  <span>Stop Voice Conversation</span>
                </button>
              </motion.div>
            ) : messages.length === 0 ? (
              /* Clean, Minimal Hero State (when voice is idle) */
              <motion.div
                layout
                key="hero-empty-state"
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -10 }}
                transition={{
                  layout: { duration: 0.32, ease: [0.25, 1, 0.5, 1] },
                  opacity: { duration: 0.2 },
                  y: { duration: 0.2 },
                }}
                className="flex flex-col items-center justify-center text-center pt-16 pb-8 space-y-5"
              >
                <div className="space-y-1.5 max-w-md">
                  <h1 className="text-xl sm:text-2xl font-semibold tracking-tight text-slate-900">
                    Reconciliation Copilot
                  </h1>
                  <p className="text-xs sm:text-sm text-slate-500 leading-normal">
                    Query live reconciliation batches, ledger totals, and exception evidence with zero financial hallucinations.
                  </p>
                </div>

                {/* 4 Engaging Starter Cards (Displayed only when chat length is zero) */}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 w-full max-w-xl pt-2">
                  {STARTER_PROMPTS.map((item, idx) => (
                    <button
                      key={idx}
                      type="button"
                      onClick={() => void handleSendMessage(item.query)}
                      className="group flex flex-col justify-between rounded-xl border border-slate-200 bg-white p-3.5 text-left shadow-2xs hover:border-slate-300 hover:shadow-xs hover:bg-slate-50/80 transition-all active:scale-[0.99] cursor-pointer"
                    >
                      <div className="space-y-1">
                        <div className="flex items-center justify-between">
                          <span className="text-xs font-semibold text-slate-800 group-hover:text-slate-900">
                            {item.title}
                          </span>
                          <span className="rounded-full bg-slate-100 px-1.5 py-0.5 text-[9px] font-mono font-medium text-slate-500">
                            {item.badge}
                          </span>
                        </div>
                        <p className="text-[11px] text-slate-500 leading-relaxed group-hover:text-slate-600 line-clamp-2">
                          {item.description}
                        </p>
                      </div>
                      <div className="mt-2.5 flex items-center text-[10px] font-mono text-slate-400 group-hover:text-slate-700 transition-colors">
                        <span>Ask AI &rarr;</span>
                      </div>
                    </button>
                  ))}
                </div>
              </motion.div>
            ) : (
              /* Active Clean Conversation Thread */
              <motion.div
                layout
                key="messages-thread"
                transition={{ layout: { duration: 0.32, ease: [0.25, 1, 0.5, 1] } }}
                className="space-y-4 pt-1"
              >
                {messages.map((msg) => (
                  <motion.div
                    layout
                    key={msg.id}
                    id={msg.id}
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{
                      layout: { duration: 0.32, ease: [0.25, 1, 0.5, 1] },
                      opacity: { duration: 0.15 },
                      y: { duration: 0.15 },
                    }}
                    className={`flex w-full ${msg.role === "user" ? "justify-end" : "justify-start"}`}
                  >
                    {msg.role === "user" ? (
                      /* Minimal User Message (No avatar icon, pure professional bubble) */
                      <div className="flex flex-col items-end max-w-[85%] sm:max-w-[75%] space-y-1">
                        <div className="rounded-2xl rounded-tr-xs bg-slate-900 px-4 py-2.5 text-white shadow-xs">
                          <p className="text-xs sm:text-sm font-medium leading-relaxed whitespace-pre-wrap">
                            {msg.content}
                          </p>
                        </div>
                        <span className="text-[10px] text-slate-400 font-mono pr-1">{msg.timestamp}</span>
                      </div>
                    ) : (
                      /* Minimal Assistant Message (No avatar icon, pure clean card) */
                      <div className="flex flex-col max-w-[95%] sm:max-w-[88%] space-y-1">
                        <div className="rounded-2xl rounded-tl-xs border border-slate-200/90 bg-white p-4 sm:p-5 shadow-2xs text-slate-900">
                          <FormattedMarkdown content={msg.content} />

                          {/* Clean Minimal Footer: Timestamp & Copy */}
                          <div className="mt-3 flex items-center justify-between border-t border-slate-100 pt-2 text-[11px] text-slate-400">
                            <span className="font-mono">{msg.timestamp}</span>
                            <button
                              type="button"
                              onClick={() => handleCopy(msg.id, msg.content)}
                              className="inline-flex items-center gap-1 text-slate-400 hover:text-slate-700 transition-colors"
                            >
                              {copiedId === msg.id ? (
                                <>
                                  <IconCheck size={12} className="text-emerald-600" />
                                  <span className="text-emerald-600 font-medium">Copied</span>
                                </>
                              ) : (
                                <>
                                  <IconCopy size={12} />
                                  <span>Copy</span>
                                </>
                              )}
                            </button>
                          </div>
                        </div>
                      </div>
                    )}
                  </motion.div>
                ))}

                {/* Minimal Thinking / Crunching Indicator */}
                {loading && (
                  <motion.div
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    className="flex items-center gap-2.5 rounded-2xl rounded-tl-xs border border-slate-200 bg-white/90 px-4 py-3 shadow-2xs max-w-sm"
                  >
                    <span className="h-2 w-2 rounded-full bg-slate-700 animate-pulse shrink-0" />
                    <motion.span
                      key={crunchingIdx}
                      initial={{ opacity: 0, x: 3 }}
                      animate={{ opacity: 1, x: 0 }}
                      className="text-xs font-medium text-slate-600 font-mono tracking-tight"
                    >
                      {CRUNCHING_MESSAGES[crunchingIdx]}
                    </motion.span>
                  </motion.div>
                )}

                <div ref={messagesEndRef} />
              </motion.div>
            )}
          </AnimatePresence>
        </motion.div>
      </div>

      {/* ================= INPUT BAR & NEW CHAT ================= */}
      <div className="w-full pt-2 px-2 sm:px-3 space-y-2">
        {messages.length > 0 && (
          <div className="flex items-center justify-end px-1">
            <button
              type="button"
              onClick={startNewChat}
              title="Start a fresh conversation"
              className="inline-flex items-center gap-1 text-[11px] font-medium text-slate-400 hover:text-slate-700 transition-colors shrink-0"
            >
              <IconRefresh size={11} />
              <span>New Chat</span>
            </button>
          </div>
        )}

        {/* Clean Input Box */}
        <div className="rounded-2xl border border-slate-200 bg-white p-3 shadow-sm hover:border-slate-300 focus-within:border-slate-400 focus-within:shadow-md transition-all">
          <textarea
            ref={textareaRef}
            rows={1}
            value={inputPrompt}
            onChange={(e) => setInputPrompt(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void handleSendMessage(inputPrompt);
              }
            }}
            placeholder="Ask about match rates, variance breakdowns, or dispute evidence..."
            className="w-full resize-none text-xs sm:text-sm font-medium placeholder:text-slate-400 focus:outline-none bg-transparent px-1.5 pt-0.5 pb-1.5 text-slate-900 max-h-32"
          />

          <div className="flex items-center justify-between pt-2 border-t border-slate-100">
            <div className="flex items-center gap-1.5">
              <button
                type="button"
                onClick={onOpenConnectModal}
                title="Connect datasets"
                className="flex h-6 w-6 items-center justify-center rounded-full text-slate-500 hover:bg-slate-100 transition-colors"
              >
                <IconPlus size={14} />
              </button>
              <button
                type="button"
                onClick={onOpenConnectModal}
                className="inline-flex items-center gap-1 rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-[11px] font-semibold text-slate-600 hover:bg-slate-100 transition-colors"
              >
                <IconPlug size={11} />
                Connect datasets
              </button>
            </div>

            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => {
                  window.dispatchEvent(
                    new CustomEvent("argus-voice-mic-toggle", {
                      detail: { greet: true, action: "toggle" },
                    }),
                  );
                }}
                title="Voice Copilot"
                className={`flex h-7 w-7 items-center justify-center rounded-full border transition-all ${
                  voiceState.status !== "idle"
                    ? "border-rose-300 bg-rose-50 text-rose-600 animate-pulse"
                    : "border-slate-200 bg-slate-50 text-slate-600 hover:bg-slate-100 hover:text-slate-900"
                }`}
              >
                <svg
                  width="13"
                  height="13"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
                  <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                  <line x1="12" x2="12" y1="19" y2="22" />
                  <line x1="8" y1="23" x2="16" y2="23" />
                </svg>
              </button>

              <button
                type="button"
                disabled={!inputPrompt.trim() || loading}
                onClick={() => void handleSendMessage(inputPrompt)}
                className={`flex h-7 w-7 items-center justify-center rounded-full text-white transition-all shadow-xs ${
                  inputPrompt.trim() && !loading
                    ? "bg-slate-900 hover:bg-slate-800 hover:scale-105 active:scale-95 cursor-pointer"
                    : "bg-slate-200 text-slate-400 cursor-not-allowed"
                }`}
              >
                <IconArrowUp size={14} className={inputPrompt.trim() && !loading ? "text-white" : "text-slate-400"} />
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default HomeChat;
