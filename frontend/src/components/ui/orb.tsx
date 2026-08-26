"use client";

import { Mesh, Program, Renderer, Triangle, Vec3 } from "ogl";
import React, { useEffect, useRef } from "react";
import "./Orb.css";

interface OrbProps {
  hue?: number;
  hoverIntensity?: number;
  rotateOnHover?: boolean;
  forceHoverState?: boolean;
  backgroundColor?: string;
  status?: "idle" | "listening" | "parsing" | "speaking" | "result" | "refused" | "error";
  voiceEnergy?: number;
  audioRef?: React.RefObject<HTMLAudioElement | null>;
  className?: string;
}

export function Orb({
  hue = 0,
  hoverIntensity = 0.4,
  rotateOnHover = true,
  forceHoverState = false,
  backgroundColor = "#000000",
  status = "idle",
  voiceEnergy: manualVoiceEnergy,
  audioRef,
  className = "",
}: OrbProps) {
  const ctnDom = useRef<HTMLDivElement | null>(null);

  // Web Audio Analyser setup for real-time acoustic modulation
  const analyserRef = useRef<AnalyserNode | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const sourceNodeRef = useRef<MediaElementAudioSourceNode | null>(null);

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

  const vert = /* glsl */ `
    precision highp float;
    attribute vec2 position;
    attribute vec2 uv;
    varying vec2 vUv;
    void main() {
      vUv = uv;
      gl_Position = vec4(position, 0.0, 1.0);
    }
  `;

  const frag = /* glsl */ `
    precision highp float;

    uniform float iTime;
    uniform vec3 iResolution;
    uniform float hue;
    uniform float hover;
    uniform float rot;
    uniform float hoverIntensity;
    uniform vec3 backgroundColor;
    varying vec2 vUv;

    vec3 rgb2yiq(vec3 c) {
      float y = dot(c, vec3(0.299, 0.587, 0.114));
      float i = dot(c, vec3(0.596, -0.274, -0.322));
      float q = dot(c, vec3(0.211, -0.523, 0.312));
      return vec3(y, i, q);
    }
    
    vec3 yiq2rgb(vec3 c) {
      float r = c.x + 0.956 * c.y + 0.621 * c.z;
      float g = c.x - 0.272 * c.y - 0.647 * c.z;
      float b = c.x - 1.106 * c.y + 1.703 * c.z;
      return vec3(r, g, b);
    }
    
    vec3 adjustHue(vec3 color, float hueDeg) {
      float hueRad = hueDeg * 3.14159265 / 180.0;
      vec3 yiq = rgb2yiq(color);
      float cosA = cos(hueRad);
      float sinA = sin(hueRad);
      float i = yiq.y * cosA - yiq.z * sinA;
      float q = yiq.y * sinA + yiq.z * cosA;
      yiq.y = i;
      yiq.z = q;
      return yiq2rgb(yiq);
    }

    vec3 hash33(vec3 p3) {
      p3 = fract(p3 * vec3(0.1031, 0.11369, 0.13787));
      p3 += dot(p3, p3.yxz + 19.19);
      return -1.0 + 2.0 * fract(vec3(
        p3.x + p3.y,
        p3.x + p3.z,
        p3.y + p3.z
      ) * p3.zyx);
    }

    float snoise3(vec3 p) {
      const float K1 = 0.333333333;
      const float K2 = 0.166666667;
      vec3 i = floor(p + (p.x + p.y + p.z) * K1);
      vec3 d0 = p - (i - (i.x + i.y + i.z) * K2);
      vec3 e = step(vec3(0.0), d0 - d0.yzx);
      vec3 i1 = e * (1.0 - e.zxy);
      vec3 i2 = 1.0 - e.zxy * (1.0 - e);
      vec3 d1 = d0 - (i1 - K2);
      vec3 d2 = d0 - (i2 - K1);
      vec3 d3 = d0 - 0.5;
      vec4 h = max(0.6 - vec4(
        dot(d0, d0),
        dot(d1, d1),
        dot(d2, d2),
        dot(d3, d3)
      ), 0.0);
      vec4 n = h * h * h * h * vec4(
        dot(d0, hash33(i)),
        dot(d1, hash33(i + i1)),
        dot(d2, hash33(i + i2)),
        dot(d3, hash33(i + 1.0))
      );
      return dot(vec4(31.316), n);
    }

    // Refined Greyish / Silver / Graphite Gradient Palette
    const vec3 baseColor1 = vec3(0.22, 0.23, 0.28); // Slate / Charcoal
    const vec3 baseColor2 = vec3(0.68, 0.70, 0.76); // Luminous Silver-Grey
    const vec3 baseColor3 = vec3(0.06, 0.07, 0.09); // Deep Obsidian Black

    vec4 draw(vec2 uv) {
      vec3 color1 = adjustHue(baseColor1, hue);
      vec3 color2 = adjustHue(baseColor2, hue);
      vec3 color3 = adjustHue(baseColor3, hue);
      
      float len = length(uv);
      float ang = atan(uv.y, uv.x);

      // Clean, unwarped circular boundary
      float circleEdge = smoothstep(0.96, 0.90, len);
      if (circleEdge <= 0.001) {
        return vec4(0.0);
      }

      // Internal fluid gradient speed and harmonic noise flow
      float internalSpeed = 0.55 + hover * 1.85;
      float n0 = snoise3(vec3(uv * 0.82, iTime * internalSpeed)) * 0.5 + 0.5;
      float n1 = snoise3(vec3(uv * 1.6 + vec2(iTime * 0.4), iTime * internalSpeed * 0.8)) * 0.5 + 0.5;

      // Internal fluid wave and rotational shimmer
      float cl = cos(ang + iTime * (1.2 + hover * 2.2)) * 0.5 + 0.5;
      float swirl = sin(len * 4.0 - iTime * (1.5 + hover * 2.5) + n0 * 2.0) * 0.5 + 0.5;

      // Blend greyish gradient layers seamlessly inside the circle
      vec3 gradA = mix(color1, color2, cl);
      vec3 gradB = mix(color3, gradA, n0);
      vec3 finalCol = mix(gradB, color2, swirl * (0.35 + hover * 0.45));

      // Specular core and soft edge lighting
      float coreLight = smoothstep(0.85, 0.0, len) * 0.25;
      float rimLight = smoothstep(0.70, 0.94, len) * 0.4;
      finalCol += vec3(coreLight + rimLight * (0.6 + n1 * 0.4));

      // Crisp circular boundary with soft ambient anti-aliased edge
      float alpha = circleEdge;
      return vec4(finalCol * alpha, alpha);
    }

    vec4 mainImage(vec2 fragCoord) {
      vec2 center = iResolution.xy * 0.5;
      float size = min(iResolution.x, iResolution.y);
      vec2 uv = (fragCoord - center) / size * 2.0;
      
      // Continuous smooth rotational spin
      float angle = rot;
      float s = sin(angle);
      float c = cos(angle);
      uv = vec2(c * uv.x - s * uv.y, s * uv.x + c * uv.y);
      
      return draw(uv);
    }

    void main() {
      vec2 fragCoord = vUv * iResolution.xy;
      vec4 col = mainImage(fragCoord);
      gl_FragColor = vec4(col.rgb, col.a);
    }
  `;

  useEffect(() => {
    const container = ctnDom.current;
    if (!container) return;

    const renderer = new Renderer({ alpha: true, premultipliedAlpha: false });
    const gl = renderer.gl;
    gl.clearColor(0, 0, 0, 0);
    container.appendChild(gl.canvas);

    const geometry = new Triangle(gl);
    const program = new Program(gl, {
      vertex: vert,
      fragment: frag,
      uniforms: {
        iTime: { value: 0 },
        iResolution: {
          value: new Vec3(gl.canvas.width, gl.canvas.height, gl.canvas.width / gl.canvas.height),
        },
        hue: { value: hue },
        hover: { value: 0 },
        rot: { value: 0 },
        hoverIntensity: { value: hoverIntensity },
        backgroundColor: { value: hexToVec3(backgroundColor) },
      },
    });

    const mesh = new Mesh(gl, { geometry, program });

    function resize() {
      if (!container) return;
      const dpr = window.devicePixelRatio || 1;
      const width = container.clientWidth || 240;
      const height = container.clientHeight || 240;
      renderer.setSize(width * dpr, height * dpr);
      gl.canvas.style.width = width + "px";
      gl.canvas.style.height = height + "px";
      program.uniforms.iResolution.value.set(
        gl.canvas.width,
        gl.canvas.height,
        gl.canvas.width / gl.canvas.height,
      );
    }
    window.addEventListener("resize", resize);
    resize();

    let lastTime = performance.now();
    let currentRot = 0;
    const rotationSpeed = 0.35; // Continuous smooth spin
    const freqData = new Uint8Array(32);

    let rafId: number;
    const update = (t: number) => {
      rafId = requestAnimationFrame(update);
      const dt = Math.min((t - lastTime) * 0.001, 0.05);
      lastTime = t;

      // Real-time acoustic frequency / voice analysis
      let energy = 0;
      if (analyserRef.current && status === "speaking") {
        try {
          analyserRef.current.getByteFrequencyData(freqData);
          let sum = 0;
          for (let i = 0; i < 16; i++) {
            sum += freqData[i] ?? 0;
          }
          energy = sum / (16 * 255);
        } catch {
          energy = 0;
        }
      }

      if (manualVoiceEnergy !== undefined) {
        energy = manualVoiceEnergy;
      }

      // Smooth synthetic energy when speaking or listening
      if (status === "speaking" && energy < 0.05) {
        const timeSec = t * 0.001;
        energy =
          0.4 +
          0.45 * Math.sin(timeSec * 3.5) * Math.cos(timeSec * 2.2) +
          0.15 * Math.sin(timeSec * 6.5);
      } else if (status === "listening") {
        const timeSec = t * 0.001;
        energy = 0.18 + 0.08 * Math.sin(timeSec * 2.0);
      } else if (status === "parsing") {
        energy = 0.32;
      }

      program.uniforms.iTime.value = t * 0.001;
      program.uniforms.hue.value = hue;
      program.uniforms.hoverIntensity.value = hoverIntensity;
      program.uniforms.backgroundColor.value = hexToVec3(backgroundColor);

      // Target hover modulates internal gradient flow within the circle
      const targetHover = status === "speaking" ? 0.75 + energy * 0.5 : status === "listening" ? 0.25 : 0.15;
      program.uniforms.hover.value +=
        (targetHover - program.uniforms.hover.value) * 0.12;

      // Constant continuous smooth spin
      currentRot += dt * rotationSpeed * (status === "speaking" ? 1.6 + energy * 1.2 : 1.0);
      program.uniforms.rot.value = currentRot;

      renderer.render({ scene: mesh });
    };
    rafId = requestAnimationFrame(update);

    return () => {
      cancelAnimationFrame(rafId);
      window.removeEventListener("resize", resize);
      if (container.contains(gl.canvas)) {
        container.removeChild(gl.canvas);
      }
      gl.getExtension("WEBGL_lose_context")?.loseContext();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hue, hoverIntensity, rotateOnHover, forceHoverState, backgroundColor, status, manualVoiceEnergy]);

  return <div ref={ctnDom} className={`orb-container ${className}`} />;
}

function hslToRgb(h: number, s: number, l: number): Vec3 {
  let r: number, g: number, b: number;

  if (s === 0) {
    r = g = b = l;
  } else {
    const hue2rgb = (p: number, q: number, t: number) => {
      if (t < 0) t += 1;
      if (t > 1) t -= 1;
      if (t < 1 / 6) return p + (q - p) * 6 * t;
      if (t < 1 / 2) return q;
      if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
      return p;
    };

    const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
    const p = 2 * l - q;
    r = hue2rgb(p, q, h + 1 / 3);
    g = hue2rgb(p, q, h);
    b = hue2rgb(p, q, h - 1 / 3);
  }

  return new Vec3(r, g, b);
}

function hexToVec3(color: string): Vec3 {
  if (color.startsWith("#")) {
    const r = parseInt(color.slice(1, 3), 16) / 255;
    const g = parseInt(color.slice(3, 5), 16) / 255;
    const b = parseInt(color.slice(5, 7), 16) / 255;
    return new Vec3(r, g, b);
  }

  const rgbMatch = color.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
  if (rgbMatch && rgbMatch[1] && rgbMatch[2] && rgbMatch[3]) {
    return new Vec3(
      parseInt(rgbMatch[1]) / 255,
      parseInt(rgbMatch[2]) / 255,
      parseInt(rgbMatch[3]) / 255,
    );
  }

  const hslMatch = color.match(/hsla?\((\d+),\s*(\d+)%,\s*(\d+)%/);
  if (hslMatch && hslMatch[1] && hslMatch[2] && hslMatch[3]) {
    const h = parseInt(hslMatch[1]) / 360;
    const s = parseInt(hslMatch[2]) / 100;
    const l = parseInt(hslMatch[3]) / 100;
    return hslToRgb(h, s, l);
  }

  return new Vec3(0, 0, 0);
}

export default Orb;
