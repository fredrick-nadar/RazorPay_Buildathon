"use client";

import React, { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";

interface VoiceOrbModalProps {
  isOpen: boolean;
  onClose: () => void;
  status: "idle" | "listening" | "parsing" | "speaking" | "confirm" | "error" | "result" | "refused";
  transcript: string;
  assistantMessage: string | null;
  language: string;
  onLanguageChange: (lang: string) => void;
  muted: boolean;
  onToggleMute: () => void;
  onMicToggle: () => void;
  onSubmitTyped: (text: string) => void;
}

export function VoiceOrbModal({
  isOpen,
  onClose,
  status,
  transcript,
  assistantMessage,
  muted,
  onToggleMute,
  onMicToggle,
  onSubmitTyped,
}: VoiceOrbModalProps) {
  const [typedText, setTypedText] = useState("");

  // Close on Escape
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && isOpen) {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  const handleInputKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && typedText.trim()) {
      e.preventDefault();
      onSubmitTyped(typedText.trim());
      setTypedText("");
    }
  };

  if (!isOpen) return null;

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.25 }}
        className="fixed inset-0 z-[999] flex flex-col items-center justify-between bg-black text-white px-4 py-8 select-none overflow-hidden"
      >
        {/* Top Header: Subtle Status Tag */}
        <div className="flex items-center justify-center w-full pt-2">
          <motion.div
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            className="flex items-center gap-2 rounded-full bg-white/10 px-3.5 py-1 backdrop-blur-md border border-white/10 text-xs font-medium text-slate-300"
          >
            <span
              className={`h-2 w-2 rounded-full ${
                status === "speaking"
                  ? "bg-blue-400 animate-ping"
                  : status === "listening"
                  ? "bg-emerald-400 animate-pulse"
                  : status === "parsing"
                  ? "bg-indigo-400 animate-bounce"
                  : "bg-slate-400"
              }`}
            />
            <span>
              {status === "listening"
                ? "Listening..."
                : status === "parsing"
                ? "Thinking..."
                : status === "speaking"
                ? "ARGUS Speaking"
                : "Realtime Voice Mode"}
            </span>
          </motion.div>
        </div>

        {/* Center: Fluid Celestial Luminous Orb */}
        <div className="flex flex-col items-center justify-center flex-1 w-full max-w-lg my-auto space-y-8">
          <div className="relative flex items-center justify-center">
            {/* Ambient Outer Glow Layer */}
            <motion.div
              animate={{
                scale: status === "speaking" ? [1, 1.25, 1.1, 1.3, 1] : status === "listening" ? [1, 1.15, 1] : [1, 1.05, 1],
                opacity: status === "speaking" ? [0.6, 0.9, 0.7] : [0.4, 0.6, 0.4],
              }}
              transition={{
                duration: status === "speaking" ? 2 : 4,
                repeat: Infinity,
                ease: "easeInOut",
              }}
              className="absolute h-56 w-56 sm:h-64 sm:w-64 rounded-full bg-gradient-to-tr from-blue-600/50 via-indigo-500/40 to-sky-300/30 blur-3xl"
            />

            {/* The Fluid Morphing Orb (Matching Screenshot) */}
            <motion.div
              animate={{
                scale: status === "speaking" ? [1, 1.08, 0.96, 1.04, 1] : status === "listening" ? [1, 1.05, 0.98, 1] : [1, 1.02, 1],
                rotate: status === "parsing" ? 360 : 0,
              }}
              transition={{
                scale: {
                  duration: status === "speaking" ? 1.8 : 3,
                  repeat: Infinity,
                  ease: "easeInOut",
                },
                rotate: {
                  duration: 8,
                  repeat: Infinity,
                  ease: "linear",
                },
              }}
              className="relative h-44 w-44 sm:h-52 sm:w-52 rounded-full overflow-hidden shadow-[0_0_60px_rgba(96,165,250,0.5)] border border-white/20"
            >
              {/* Deep Blue Base */}
              <div className="absolute inset-0 bg-[#3b5bfd]" />

              {/* Radiant Sky Blue to Cobalt Top Gradient */}
              <div
                className="absolute inset-0"
                style={{
                  background: "radial-gradient(circle at 50% 20%, #70a6ff 0%, #3b66ff 45%, #2548e6 80%)",
                }}
              />

              {/* Flowing Ethereal White / Cloud Wave (as in the screenshot) */}
              <motion.div
                animate={{
                  y: status === "speaking" ? [-4, 6, -6, 4] : [-2, 3, -2],
                  x: status === "speaking" ? [-6, 6, -4, 4] : [-3, 3, -3],
                  scaleY: status === "speaking" ? [1, 1.15, 0.9, 1] : [1, 1.05, 1],
                }}
                transition={{
                  duration: status === "speaking" ? 2.5 : 5,
                  repeat: Infinity,
                  ease: "easeInOut",
                }}
                className="absolute -bottom-6 -left-10 -right-10 h-32 rounded-full bg-gradient-to-t from-white via-white/95 to-transparent blur-md"
                style={{
                  boxShadow: "0 -10px 40px 10px rgba(255,255,255,0.9)",
                }}
              />

              {/* Secondary Soft Cloud Highlight */}
              <motion.div
                animate={{
                  opacity: [0.7, 0.95, 0.7],
                  scale: [0.95, 1.05, 0.95],
                }}
                transition={{
                  duration: 3,
                  repeat: Infinity,
                  ease: "easeInOut",
                }}
                className="absolute top-1/3 left-1/4 right-1/4 h-16 rounded-full bg-white/40 blur-lg"
              />
            </motion.div>
          </div>

          {/* Real-time Subtitle / Caption Stream */}
          <div className="w-full text-center px-4 min-h-[60px] flex items-center justify-center">
            {transcript ? (
              <motion.p
                key="transcript"
                initial={{ opacity: 0, y: 5 }}
                animate={{ opacity: 1, y: 0 }}
                className="text-base sm:text-lg font-medium text-slate-200 leading-relaxed"
              >
                &ldquo;{transcript}&rdquo;
              </motion.p>
            ) : assistantMessage ? (
              <motion.p
                key="assistantMessage"
                initial={{ opacity: 0, y: 5 }}
                animate={{ opacity: 1, y: 0 }}
                className="text-sm sm:text-base font-normal text-slate-300 leading-relaxed line-clamp-3"
              >
                {assistantMessage}
              </motion.p>
            ) : status === "speaking" ? (
              <motion.p
                key="speakingGreeting"
                initial={{ opacity: 0, y: 5 }}
                animate={{ opacity: 1, y: 0 }}
                className="text-base sm:text-lg font-medium text-slate-200 leading-relaxed"
              >
                &ldquo;Hello, I&apos;m ARGUS. How can I assist with your reconciliation ledger today?&rdquo;
              </motion.p>
            ) : (
              <p className="text-xs sm:text-sm text-slate-500 font-mono tracking-wider uppercase">
                {status === "listening" ? "Listening to your voice..." : "Say anything or ask about the active ledger"}
              </p>
            )}
          </div>
        </div>

        {/* Bottom Bar (Matching Screenshot: Pill Container with + , Type, Mute, Close) */}
        <div className="w-full max-w-md pb-4">
          <div className="flex items-center justify-between rounded-full bg-[#1c1c1e] border border-white/10 px-3 py-2 shadow-2xl backdrop-blur-xl">
            {/* Left '+' button */}
            <button
              type="button"
              title="Add attachment"
              className="flex h-9 w-9 items-center justify-center rounded-full text-slate-400 hover:text-white hover:bg-white/10 transition-colors"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="12" y1="5" x2="12" y2="19" />
                <line x1="5" y1="12" x2="19" y2="12" />
              </svg>
            </button>

            {/* Middle: 'Type' input / text */}
            <div className="flex-1 px-3">
              <input
                type="text"
                value={typedText}
                onChange={(e) => setTypedText(e.target.value)}
                onKeyDown={handleInputKeyDown}
                placeholder="Type"
                className="w-full bg-transparent text-sm text-white placeholder:text-slate-500 focus:outline-none"
              />
            </div>

            <div className="flex items-center gap-2">
              {/* Mic / Mute Toggle Button */}
              <button
                type="button"
                onClick={onToggleMute || onMicToggle}
                title={muted ? "Unmute mic" : "Mute mic"}
                className={`flex h-9 w-9 items-center justify-center rounded-full transition-colors ${
                  muted ? "text-red-400 hover:bg-red-500/10" : "text-slate-400 hover:text-white hover:bg-white/10"
                }`}
              >
                {muted ? (
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <line x1="1" y1="1" x2="23" y2="23" />
                    <path d="M9 9v3a3 3 0 0 0 5.12 2.12M15 9.34V4a3 3 0 0 0-5.94-.6" />
                    <path d="M17 16.95A7 7 0 0 1 5 12v-2m14 0v2a7 7 0 0 1-.11 1.23" />
                    <line x1="12" y1="19" x2="12" y2="23" />
                    <line x1="8" y1="23" x2="16" y2="23" />
                  </svg>
                ) : (
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
                    <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                    <line x1="12" y1="19" x2="12" y2="23" />
                    <line x1="8" y1="23" x2="16" y2="23" />
                  </svg>
                )}
              </button>

              {/* Close Button (White circular with '✕' icon matching screenshot) */}
              <button
                type="button"
                onClick={onClose}
                title="Close Realtime Voice"
                className="flex h-8 w-8 items-center justify-center rounded-full bg-white text-black font-bold hover:bg-slate-200 transition-transform active:scale-95 shadow-md"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="18" y1="6" x2="6" y2="18" />
                  <line x1="6" y1="6" x2="18" y2="18" />
                </svg>
              </button>
            </div>
          </div>
        </div>
      </motion.div>
    </AnimatePresence>
  );
}
