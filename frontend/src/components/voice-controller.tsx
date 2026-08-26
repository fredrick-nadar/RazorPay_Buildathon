"use client";

/**
 * ARGUS Voice Copilot (PRD §13.5).
 *
 * Google Gemini-style floating bottom pill with fluid iridescent wave animations,
 * prominent 6-line brand icon, instant typed fallback, preset prompt chips,
 * and robust Sarvam AI / ElevenLabs cloud STT + TTS integration.
 *
 * Safety Model: Observational & navigation copilot ONLY. Cannot approve, apply,
 * or mutate records. Forbidden actions are strictly refused and audited.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { AnimatePresence, motion } from "motion/react";
import { VoiceOrbModal } from "./voice-orb-modal";

type VoiceStatus =
  | "idle"
  | "listening"
  | "parsing"
  | "speaking"
  | "confirm"
  | "result"
  | "refused"
  | "error";

interface VoiceEntity {
  case_id: string | null;
  amount_paise: number | null;
  status: string | null;
  category: string | null;
}

interface VoiceParse {
  token: string;
  transcript: string;
  language: string;
  status: string;
  intent: string | null;
  forbidden_intent: string | null;
  entities: VoiceEntity;
  requires_confirmation: boolean;
  message: string;
  message_key: string;
  audio_base64?: string | null;
  content_type?: string | null;
}

interface VoiceCaseCard {
  case_id: string;
  category: string;
  status: string;
  variance_paise: number;
  currency: string;
  summary: string;
}

interface VoicePreview {
  case_id: string;
  correction_id: string;
  proposed_delta_paise: number;
  variance_before_paise: number;
  variance_after_paise: number;
  status: string;
}

interface VoiceExecution {
  status: string;
  intent: string | null;
  message: string;
  message_key: string;
  language: string;
  cases: VoiceCaseCard[];
  previews: VoicePreview[];
  briefing: string | null;
  navigation: { type: string; route?: string; case_id?: string; status?: string; run_id?: string } | null;
  audio_base64?: string | null;
  content_type?: string | null;
}

interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
}

interface SpeechRecognitionResultLike {
  isFinal: boolean;
  length: number;
  [index: number]: { transcript: string };
}

interface SpeechRecognitionEventLike {
  results: { length: number; [index: number]: SpeechRecognitionResultLike };
}

type SpeechRecognitionCtor = new () => SpeechRecognitionLike;

function getRecognitionCtor(): SpeechRecognitionCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as {
    SpeechRecognition?: SpeechRecognitionCtor;
    webkitSpeechRecognition?: SpeechRecognitionCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

/**
 * Stop all currently playing TTS audio (both HTML5 Audio elements and
 * Web Speech API synthesis). Called on mic-start / interrupt.
 */
function stopAllAudio(audioRef: React.MutableRefObject<HTMLAudioElement | null>) {
  // Stop tracked HTML5 Audio element
  if (audioRef.current) {
    try {
      audioRef.current.pause();
      audioRef.current.currentTime = 0;
      audioRef.current.src = "";
    } catch { /* ignore */ }
    audioRef.current = null;
  }
  // Cancel browser speech synthesis
  if (typeof window !== "undefined" && "speechSynthesis" in window) {
    try { window.speechSynthesis.cancel(); } catch { /* ignore */ }
  }
}

function pickNaturalVoice(lang: string): SpeechSynthesisVoice | null {
  if (typeof window === "undefined" || !("speechSynthesis" in window)) return null;
  const voices = window.speechSynthesis.getVoices();
  const matching = voices.filter((v) => v.lang.replace("_", "-").toLowerCase().startsWith(lang.slice(0, 2).toLowerCase()));
  const pool = matching.length > 0 ? matching : voices;
  return (
    pool.find((v) => /natural|neural|premium|enhanced/i.test(v.name)) ??
    pool.find((v) => /google/i.test(v.name)) ??
    pool[0] ??
    null
  );
}

/**
 * Play cloud-generated audio or fall back to browser speech synthesis.
 * All playback is tracked via audioRef so it can be interrupted.
 */
function playAudioOrSpeak(
  text: string,
  lang: string,
  audioRef: React.MutableRefObject<HTMLAudioElement | null>,
  audioBase64?: string | null,
  contentType: string = "audio/wav",
  onEnded?: () => void,
) {
  if (typeof window === "undefined") return;

  // Always stop any currently playing audio first
  stopAllAudio(audioRef);

  // 1. If Sarvam or ElevenLabs generated natural voice audio, play it
  if (audioBase64) {
    try {
      const audio = new Audio(`data:${contentType};base64,${audioBase64}`);
      audioRef.current = audio;
      audio.onended = () => {
        if (audioRef.current === audio) audioRef.current = null;
        if (onEnded) onEnded();
      };
      audio.onerror = () => {
        if (audioRef.current === audio) audioRef.current = null;
      };
      audio.play().catch(() => {
        // autoplay blocked → fall through to browser speech
        audioRef.current = null;
        playBrowserSpeech(text, lang, audioRef, onEnded);
      });
      return;
    } catch {
      /* fallback to browser speech synthesis */
    }
  }

  // 2. Web Speech API synthesis fallback
  playBrowserSpeech(text, lang, audioRef, onEnded);
}

function playBrowserSpeech(
  text: string,
  lang: string,
  audioRef: React.MutableRefObject<HTMLAudioElement | null>,
  onEnded?: () => void,
) {
  if (typeof window === "undefined" || !("speechSynthesis" in window)) return;
  // Ensure any HTML5 Audio playback is stopped before browser synthesis
  stopAllAudio(audioRef);
  try {
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = lang;
    utterance.rate = 1.02;
    const voice = pickNaturalVoice(lang);
    if (voice) utterance.voice = voice;
    if (onEnded) {
      utterance.onend = () => onEnded();
    }
    window.speechSynthesis.speak(utterance);
  } catch {
    /* ignore */
  }
}

export function VoiceController() {
  const router = useRouter();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [language, setLanguage] = useState("en-IN");
  const [status, setStatus] = useState<VoiceStatus>("idle");
  const [transcript, setTranscript] = useState("");
  const [typed, setTyped] = useState("");
  const [parse, setParse] = useState<VoiceParse | null>(null);
  const [execution, setExecution] = useState<VoiceExecution | null>(null);
  const [, setError] = useState<string | null>(null);
  const [, setLatencyMs] = useState<number | null>(null);
  const [capabilities, setCapabilities] = useState<{ stt: string; tts: string }>({ stt: "unavailable", tts: "unavailable" });
  const [muted, setMuted] = useState(false);
  const [realtimeMode] = useState(true);

  const ttsAudioRef = useRef<HTMLAudioElement | null>(null);

  const speak = useCallback(
    (text: string) => {
      if (muted || !text) return;
      if (capabilities.tts === "sarvam") {
        void (async () => {
          try {
            const res = await fetch("/api/v1/voice/tts", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ text, language }),
            });
            if (res.ok) {
              const data = (await res.json()) as { success: boolean; audio_base64?: string };
              if (data.success && data.audio_base64) {
                playAudioOrSpeak(text, language, ttsAudioRef, data.audio_base64, "audio/wav");
                return;
              }
            }
          } catch {
            /* network hiccup -> browser voice */
          }
          playBrowserSpeech(text, language, ttsAudioRef);
        })();
        return;
      }
      playBrowserSpeech(text, language, ttsAudioRef);
    },
    [capabilities.tts, language, muted],
  );

  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const browserFinalRef = useRef<string>("");
  const inputRef = useRef<HTMLInputElement>(null);
  const [dashboardTab, setDashboardTab] = useState<string>("home");

  useEffect(() => {
    void (async () => {
      try {
        const res = await fetch("/api/v1/voice/capabilities");
        if (res.ok) setCapabilities((await res.json()) as { stt: string; tts: string });
      } catch {
        /* capabilities probe is best-effort */
      }
    })();
    function onKey(event: KeyboardEvent) {
      if (event.ctrlKey && event.key.toLowerCase() === "k") {
        event.preventDefault();
        inputRef.current?.focus();
        setOpen(true);
      }
      if (event.ctrlKey && event.shiftKey && event.key.toLowerCase() === "v") {
        event.preventDefault();
        setOpen((value) => !value);
      }
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const silenceTimerRef = useRef<NodeJS.Timeout | null>(null);

  const stopListening = useCallback(() => {
    // Clear any pending silence timer
    if (silenceTimerRef.current) {
      clearTimeout(silenceTimerRef.current);
      silenceTimerRef.current = null;
    }

    // Stop any playing TTS audio immediately (the core interruption fix)
    stopAllAudio(ttsAudioRef);

    try {
      recognitionRef.current?.stop();
    } catch {
      /* ignore */
    }
    recognitionRef.current = null;

    if (mediaRecorderRef.current && mediaRecorderRef.current.state === "recording") {
      try {
        mediaRecorderRef.current.stop();
      } catch {
        /* ignore */
      }
    }
  }, []);

  const inFlightRef = useRef(false);
  const realtimeModeRef = useRef(realtimeMode);
  realtimeModeRef.current = realtimeMode;
  const startListeningRef = useRef<(() => Promise<void>) | null>(null);

  const runParseAndExecute = useCallback(
    async (text: string, confirmed = false) => {
      const trimmed = text.trim();
      if (!trimmed) return;
      if (inFlightRef.current) return;
      inFlightRef.current = true;

      setOpen(true);
      setStatus("parsing");
      setError(null);
      setExecution(null);
      const startedAt = performance.now();
      try {
        // Atomic single-round-trip path: parse + guard + execute together.
        const commandRes = await fetch("/api/v1/voice/command", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            transcript: trimmed,
            language,
            confirmed,
            page_context: { tab: dashboardTab, pathname },
          }),
        });
        if (!commandRes.ok) throw new Error(`Command failed (${commandRes.status})`);
        const commandData = (await commandRes.json()) as VoiceParse & {
          execution: VoiceExecution | null;
        };
        setLatencyMs(Math.round(performance.now() - startedAt));

        // Auto-switch UI language if Gemini / model detected language switch request
        if (commandData.language && commandData.language !== language) {
          setLanguage(commandData.language);
        }

        const onVoicePlaybackEnded = () => {
          if (realtimeModeRef.current && startListeningRef.current) {
            setTimeout(() => {
              void startListeningRef.current?.();
            }, 450);
          }
        };

        if (commandData.execution) {
          const result = commandData.execution;
          const targetLang = commandData.language || language;
          const syntheticParse: VoiceParse = {
            token: "",
            transcript: commandData.transcript,
            language: targetLang,
            status: commandData.status,
            intent: commandData.intent,
            forbidden_intent: commandData.forbidden_intent,
            entities: commandData.entities,
            requires_confirmation: false,
            message: commandData.message,
            message_key: commandData.message_key,
          };
          setParse(syntheticParse);
          setExecution(result);
          if (result.status === "REFUSED") {
            setStatus("refused");
            playAudioOrSpeak(
              result.message,
              targetLang,
              ttsAudioRef,
              result.audio_base64,
              result.content_type || "audio/wav",
              onVoicePlaybackEnded,
            );
            return;
          }
          setStatus("result");
          // Play the audio generated by the backend directly (avoids duplicate TTS request)
          if (result.audio_base64) {
            playAudioOrSpeak(
              result.message,
              targetLang,
              ttsAudioRef,
              result.audio_base64,
              result.content_type || "audio/wav",
              onVoicePlaybackEnded,
            );
          } else {
            speak(result.message);
          }
          const nav = result.navigation;
          if (nav?.type === "navigate" && nav.route) {
            setTimeout(() => router.push(nav.route as string), 900);
          } else if (nav?.type === "refresh_runs") {
            setTimeout(() => router.refresh(), 600);
            window.dispatchEvent(new CustomEvent("argus:voice-runs-refreshed", { detail: nav.run_id }));
          } else if (nav?.type === "select_case" && nav.case_id) {
            window.dispatchEvent(new CustomEvent("argus:voice-select-case", { detail: nav.case_id }));
          } else if (nav?.type === "filter_cases") {
            window.dispatchEvent(new CustomEvent("argus:voice-filter-cases", { detail: nav }));
          }
          return;
        }

        // Non-executing outcomes surface through the parse shape.
        const parsed = commandData as VoiceParse;
        const targetLang = parsed.language || language;
        setParse(parsed);
        if (parsed.status === "REFUSED") {
          setStatus("refused");
          playAudioOrSpeak(
            parsed.message,
            targetLang,
            ttsAudioRef,
            parsed.audio_base64,
            parsed.content_type || "audio/wav",
            onVoicePlaybackEnded,
          );
          return;
        }
        if (parsed.status === "NOT_UNDERSTOOD") {
          setStatus("error");
          setError(parsed.message);
          return;
        }
        if (parsed.message_key === "conversational_answer") {
          setStatus("result");
          setExecution({
            status: "OK",
            intent: "EXPLAIN_CASE",
            message: parsed.message,
            message_key: "conversational_answer",
            language: targetLang,
            cases: [],
            previews: [],
            briefing: null,
            navigation: null,
            audio_base64: parsed.audio_base64,
            content_type: parsed.content_type,
          });
          playAudioOrSpeak(
            parsed.message,
            targetLang,
            ttsAudioRef,
            parsed.audio_base64,
            parsed.content_type || "audio/wav",
            onVoicePlaybackEnded,
          );
          return;
        }
        if (parsed.requires_confirmation) {
          setStatus("confirm");
          return;
        }
      } catch (cause) {
        setStatus("error");
        setError(cause instanceof Error ? cause.message : String(cause));
      } finally {
        inFlightRef.current = false;
      }
    },
    [language, router, speak, dashboardTab, pathname],
  );

  const startListening = useCallback(async () => {
    // Stop any playing TTS audio when mic is activated (interruption)
    stopAllAudio(ttsAudioRef);

    if (silenceTimerRef.current) {
      clearTimeout(silenceTimerRef.current);
      silenceTimerRef.current = null;
    }

    setError(null);
    setParse(null);
    setExecution(null);
    setTranscript("");
    setStatus("listening");
    setOpen(true);

    audioChunksRef.current = [];
    browserFinalRef.current = "";

    const useServerSTT = capabilities.stt === "sarvam";
    const startServerRecording = async () => {
      if (typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia) return;
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const mediaRecorder = new MediaRecorder(stream);
        mediaRecorderRef.current = mediaRecorder;
        mediaRecorder.ondataavailable = (event) => {
          if (event.data.size > 0) audioChunksRef.current.push(event.data);
        };
        mediaRecorder.onstop = async () => {
          stream.getTracks().forEach((t) => t.stop());
          const audioBlob = new Blob(audioChunksRef.current, { type: "audio/webm" });
          if (audioBlob.size <= 1000) {
            if (browserFinalRef.current) {
              void runParseAndExecute(browserFinalRef.current);
            } else {
              setStatus("idle");
            }
            return;
          }
          const reader = new FileReader();
          reader.onloadend = async () => {
            try {
              const res = await fetch("/api/v1/voice/transcribe", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  audio_base64: reader.result as string,
                  language,
                  content_type: "audio/webm",
                }),
              });
              if (!res.ok) {
                const detail = (await res.json().catch(() => null)) as { reason?: string } | null;
                if (browserFinalRef.current) {
                  void runParseAndExecute(browserFinalRef.current);
                  return;
                }
                setStatus("error");
                setError(detail?.reason ?? "Server transcription unavailable. Use the typed command bar.");
                return;
              }
              const data = (await res.json()) as { success: boolean; transcript: string };
              if (data.success && data.transcript) {
                setTranscript(data.transcript);
                void runParseAndExecute(data.transcript);
              } else if (browserFinalRef.current) {
                void runParseAndExecute(browserFinalRef.current);
              }
            } catch {
              if (browserFinalRef.current) void runParseAndExecute(browserFinalRef.current);
              else {
                setStatus("error");
                setError("Transcription request failed. Use the typed command bar.");
              }
            }
          };
          reader.readAsDataURL(audioBlob);
        };
        mediaRecorder.start();
      } catch {
        // Recorder failed - browser STT will handle it
      }
    };

    if (useServerSTT) {
      void startServerRecording();
    }

    const Ctor = getRecognitionCtor();
    if (!Ctor) {
      if (!useServerSTT) {
        setStatus("error");
        setError("Speech recognition is not available in this browser. Type your command below.");
      }
      return;
    }
    try {
      const recognition = new Ctor();
      recognition.lang = language;
      recognition.continuous = true;
      recognition.interimResults = true;
      recognition.maxAlternatives = 1;
      recognition.onresult = (event) => {
        let finalText = "";
        let interim = "";
        for (let i = 0; i < event.results.length; i += 1) {
          const result = event.results[i];
          if (!result) continue;
          const text = result[0]?.transcript ?? "";
          if (result.isFinal) finalText += text;
          else interim += text;
        }

        const currentText = finalText || interim;
        setTranscript(currentText);
        if (finalText) browserFinalRef.current = finalText;

        // Auto-pause detection: if user speaks and pauses for 1.3s, auto-close mic and send to Gemini!
        if (currentText.trim().length > 0) {
          if (silenceTimerRef.current) clearTimeout(silenceTimerRef.current);
          silenceTimerRef.current = setTimeout(() => {
            stopListening();
          }, 1300);
        }
      };
      recognition.onerror = (event) => {
        if (event.error === "network") {
          if (!useServerSTT) {
            setStatus("idle");
            setError("Browser speech service unreachable. Type below or click a quick prompt.");
            inputRef.current?.focus();
          }
        } else if (event.error === "not-allowed" || event.error === "service-not-allowed") {
          setStatus("error");
          setError("Microphone permission was denied. Allow microphone access or type below.");
        } else if (event.error === "no-speech") {
          if (!useServerSTT) setStatus("idle");
        }
      };
      recognition.onend = () => {
        if (!useServerSTT) setStatus((current) => (current === "listening" ? "idle" : current));
      };
      recognitionRef.current = recognition;
      recognition.start();
    } catch {
      if (!useServerSTT) void startServerRecording();
    }
  }, [capabilities.stt, language, runParseAndExecute, stopListening]);

  startListeningRef.current = startListening;

  const submitTyped = useCallback(() => {
    const text = typed;
    setTyped("");
    void runParseAndExecute(text);
  }, [typed, runParseAndExecute]);

  const startWithGreeting = useCallback(
    async (targetLang = language) => {
      // Clear any pending silence timer
      if (silenceTimerRef.current) {
        clearTimeout(silenceTimerRef.current);
        silenceTimerRef.current = null;
      }
      setOpen(true);
      setError(null);
      setParse(null);
      setExecution(null);
      setTranscript("");
      setStatus("speaking");

      const greetingText = targetLang.startsWith("hi")
        ? "नमस्ते, मैं आर्गस हूँ। आज आपके लेजर में कैसे मदद कर सकता हूँ?"
        : "Hello, I'm ARGUS. How can I assist with your reconciliation ledger today?";

      const onGreetingEnded = () => {
        if (realtimeModeRef.current && startListeningRef.current) {
          setTimeout(() => {
            void startListeningRef.current?.();
          }, 350);
        }
      };

      try {
        const res = await fetch("/api/v1/voice/tts", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: greetingText, language: targetLang }),
        });
        if (res.ok) {
          const data = (await res.json()) as {
            success: boolean;
            audio_base64?: string;
            content_type?: string;
          };
          if (data.success && data.audio_base64) {
            playAudioOrSpeak(
              greetingText,
              targetLang,
              ttsAudioRef,
              data.audio_base64,
              data.content_type || "audio/wav",
              onGreetingEnded,
            );
            return;
          }
        }
      } catch {
        /* fallback to browser speech */
      }

      playBrowserSpeech(greetingText, targetLang, ttsAudioRef, onGreetingEnded);
    },
    [language],
  );

  useEffect(() => {
    function onTabChange(event: Event) {
      const custom = event as CustomEvent<{ tab: string }>;
      if (custom.detail?.tab) {
        setDashboardTab(custom.detail.tab);
      }
    }
    function onVoiceCommand(event: Event) {
      const custom = event as CustomEvent<{ text: string }>;
      if (custom.detail?.text) {
        void runParseAndExecute(custom.detail.text);
      }
    }
    function onVoiceMicToggle(event: Event) {
      if (status === "listening" || status === "speaking") {
        stopAllAudio(ttsAudioRef);
        stopListening();
        setOpen(false);
      } else {
        const custom = event as CustomEvent<{ greet?: boolean }>;
        if (custom.detail?.greet !== false) {
          void startWithGreeting();
        } else {
          setOpen(true);
          void startListening();
        }
      }
    }

    window.addEventListener("argus-dashboard-tab", onTabChange);
    window.addEventListener("argus-voice-command", onVoiceCommand);
    window.addEventListener("argus-voice-mic-toggle", onVoiceMicToggle);

    return () => {
      window.removeEventListener("argus-dashboard-tab", onTabChange);
      window.removeEventListener("argus-voice-command", onVoiceCommand);
      window.removeEventListener("argus-voice-mic-toggle", onVoiceMicToggle);
    };
  }, [runParseAndExecute, startListening, startWithGreeting, stopListening, status]);

  if (pathname === "/") {
    return null;
  }

  const isOuterPage = pathname !== "/dashboard" || dashboardTab !== "home" || open;

  return (
    <>
      {/* Full-Screen Realtime ChatGPT Voice Orb Experience */}
      <VoiceOrbModal
        isOpen={open}
        onClose={() => setOpen(false)}
        status={status}
        transcript={transcript}
        assistantMessage={execution?.message || parse?.message || null}
        language={language}
        onLanguageChange={setLanguage}
        muted={muted}
        onToggleMute={() => setMuted((m) => !m)}
        onMicToggle={status === "listening" ? stopListening : startListening}
        onSubmitTyped={(text) => void runParseAndExecute(text)}
      />

      <div className="pointer-events-none fixed bottom-6 left-1/2 -translate-x-1/2 z-[100] flex flex-col items-center gap-3 w-full max-w-2xl px-4">
        {/* ================= Center Floating Pill Launcher ================= */}
        <AnimatePresence>
          {isOuterPage && !open && (
            <motion.div
              key="argus-floating-pill"
              initial={{
                opacity: 0,
                y: 35,
                scaleX: 0.85,
                scaleY: 0.7,
                filter: "blur(10px)",
                borderRadius: "40px",
              }}
              animate={{
                opacity: 1,
                y: 0,
                scaleX: 1,
                scaleY: 1,
                filter: "blur(0px)",
                borderRadius: "9999px",
              }}
              exit={{
                opacity: 0,
                y: 32,
                scaleX: 0.82,
                scaleY: 0.65,
                filter: "blur(10px)",
                borderRadius: "40px",
              }}
              transition={{
                type: "spring",
                stiffness: 380,
                damping: 24,
                mass: 0.6,
              }}
              className="pointer-events-auto relative w-full group"
            >
              {/* Pill Body */}
              <div className="relative flex items-center justify-between gap-2.5 rounded-full border border-slate-200 bg-white/95 px-3 py-2 shadow-xl backdrop-blur-md transition-all duration-200 hover:border-slate-300">
                {/* Brand Icon (6 Slanted Lines) */}
                <button
                  onClick={() => setOpen((prev) => !prev)}
                  aria-label="Toggle ARGUS Voice Copilot"
                  suppressHydrationWarning
                  className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-slate-200 bg-slate-50 text-slate-900 transition-all hover:bg-slate-100 hover:scale-105"
                >
                  <svg viewBox="0 0 42 34" className="w-5 h-4 text-slate-900" fill="currentColor" aria-hidden>
                    <polygon points="12,0 30,0 33.2,3.2 15.2,3.2" />
                    <polygon points="14.6,5.6 32.6,5.6 35.8,8.8 17.8,8.8" />
                    <polygon points="17.2,11.2 35.2,11.2 38.4,14.4 20.4,14.4" />
                    <polygon points="3.2,16.8 21.2,16.8 24.4,20 6.4,20" />
                    <polygon points="5.8,22.4 23.8,22.4 27,25.6 9,25.6" />
                    <polygon points="8.4,28 26.4,28 29.6,31.2 11.6,31.2" />
                  </svg>
                </button>

                {/* Integrated Natural Language Input */}
                <div className="flex flex-1 items-center min-w-0">
                  <input
                    ref={inputRef}
                    value={typed}
                    onChange={(event) => setTyped(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") submitTyped();
                    }}
                    onFocus={() => setOpen(true)}
                    placeholder="Ask or command ARGUS... (or tap mic)"
                    aria-label="Voice or typed command"
                    suppressHydrationWarning
                    className="w-full bg-transparent px-2 text-xs font-medium text-slate-900 placeholder-slate-400 focus:outline-none"
                  />
                </div>

                {/* Action Buttons */}
                <div className="flex items-center gap-1.5 shrink-0">
                  {/* Push-to-Talk Mic Button */}
                  <button
                    onClick={() => {
                      void startWithGreeting();
                    }}
                    aria-label="Start Voice Copilot"
                    suppressHydrationWarning
                    className="flex h-9 w-9 items-center justify-center rounded-full border border-slate-200 bg-slate-100 text-slate-700 hover:border-slate-300 hover:bg-slate-200 hover:text-slate-900 hover:scale-105 transition-all shadow-xs"
                  >
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
                      <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                      <line x1="12" x2="12" y1="19" y2="22" />
                      <line x1="8" y1="23" x2="16" y2="23" />
                    </svg>
                  </button>
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </>
  );
}
