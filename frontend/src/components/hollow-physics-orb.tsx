"use client";

import React, { useEffect, useRef } from "react";

interface HollowPhysicsOrbProps {
  status: "idle" | "listening" | "parsing" | "speaking" | "result" | "refused" | "error";
  size?: number;
  className?: string;
  audioRef?: React.RefObject<HTMLAudioElement | null>;
}

export function HollowPhysicsOrb({
  status,
  size = 240,
  className = "",
  audioRef,
}: HollowPhysicsOrbProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const animationFrameRef = useRef<number | null>(null);

  // Web Audio Analyser setup
  const analyserRef = useRef<AnalyserNode | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const sourceNodeRef = useRef<MediaElementAudioSourceNode | null>(null);

  // Connect Web Audio API to the TTS audio element when available
  useEffect(() => {
    const audioEl = audioRef?.current;
    if (!audioEl) return;

    const setupAudio = () => {
      try {
        if (!audioContextRef.current) {
          const AudioContextClass =
            window.AudioContext ||
            (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
          if (AudioContextClass) {
            audioContextRef.current = new AudioContextClass();
          }
        }
        if (audioContextRef.current && !sourceNodeRef.current) {
          const ctx = audioContextRef.current;
          analyserRef.current = ctx.createAnalyser();
          analyserRef.current.fftSize = 64;
          analyserRef.current.smoothingTimeConstant = 0.8;

          try {
            sourceNodeRef.current = ctx.createMediaElementSource(audioEl);
            sourceNodeRef.current.connect(analyserRef.current);
            analyserRef.current.connect(ctx.destination);
          } catch {
            /* If already connected or cross-origin restrictions */
          }
        }
      } catch {
        /* audio analysis is progressive enhancement */
      }
    };

    const handlePlay = () => {
      if (audioContextRef.current?.state === "suspended") {
        void audioContextRef.current.resume();
      }
      setupAudio();
    };

    audioEl.addEventListener("play", handlePlay);
    return () => {
      audioEl.removeEventListener("play", handlePlay);
    };
  }, [audioRef]);

  // Spring physics and organic harmonic oscillator state
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const NUM_POINTS = 36;
    const springs: { currentRadius: number; targetRadius: number; velocity: number }[] = [];
    for (let i = 0; i < NUM_POINTS; i++) {
      springs.push({ currentRadius: 1, targetRadius: 1, velocity: 0 });
    }

    let time = 0;
    const freqData = new Uint8Array(32);

    const render = () => {
      time += 0.035;

      // Determine frequency energy level
      let voiceEnergy = 0;
      let bassEnergy = 0;
      let midEnergy = 0;

      if (analyserRef.current && status === "speaking") {
        try {
          analyserRef.current.getByteFrequencyData(freqData);
          let sum = 0;
          for (let i = 0; i < 16; i++) {
            sum += freqData[i] ?? 0;
          }
          voiceEnergy = sum / (16 * 255);
          bassEnergy = ((freqData[1] ?? 0) + (freqData[2] ?? 0) + (freqData[3] ?? 0)) / (3 * 255);
          midEnergy = ((freqData[6] ?? 0) + (freqData[7] ?? 0) + (freqData[8] ?? 0)) / (3 * 255);
        } catch {
          voiceEnergy = 0;
        }
      }

      // Procedural synthetic energy fallback for smooth physics animation
      if (status === "speaking" && voiceEnergy < 0.05) {
        const speechEnvelope =
          0.35 +
          0.45 * Math.sin(time * 3.8) * Math.cos(time * 2.1) +
          0.2 * Math.sin(time * 7.2);
        voiceEnergy = Math.max(0.15, speechEnvelope);
        bassEnergy = 0.4 + 0.3 * Math.sin(time * 2.4);
        midEnergy = 0.35 + 0.25 * Math.cos(time * 4.9);
      } else if (status === "listening") {
        voiceEnergy = 0.12 + 0.08 * Math.sin(time * 1.8);
        bassEnergy = 0.1;
        midEnergy = 0.1;
      } else if (status === "parsing") {
        voiceEnergy = 0.25 + 0.15 * Math.sin(time * 5.0);
      }

      const width = canvas.width;
      const height = canvas.height;
      const centerX = width / 2;
      const centerY = height / 2;

      ctx.clearRect(0, 0, width, height);

      // Base dimensional radii (hollow ring: outer ~80px, inner ~52px at standard scale)
      const baseOuterRadius = size * 0.34;
      const baseInnerRadius = size * 0.21;

      // Update spring physics for each radial point around circle
      const outerPoints: { x: number; y: number }[] = [];
      const innerPoints: { x: number; y: number }[] = [];

      for (let i = 0; i < NUM_POINTS; i++) {
        const angle = (i / NUM_POINTS) * Math.PI * 2;
        const spring = springs[i] ?? { currentRadius: 1, targetRadius: 1, velocity: 0 };

        // Harmonic acoustic frequency displacement
        const harmonic1 = Math.sin(angle * 3 + time * 2.8) * (0.2 + bassEnergy * 0.4);
        const harmonic2 = Math.cos(angle * 5 - time * 3.4) * (0.15 + midEnergy * 0.35);
        const harmonic3 = Math.sin(angle * 2 + time * 1.5) * (0.1 + voiceEnergy * 0.3);
        const wobbleFactor = harmonic1 + harmonic2 + harmonic3;

        spring.targetRadius = 1 + wobbleFactor * voiceEnergy * 0.65;

        // Spring physics: F = -k * x - c * v
        const k = 0.18;
        const damping = 0.78;
        const displacement = spring.targetRadius - spring.currentRadius;
        spring.velocity += displacement * k;
        spring.velocity *= damping;
        spring.currentRadius += spring.velocity;

        // Outer radius with displacement
        const rOuter = baseOuterRadius * spring.currentRadius;
        const xOuter = centerX + Math.cos(angle) * rOuter;
        const yOuter = centerY + Math.sin(angle) * rOuter;
        outerPoints.push({ x: xOuter, y: yOuter });

        // Inner radius with slight counter-phase displacement
        const innerWobble = 1 + wobbleFactor * 0.55 * voiceEnergy;
        const rInner = baseInnerRadius * (0.95 + 0.05 * innerWobble);
        const xInner = centerX + Math.cos(angle) * rInner;
        const yInner = centerY + Math.sin(angle) * rInner;
        innerPoints.push({ x: xInner, y: yInner });
      }

      // 1. Draw outer subtle dark ambient glow / shadow
      ctx.save();
      ctx.beginPath();
      drawSmoothClosedCurve(ctx, outerPoints);
      ctx.shadowColor = "rgba(0, 0, 0, 0.35)";
      ctx.shadowBlur = 24;
      ctx.shadowOffsetY = 10;
      ctx.fillStyle = "rgba(0,0,0,0.01)";
      ctx.fill();
      ctx.restore();

      // 2. Draw Hollow Black Ring Body with evenodd fill rule (empty inside!)
      ctx.save();
      ctx.beginPath();

      // Outer path (clockwise)
      drawSmoothClosedCurve(ctx, outerPoints);

      // Inner path (counter-clockwise for evenodd hollow hole)
      drawSmoothClosedCurve(ctx, innerPoints.slice().reverse());

      // Pure pitch-black fill
      ctx.fillStyle = "#09090b";
      ctx.fill("evenodd");

      // 3. Subtle iridescent specular rim on outer edge
      ctx.lineWidth = 2.5;
      ctx.strokeStyle = status === "speaking" ? "#18181b" : "#27272a";
      ctx.stroke();
      ctx.restore();

      // 4. Subtle inner ring highlight border
      ctx.save();
      ctx.beginPath();
      drawSmoothClosedCurve(ctx, innerPoints);
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = "rgba(255, 255, 255, 0.08)";
      ctx.stroke();
      ctx.restore();

      // 5. Outer delicate fluid sheen stroke
      ctx.save();
      ctx.beginPath();
      drawSmoothClosedCurve(ctx, outerPoints);
      ctx.lineWidth = 1;
      ctx.strokeStyle =
        status === "speaking" ? "rgba(255, 255, 255, 0.18)" : "rgba(255, 255, 255, 0.09)";
      ctx.stroke();
      ctx.restore();

      animationFrameRef.current = requestAnimationFrame(render);
    };

    render();

    return () => {
      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current);
      }
    };
  }, [status, size]);

  return (
    <div className={`relative flex items-center justify-center ${className}`}>
      <canvas
        ref={canvasRef}
        width={size * 1.5}
        height={size * 1.5}
        style={{ width: size, height: size }}
        className="pointer-events-none select-none"
      />
    </div>
  );
}

/**
 * Draw a smooth, continuous closed cubic spline through array of 2D points.
 */
function drawSmoothClosedCurve(
  ctx: CanvasRenderingContext2D,
  points: { x: number; y: number }[],
) {
  const len = points.length;
  if (len < 3) return;

  const firstPoint = points[0];
  const lastPoint = points[len - 1];
  if (!firstPoint || !lastPoint) return;

  const midX = (firstPoint.x + lastPoint.x) / 2;
  const midY = (firstPoint.y + lastPoint.y) / 2;

  ctx.moveTo(midX, midY);

  for (let i = 0; i < len; i++) {
    const p0 = points[i];
    const p1 = points[(i + 1) % len];
    if (!p0 || !p1) continue;

    const nextMidX = (p0.x + p1.x) / 2;
    const nextMidY = (p0.y + p1.y) / 2;
    ctx.quadraticCurveTo(p0.x, p0.y, nextMidX, nextMidY);
  }

  ctx.closePath();
}
