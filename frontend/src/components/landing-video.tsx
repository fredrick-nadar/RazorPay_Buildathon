"use client";

import { useEffect, useRef } from "react";

/**
 * Full-bleed looping background video. Client component so the muted
 * property is guaranteed before autoplay (SSR does not serialize `muted`).
 */
export function LandingVideo() {
  const ref = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    const video = ref.current;
    if (!video) return;
    video.muted = true;
    video.defaultMuted = true;
    const attempt = video.play();
    if (attempt) attempt.catch(() => {});
  }, []);

  return (
    <video
      ref={ref}
      className="bg-video"
      autoPlay
      muted
      loop
      playsInline
      tabIndex={-1}
      aria-hidden="true"
    >
      <source
        src="https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260808_075824_7c8a2ef3-826c-43ca-81a1-162429faa306.mp4"
        type="video/mp4"
      />
    </video>
  );
}
