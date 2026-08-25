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
import { Wave } from "@/components/ui/wave";

type VoiceStatus =
  | "idle"
  | "listening"
  | "parsing"
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

const LANGUAGES = [
  { code: "en-IN", label: "English (India)" },
  { code: "hi-IN", label: "हिन्दी / Hinglish" },
  { code: "ta-IN", label: "தமிழ்" },
  { code: "te-IN", label: "తెలుగు" },
  { code: "kn-IN", label: "ಕನ್ನಡ" },
];

const PRESET_PROMPTS = [
  { label: "🔍 Show unresolved", text: "Show unresolved cases" },
  { label: "❓ Why case 1?", text: "Why is case 1 unresolved?" },
  { label: "🛡️ Approve all (Test Refusal)", text: "Approve all corrections" },
  { label: "⚡ Run recon", text: "Run reconciliation for loaded batch" },
  { label: "🖥️ Presentation", text: "Open presentation mode" },
];

function getRecognitionCtor(): SpeechRecognitionCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as {
    SpeechRecognition?: SpeechRecognitionCtor;
    webkitSpeechRecognition?: SpeechRecognitionCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

function playAudioOrSpeak(text: string, lang: string, audioBase64?: string | null, contentType: string = "audio/wav") {
  if (typeof window === "undefined") return;

  // 1. If Sarvam or ElevenLabs generated natural voice audio, play it immediately!
  if (audioBase64) {
    try {
      const audio = new Audio(`data:${contentType};base64,${audioBase64}`);
      audio.play().catch(() => {
        /* browser autoplay policy fallback */
      });
      return;
    } catch {
      /* fallback to browser speech synthesis */
    }
  }

  // 2. Web Speech API synthesis fallback
  if ("speechSynthesis" in window) {
    try {
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = lang;
      utterance.rate = 1.02;
      window.speechSynthesis.cancel();
      window.speechSynthesis.speak(utterance);
    } catch {
      /* ignore */
    }
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

function playBrowserSpeech(text: string, lang: string) {
  if (typeof window === "undefined" || !("speechSynthesis" in window)) return;
  try {
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = lang;
    utterance.rate = 1.02;
    const voice = pickNaturalVoice(lang);
    if (voice) utterance.voice = voice;
    window.speechSynthesis.cancel();
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
  const [error, setError] = useState<string | null>(null);
  const [latencyMs, setLatencyMs] = useState<number | null>(null);
  const [capabilities, setCapabilities] = useState<{ stt: string; tts: string }>({ stt: "unavailable", tts: "unavailable" });
  const [muted, setMuted] = useState(false);

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
                try {
                  const audio = new Audio(`data:audio/wav;base64,${data.audio_base64}`);
                  await audio.play();
                  return;
                } catch {
                  /* autoplay blocked -> browser voice */
                }
              }
            }
          } catch {
            /* network hiccup -> browser voice */
          }
          playBrowserSpeech(text, language);
        })();
        return;
      }
      playBrowserSpeech(text, language);
    },
    [capabilities.tts, language, muted],
  );

  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

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

  const stopListening = useCallback(() => {
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

  const runParseAndExecute = useCallback(
    async (text: string, confirmed = false) => {
      const trimmed = text.trim();
      if (!trimmed) return;
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
          body: JSON.stringify({ transcript: trimmed, language, confirmed }),
        });
        if (!commandRes.ok) throw new Error(`Command failed (${commandRes.status})`);
        const commandData = (await commandRes.json()) as VoiceParse & {
          execution: VoiceExecution | null;
        };
        setLatencyMs(Math.round(performance.now() - startedAt));

        if (commandData.execution) {
          const result = commandData.execution;
          const syntheticParse: VoiceParse = {
            token: "",
            transcript: commandData.transcript,
            language: commandData.language,
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
            playAudioOrSpeak(result.message, language, result.audio_base64, result.content_type || "audio/wav");
            return;
          }
          setStatus("result");
          speak(result.message);
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
        setParse(parsed);
        if (parsed.status === "REFUSED") {
          setStatus("refused");
          speak(parsed.message);
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
            intent: "BRIEF_STATUS",
            message: parsed.message,
            message_key: "conversational_answer",
            language,
            cases: [],
            previews: [],
            briefing: null,
            navigation: null,
            audio_base64: parsed.audio_base64,
            content_type: parsed.content_type,
          });
          speak(parsed.message);
          return;
        }
        if (parsed.requires_confirmation) {
          setStatus("confirm");
          return;
        }

        if (parsed.status === "REFUSED") {
          setStatus("refused");
          speak(parsed.message);
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
            language,
            cases: [],
            previews: [],
            briefing: null,
            navigation: null,
            audio_base64: parsed.audio_base64,
            content_type: parsed.content_type,
          });
          playAudioOrSpeak(
            parsed.message,
            language,
            parsed.audio_base64,
            parsed.content_type || "audio/wav"
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
      }
    },
    [language, router, speak],
  );

  const startListening = useCallback(async () => {
    setError(null);
    setParse(null);
    setExecution(null);
    setTranscript("");
    setStatus("listening");
    setOpen(true);

    audioChunksRef.current = [];

    // SINGLE-ENGINE POLICY: on-device Web Speech recognition is primary
    // (live interim results, zero upload latency). MediaRecorder ->
    // /voice/transcribe is a FALLBACK used only when browser recognition
    // is unavailable. Running both at once executed commands twice.

    const startRecorderFallback = async () => {
      if (typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia) {
        setStatus("error");
        setError("Voice capture is unavailable here. Type your command below.");
        return;
      }
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
          if (audioBlob.size <= 1000) return;
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
                setStatus("error");
                setError(
                  detail?.reason ??
                    "Server transcription is unavailable. Use the typed command bar.",
                );
                return;
              }
              const data = (await res.json()) as { success: boolean; transcript: string };
              if (data.success && data.transcript) {
                setTranscript(data.transcript);
                void runParseAndExecute(data.transcript);
              }
            } catch {
              setStatus("error");
              setError("Transcription request failed. Use the typed command bar.");
            }
          };
          reader.readAsDataURL(audioBlob);
        };
        mediaRecorder.start();
      } catch {
        setStatus("error");
        setError("Microphone permission was denied. Allow microphone access or type below.");
      }
    };

    const Ctor = getRecognitionCtor();
    if (!Ctor) {
      // No on-device recognition (e.g. Firefox): server STT fallback.
      void startRecorderFallback();
      return;
    }

    try {
      const recognition = new Ctor();
      recognition.lang = language;
      recognition.continuous = false;
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
        setTranscript(finalText || interim);
        if (finalText) {
          setTranscript(finalText);
          stopListening();
          void runParseAndExecute(finalText);
        }
      };

      recognition.onerror = (event) => {
        if (event.error === "network") {
          // Browser cloud STT blocked: server STT fallback when configured.
          if (capabilities.stt === "sarvam") {
            void startRecorderFallback();
          } else {
            setStatus("idle");
            setError(
              "Browser speech service unreachable. Type below or click a quick prompt.",
            );
            inputRef.current?.focus();
          }
        } else if (event.error === "not-allowed" || event.error === "service-not-allowed") {
          setStatus("error");
          setError("Microphone permission was denied. Allow microphone access or type below.");
        } else if (event.error === "no-speech") {
          setStatus("idle");
        }
      };

      recognition.onend = () => {
        setStatus((current) => (current === "listening" ? "idle" : current));
      };

      recognitionRef.current = recognition;
      recognition.start();
    } catch {
      void startRecorderFallback();
    }
  }, [capabilities.stt, language, runParseAndExecute, stopListening]);

  const submitTyped = useCallback(() => {
    const text = typed;
    setTyped("");
    void runParseAndExecute(text);
  }, [typed, runParseAndExecute]);

  const busy = status === "parsing" || status === "listening";

  if (pathname === "/") {
    return null;
  }

  return (
    <div className="pointer-events-none fixed bottom-6 left-1/2 -translate-x-1/2 z-[100] flex flex-col items-center gap-3 w-full max-w-2xl px-4">
      {/* ================= Drawer Popover (Active Result / Refusal / Confirmation) ================= */}
      {open && (
        <div
          className="pointer-events-auto w-full overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl transition-all duration-300 animate-rise text-slate-900"
          role="dialog"
          aria-label="ARGUS Voice Copilot"
        >
          {/* Header */}
          <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3 bg-slate-50/70">
            <div className="flex items-center gap-2.5">
              <span className="flex h-7 w-7 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-900 shadow-xs">
                <svg viewBox="0 0 42 34" className="w-4 h-3.5" fill="currentColor" aria-hidden>
                  <polygon points="12,0 30,0 33.2,3.2 15.2,3.2" />
                  <polygon points="14.6,5.6 32.6,5.6 35.8,8.8 17.8,8.8" />
                  <polygon points="17.2,11.2 35.2,11.2 38.4,14.4 20.4,14.4" />
                  <polygon points="3.2,16.8 21.2,16.8 24.4,20 6.4,20" />
                  <polygon points="5.8,22.4 23.8,22.4 27,25.6 9,25.6" />
                  <polygon points="8.4,28 26.4,28 29.6,31.2 11.6,31.2" />
                </svg>
              </span>
              <div>
                <div className="flex items-center gap-2">
                  <p className="text-xs font-bold tracking-tight text-slate-900">ARGUS Copilot</p>
                  <span className="rounded-full bg-blue-50 border border-blue-200 px-2 py-0.5 text-[10px] font-semibold text-blue-700">
                    Sarvam AI Voice Engine
                  </span>
                </div>
                <p className="text-[10px] font-medium uppercase tracking-wider text-slate-500">Read · Brief · Navigate Only</p>
              </div>
            </div>

            <div className="flex items-center gap-2">
              <select
                value={language}
                onChange={(event) => setLanguage(event.target.value)}
                aria-label="Voice language"
                suppressHydrationWarning
                className="rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-xs font-medium text-slate-700 focus:outline-none focus:border-slate-400"
              >
                {LANGUAGES.map((option) => (
                  <option key={option.code} value={option.code}>
                    {option.label}
                  </option>
                ))}
              </select>
              <button
                onClick={() => setMuted((m) => !m)}
                aria-label={muted ? "Unmute voice responses" : "Mute voice responses"}
                suppressHydrationWarning
                className={`rounded-lg p-1.5 transition-colors ${
                  muted ? "text-slate-400 hover:text-slate-600" : "text-emerald-600 hover:text-emerald-700"
                } hover:bg-slate-100`}
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
                  {muted ? (
                    <>
                      <line x1="23" y1="9" x2="17" y2="15" />
                      <line x1="17" y1="9" x2="23" y2="15" />
                    </>
                  ) : (
                    <path d="M15.54 8.46a5 5 0 0 1 0 7.07" />
                  )}
                </svg>
              </button>
              <button
                onClick={() => setOpen(false)}
                aria-label="Close voice panel"
                suppressHydrationWarning
                className="rounded-lg p-1.5 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-700"
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <path d="m6 6 12 12M18 6 6 18" />
                </svg>
              </button>
            </div>
          </div>

          <div className="space-y-3 p-4">
            {/* Quick Action Prompt Chips */}
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-[10px] uppercase font-bold tracking-wider text-slate-500 mr-1">Quick Prompts:</span>
              {PRESET_PROMPTS.map((chip) => (
                <button
                  key={chip.text}
                  onClick={() => void runParseAndExecute(chip.text)}
                  disabled={busy}
                  suppressHydrationWarning
                  className="rounded-full border border-slate-200 bg-slate-100 px-2.5 py-1 text-[11px] font-semibold text-slate-700 transition-colors hover:border-slate-300 hover:bg-slate-200 hover:text-slate-900 disabled:opacity-40"
                >
                  {chip.label}
                </button>
              ))}
            </div>

            {/* Listening / Wave animation state */}
            {status === "listening" && (
              <div className="relative overflow-hidden rounded-xl border border-blue-200 bg-blue-50/70 p-3.5">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <Wave className="w-8 h-5 text-blue-600" />
                    <p className="text-xs font-bold text-blue-900">Listening to your voice (Sarvam AI)…</p>
                  </div>
                  <button
                    onClick={stopListening}
                    suppressHydrationWarning
                    className="text-xs font-bold text-blue-700 hover:text-blue-900 px-2 py-1 rounded bg-white border border-blue-200 shadow-2xs"
                  >
                    Done / Stop
                  </button>
                </div>
                {transcript && (
                  <p className="mt-2 text-xs italic font-medium text-slate-800 bg-white rounded-lg p-2.5 border border-blue-100 shadow-2xs">
                    &ldquo;{transcript}&rdquo;
                  </p>
                )}
              </div>
            )}

            {/* Parsing State */}
            {status === "parsing" && (
              <div className="flex items-center gap-3 rounded-xl border border-slate-200 bg-slate-50 p-3 text-xs font-medium text-slate-700">
                <Wave className="w-6 h-4 text-blue-600" />
                <span>Interpreting intent & checking safety policy…</span>
              </div>
            )}

            {/* Refused State (Financial Safety Boundary Guardrail) */}
            {status === "refused" && parse?.status === "REFUSED" && (
              <div className="rounded-xl border border-rose-200 bg-rose-50/90 p-3.5 text-rose-900" role="alert">
                <div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-wider text-rose-700">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <circle cx="12" cy="12" r="10" />
                    <line x1="12" y1="8" x2="12" y2="12" />
                    <line x1="12" y1="16" x2="12.01" y2="16" />
                  </svg>
                  Financial Safety Refusal · {parse.forbidden_intent?.replace(/_/g, " ").toLowerCase()}
                </div>
                <p className="mt-1.5 text-xs font-semibold leading-relaxed text-slate-900">{parse.message}</p>
                <div className="mt-2 rounded bg-white p-2 text-[10.5px] font-medium text-slate-600 border border-rose-200">
                  ⚠️ Per PRD financial safety rules, corrections and money movements can only be approved through the human UI approval panel.
                </div>
              </div>
            )}

            {/* Confirmation State */}
            {status === "confirm" && parse && (
              <div className="rounded-xl border border-amber-200 bg-amber-50 p-3.5">
                <p className="text-[10px] font-bold uppercase tracking-wider text-amber-800">Confirmation Required</p>
                <p className="mt-1 text-xs font-medium text-slate-800">{parse.message}</p>
                <div className="mt-3 flex gap-2">
                  <button
                    onClick={() => void runParseAndExecute(parse.transcript, true)}
                    suppressHydrationWarning
                    className="rounded-lg bg-slate-900 px-3.5 py-1.5 text-xs font-bold text-white shadow-sm hover:bg-slate-800"
                  >
                    Confirm & Execute
                  </button>
                  <button
                    onClick={() => setStatus("idle")}
                    suppressHydrationWarning
                    className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}

            {/* Execution Result State */}
            {status === "result" && execution && (
              <div className="rounded-xl border border-emerald-200 bg-emerald-50/80 p-3.5">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <p className="text-[10px] font-bold uppercase tracking-wider text-emerald-800">
                      {execution.intent?.replace(/_/g, " ").toLowerCase() ?? "Result"}
                    </p>
                    {latencyMs !== null && (
                      <span className="rounded-full border border-emerald-200 bg-white px-2 py-0.5 font-mono text-[9px] font-semibold text-emerald-700">
                        {latencyMs} ms
                      </span>
                    )}
                  </div>
                  <button
                    onClick={() =>
                      playAudioOrSpeak(
                        execution.message,
                        language,
                        execution.audio_base64,
                        execution.content_type || "audio/wav",
                      )
                    }
                    suppressHydrationWarning
                    className="flex items-center gap-1 text-xs font-bold text-emerald-700 hover:text-emerald-900"
                  >
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
                      <path d="M15.54 8.46a5 5 0 0 1 0 7.07" />
                    </svg>
                    Replay Audio
                  </button>
                </div>
                <p className="mt-1.5 text-xs font-medium leading-relaxed text-slate-900">{execution.message}</p>
                {execution.cases && execution.cases.length > 0 && (
                  <div className="mt-2.5 flex flex-wrap gap-1.5">
                    {execution.cases.slice(0, 6).map((c) => (
                      <span
                        key={c.case_id}
                        className="rounded-md border border-slate-200 bg-white px-2 py-0.5 font-mono text-[10.5px] font-semibold text-slate-800 shadow-2xs"
                      >
                        {c.case_id} · {c.status}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Error / Recovery notice */}
            {error && (
              <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs font-medium text-amber-900">
                <p>{error}</p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ================= Center Floating Pill ================= */}
      <div className="pointer-events-auto relative w-full group">
        {/* Pill Body */}
        <div className="relative flex items-center justify-between gap-2.5 rounded-full border border-slate-200 bg-white/95 px-3 py-2 shadow-xl backdrop-blur-md">
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
              placeholder="Ask or command ARGUS... (or push mic)"
              aria-label="Voice or typed command"
              suppressHydrationWarning
              className="w-full bg-transparent px-2 text-xs font-medium text-slate-900 placeholder-slate-400 focus:outline-none"
            />
          </div>

          {/* Action Buttons */}
          <div className="flex items-center gap-1.5 shrink-0">
            {/* Run Button (visible if user typed something) */}
            {typed.trim() && (
              <button
                onClick={submitTyped}
                disabled={busy}
                suppressHydrationWarning
                className="flex h-8 items-center justify-center rounded-full bg-slate-900 px-3 text-xs font-bold text-white transition hover:bg-slate-800 disabled:opacity-40"
              >
                Send
              </button>
            )}

            {/* Push-to-Talk Mic Button */}
            <button
              onClick={status === "listening" ? stopListening : startListening}
              disabled={busy && status !== "listening"}
              aria-label={status === "listening" ? "Stop listening" : "Start push-to-talk voice copilot"}
              suppressHydrationWarning
              className={`flex h-9 w-9 items-center justify-center rounded-full border transition-all ${
                status === "listening"
                  ? "border-blue-600 bg-blue-600 text-white animate-pulse shadow-md shadow-blue-500/30"
                  : "border-slate-200 bg-slate-100 text-slate-700 hover:border-slate-300 hover:bg-slate-200 hover:text-slate-900 hover:scale-105"
              }`}
            >
              {status === "listening" ? (
                <Wave className="w-4 h-3 text-white" />
              ) : (
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
                  <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                  <line x1="12" x2="12" y1="19" y2="22" />
                </svg>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
