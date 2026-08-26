"use client";

import React, { useEffect, useRef, useState } from "react";
import { motion } from "motion/react";
import { FormattedMarkdown } from "@/components/ui/formatted-markdown";
import {
  IconArrowUp,
  IconCheck,
  IconCopy,
  IconPlug,
  IconPlus,
  IconRefresh,
} from "@/components/icons";

export interface ChatMessageItem {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: string;
}

const STORAGE_KEY = "argus_copilot_chat_history_v1";

const CRUNCHING_MESSAGES = [
  "Analyzing live SQLite ledger...",
  "Evaluating reconciliation records & variances...",
  "Formulating verified response...",
];

const SUGGESTED_QUERIES = [
  "What is the current deterministic match rate?",
  "How many cases are unresolved and why?",
  "Show the total financial variance breakdown",
  "Explain the duplicate ledger posting exceptions",
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
  const [messages, setMessages] = useState<ChatMessageItem[]>([]);
  const [inputPrompt, setInputPrompt] = useState("");
  const [loading, setLoading] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [crunchingIdx, setCrunchingIdx] = useState(0);
  const [hydrated, setHydrated] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // 1. Persistent Memory: Load from localStorage on mount
  useEffect(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved) {
        const parsed = JSON.parse(saved) as ChatMessageItem[];
        if (Array.isArray(parsed) && parsed.length > 0) {
          setMessages(parsed);
        }
      }
    } catch {
      /* ignore storage read error */
    } finally {
      setHydrated(true);
    }
  }, []);

  // 2. Persistent Memory: Save to localStorage whenever messages update
  useEffect(() => {
    if (!hydrated) return;
    try {
      if (messages.length > 0) {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(messages));
      } else {
        localStorage.removeItem(STORAGE_KEY);
      }
    } catch {
      /* ignore storage write error */
    }
  }, [messages, hydrated]);

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

  const handleSendMessage = async (rawText: string) => {
    const text = rawText.trim();
    if (!text || loading) return;

    const userMsg: ChatMessageItem = {
      id: `user-${Date.now()}`,
      role: "user",
      content: text,
      timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
    };

    const updatedMessages = [...messages, userMsg];
    setMessages(updatedMessages);
    setInputPrompt("");
    setLoading(true);
    setCrunchingIdx(0);

    try {
      // Send full conversation history so Groq maintains complete contextual memory
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

      setMessages((prev) => [...prev, assistantMsg]);
    } catch (err) {
      const errorMsg: ChatMessageItem = {
        id: `assistant-error-${Date.now()}`,
        role: "assistant",
        content: `An error occurred: ${err instanceof Error ? err.message : String(err)}. Please try again.`,
        timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
      };
      setMessages((prev) => [...prev, errorMsg]);
    } finally {
      setLoading(false);
    }
  };

  const handleCopy = (id: string, text: string) => {
    void navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const clearChat = () => {
    setMessages([]);
    setInputPrompt("");
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      /* ignore */
    }
  };

  return (
    <div className="flex flex-col w-full max-w-3xl mx-auto min-h-[620px] h-full justify-between pb-4">
      {/* ================= CHAT STREAM / FEED ================= */}
      <div className="flex-1 overflow-y-auto px-2 sm:px-3 py-2 space-y-5">
        {messages.length === 0 ? (
          /* Clean, Minimal Hero State */
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
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

            {/* Clean Minimal Suggestions */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 w-full max-w-lg pt-3">
              {SUGGESTED_QUERIES.map((query, idx) => (
                <button
                  key={idx}
                  type="button"
                  onClick={() => void handleSendMessage(query)}
                  className="rounded-xl border border-slate-200 bg-white p-3 text-left text-xs font-medium text-slate-700 shadow-2xs hover:border-slate-300 hover:bg-slate-50 transition-all"
                >
                  {query}
                </button>
              ))}
            </div>
          </motion.div>
        ) : (
          /* Active Clean Conversation Thread */
          <div className="space-y-4 pt-1">
            {messages.map((msg) => (
              <motion.div
                key={msg.id}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.15 }}
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
          </div>
        )}
      </div>

      {/* ================= INPUT BAR & NEW CHAT ================= */}
      <div className="w-full pt-2 px-2 sm:px-3 space-y-2">
        {messages.length > 0 && (
          <div className="flex items-center justify-between px-1">
            <div className="flex items-center gap-1.5 overflow-x-auto pb-0.5 scrollbar-none">
              {SUGGESTED_QUERIES.slice(0, 2).map((query, idx) => (
                <button
                  key={idx}
                  type="button"
                  onClick={() => void handleSendMessage(query)}
                  className="whitespace-nowrap rounded-full border border-slate-200 bg-white px-2.5 py-0.5 text-[11px] font-medium text-slate-600 shadow-2xs hover:bg-slate-50 transition-colors"
                >
                  {query}
                </button>
              ))}
            </div>
            <button
              type="button"
              onClick={clearChat}
              title="Reset conversation and clear memory"
              className="inline-flex items-center gap-1 text-[11px] font-medium text-slate-400 hover:text-slate-700 transition-colors shrink-0 ml-2"
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
              {/* Realtime Voice Mode Trigger */}
              <button
                type="button"
                onClick={() => {
                  window.dispatchEvent(
                    new CustomEvent("argus-voice-mic-toggle", { detail: { greet: true } })
                  );
                }}
                title="Start Realtime Voice Conversation"
                className="flex h-7 w-7 items-center justify-center rounded-full bg-slate-100 text-slate-700 hover:bg-slate-200 hover:text-slate-900 transition-all active:scale-95 shadow-2xs"
              >
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
                  <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                  <line x1="12" x2="12" y1="19" y2="22" />
                  <line x1="8" y1="23" x2="16" y2="23" />
                </svg>
              </button>

              {/* Send Button */}
              <button
                type="button"
                disabled={!inputPrompt.trim() || loading}
                onClick={() => void handleSendMessage(inputPrompt)}
                title="Send message (Enter)"
                className={`flex h-7 w-7 items-center justify-center rounded-full transition-all active:scale-95 ${
                  inputPrompt.trim() && !loading
                    ? "bg-slate-900 text-white hover:bg-slate-800"
                    : "bg-slate-100 text-slate-300 cursor-not-allowed"
                }`}
              >
                <IconArrowUp size={14} />
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
