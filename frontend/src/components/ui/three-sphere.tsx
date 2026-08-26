"use client";

import React, { useEffect, useRef } from "react";
import * as THREE from "three";

interface ThreeSphereProps {
  status?: "idle" | "listening" | "parsing" | "speaking" | "result" | "refused" | "error";
  voiceEnergy?: number;
  size?: number;
  className?: string;
}

export function ThreeSphere({
  status = "idle",
  voiceEnergy = 0,
  size = 280,
  className = "",
}: ThreeSphereProps) {
  const mountRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const container = mountRef.current;
    if (!container) return;

    // --- SCENE SETUP ---
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 1000);
    camera.position.z = 4.6;

    const renderer = new THREE.WebGLRenderer({
      antialias: true,
      alpha: true,
      powerPreference: "high-performance",
    });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(size, size);
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.35;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    container.appendChild(renderer.domElement);

    // --- PROCEDURAL HIGH-END POLISHED TEXTURE (Perplexity 3D Avatar Sheen) ---
    const canvas = document.createElement("canvas");
    canvas.width = 1024;
    canvas.height = 1024;
    const ctx = canvas.getContext("2d");
    if (ctx) {
      // Luxury dark graphite / slate obsidian gradient
      const grad = ctx.createLinearGradient(0, 0, 1024, 1024);
      grad.addColorStop(0.0, "#111317");
      grad.addColorStop(0.25, "#1f222b");
      grad.addColorStop(0.5, "#323745");
      grad.addColorStop(0.75, "#52586a");
      grad.addColorStop(0.9, "#8a92a6");
      grad.addColorStop(1.0, "#0c0d10");
      ctx.fillStyle = grad;
      ctx.fillRect(0, 0, 1024, 1024);

      // Delicate studio reflection waves
      for (let i = 0; i < 8; i++) {
        ctx.beginPath();
        const y = 120 + i * 110;
        ctx.moveTo(0, y);
        ctx.bezierCurveTo(300, y - 60, 700, y + 60, 1024, y);
        ctx.strokeStyle = `rgba(255, 255, 255, ${0.04 + (i % 3) * 0.03})`;
        ctx.lineWidth = 2.5;
        ctx.stroke();
      }

      // Fine concentric luxury topography rings
      for (let r = 40; r < 480; r += 32) {
        ctx.beginPath();
        ctx.arc(512, 512, r, 0, Math.PI * 2);
        ctx.strokeStyle = "rgba(255, 255, 255, 0.05)";
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }
    }
    const texture = new THREE.CanvasTexture(canvas);
    texture.wrapS = THREE.RepeatWrapping;
    texture.wrapT = THREE.RepeatWrapping;

    // --- 3D POLISHED SPHERE MESH ---
    const geometry = new THREE.SphereGeometry(1.42, 64, 64);
    const material = new THREE.MeshPhysicalMaterial({
      map: texture,
      color: new THREE.Color(0x232631),
      roughness: 0.1,
      metalness: 0.22,
      clearcoat: 1.0,
      clearcoatRoughness: 0.06,
      reflectivity: 0.96,
      sheen: 0.7,
      sheenColor: new THREE.Color(0xe2e8f0),
      sheenRoughness: 0.25,
      ior: 1.52,
    });

    const sphereMesh = new THREE.Mesh(geometry, material);
    scene.add(sphereMesh);

    // High-gloss outer accent ring
    const ringGeo = new THREE.TorusGeometry(1.62, 0.014, 24, 120);
    const ringMat = new THREE.MeshStandardMaterial({
      color: 0xc8d0de,
      roughness: 0.15,
      metalness: 0.85,
      transparent: true,
      opacity: 0.55,
    });
    const ringMesh = new THREE.Mesh(ringGeo, ringMat);
    ringMesh.rotation.x = Math.PI / 3;
    scene.add(ringMesh);

    // --- STUDIO 3-POINT LIGHTING RIG ---
    // Key Light (Top-right specular sheen)
    const keyLight = new THREE.DirectionalLight(0xffffff, 3.4);
    keyLight.position.set(4.0, 4.5, 4.0);
    scene.add(keyLight);

    // Fill Light (Bottom-left cool silver fill)
    const fillLight = new THREE.DirectionalLight(0xa0aec0, 1.9);
    fillLight.position.set(-4.0, -2.5, 3.0);
    scene.add(fillLight);

    // Rim Light (Top-back edge highlight)
    const rimLight = new THREE.DirectionalLight(0xffffff, 2.8);
    rimLight.position.set(0, 5.0, -4.0);
    scene.add(rimLight);

    // Warm Accent Light
    const accentLight = new THREE.PointLight(0xf8fafc, 2.0, 8);
    accentLight.position.set(0, -3.5, 2.5);
    scene.add(accentLight);

    // Ambient light
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.65);
    scene.add(ambientLight);

    // --- ANIMATION STATE & LOOP ---
    let rafId: number;
    const clock = new THREE.Clock();

    const animate = () => {
      rafId = requestAnimationFrame(animate);
      const elapsedTime = clock.getElapsedTime();

      // Audio frequency modulation
      let energy = voiceEnergy;
      if (status === "speaking" && energy < 0.05) {
        energy =
          0.35 +
          0.28 * Math.sin(elapsedTime * 4.2) * Math.cos(elapsedTime * 2.5) +
          0.12 * Math.sin(elapsedTime * 7.5);
      }

      if (status === "speaking") {
        // --- REPLYING MODE: Continuous Dynamic 3D Rotation & Turntable Spin ---
        // Keeps actively rotating around its axis for the full duration of the AI's reply
        sphereMesh.rotation.y += 0.024 + energy * 0.035;
        sphereMesh.rotation.x = Math.sin(elapsedTime * 2.4) * 0.14;
        sphereMesh.rotation.z = Math.cos(elapsedTime * 1.8) * 0.1;

        // Specular ring spins with energetic orbit
        ringMesh.rotation.z += 0.018 + energy * 0.025;
        ringMesh.rotation.y += 0.012;
        ringMesh.rotation.x = Math.PI / 3 + Math.sin(elapsedTime * 2.8) * 0.12;

        // Voice dictation scale & light energy pulse
        const s = 1.0 + energy * 0.08 + Math.sin(elapsedTime * 4.5) * 0.02;
        sphereMesh.scale.set(s, s, s);

        keyLight.intensity = 3.4 + energy * 1.2;
        rimLight.intensity = 2.8 + energy * 1.0;
      } else if (status === "listening") {
        // --- LISTENING MODE: Gentle floating & random smooth precession ---
        sphereMesh.rotation.x = Math.sin(elapsedTime * 0.65) * 0.32;
        sphereMesh.rotation.y += 0.009;
        sphereMesh.rotation.z = Math.cos(elapsedTime * 0.55) * 0.22;

        ringMesh.rotation.z += 0.007;
        ringMesh.rotation.x = Math.PI / 3 + Math.sin(elapsedTime * 0.8) * 0.08;

        // Subtle breathing scale
        const s = 1.0 + Math.sin(elapsedTime * 2.2) * 0.02;
        sphereMesh.scale.set(s, s, s);
      } else {
        // --- IDLE / RESTING MODE: Smooth random multi-axis precession ---
        sphereMesh.rotation.x = Math.sin(elapsedTime * 0.45) * 0.22;
        sphereMesh.rotation.y += 0.006;
        sphereMesh.rotation.z = Math.cos(elapsedTime * 0.35) * 0.16;

        ringMesh.rotation.z += 0.004;

        sphereMesh.scale.set(1, 1, 1);
      }

      // Smooth floating vertical hover
      sphereMesh.position.y = Math.sin(elapsedTime * 1.4) * 0.05;
      ringMesh.position.y = sphereMesh.position.y;

      renderer.render(scene, camera);
    };

    animate();

    return () => {
      cancelAnimationFrame(rafId);
      geometry.dispose();
      material.dispose();
      ringGeo.dispose();
      ringMat.dispose();
      texture.dispose();
      renderer.dispose();
      if (container.contains(renderer.domElement)) {
        container.removeChild(renderer.domElement);
      }
    };
  }, [size, status, voiceEnergy]);

  return (
    <div
      ref={mountRef}
      className={`relative flex items-center justify-center select-none ${className}`}
      style={{ width: size, height: size }}
    />
  );
}

export default ThreeSphere;
