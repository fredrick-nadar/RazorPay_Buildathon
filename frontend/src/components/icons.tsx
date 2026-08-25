/**
 * Animated Lucide Icon Library for ARGUS CONTROL.
 * Built with motion/react & Lucide icons.
 * Pure black monochrome styling (text-slate-900 / #0f172a) with fluid micro-interactions.
 */

"use client";

import { useState } from "react";
import type { ComponentProps } from "react";
import { motion } from "motion/react";

export type IconProps = Omit<ComponentProps<typeof motion.svg>, "size"> & {
  size?: number | string;
};

/* ------------------------------------------------------------------ */
/* 1. IconHome - Roof bounce + Door pulse                             */
/* ------------------------------------------------------------------ */
export function IconHome({ size = 16, className = "", ...props }: IconProps) {
  const [hovered, setHovered] = useState(false);
  return (
    <motion.svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      animate={hovered ? { scale: 1.12, y: -1 } : { scale: 1, y: 0 }}
      transition={{ type: "spring", stiffness: 400, damping: 17 }}
      className={`text-slate-900 ${className}`}
      {...props}
    >
      <motion.path
        d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"
        animate={hovered ? { scale: 1.02 } : { scale: 1 }}
        transition={{ duration: 0.2 }}
      />
      <motion.polyline
        points="9 22 9 12 15 12 15 22"
        animate={hovered ? { scaleY: 1.1, originY: 1 } : { scaleY: 1 }}
        transition={{ duration: 0.25 }}
      />
    </motion.svg>
  );
}

/* ------------------------------------------------------------------ */
/* 2. IconBolt (Zap) - Electric Jolt Burst                             */
/* ------------------------------------------------------------------ */
export function IconBolt({ size = 16, className = "", ...props }: IconProps) {
  const [hovered, setHovered] = useState(false);
  return (
    <motion.svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      animate={
        hovered
          ? {
              scale: [1, 1.25, 1.15],
              rotate: [0, -12, 10, 0],
            }
          : { scale: 1, rotate: 0 }
      }
      transition={{ duration: 0.4, ease: "easeInOut" }}
      className={`text-slate-900 ${className}`}
      {...props}
    >
      <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
    </motion.svg>
  );
}

/* ------------------------------------------------------------------ */
/* 3. IconShield - Security Seal Lift                                 */
/* ------------------------------------------------------------------ */
export function IconShield({ size = 16, className = "", ...props }: IconProps) {
  const [hovered, setHovered] = useState(false);
  return (
    <motion.svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      animate={hovered ? { scale: 1.15, y: -1.5 } : { scale: 1, y: 0 }}
      transition={{ type: "spring", stiffness: 450, damping: 18 }}
      className={`text-slate-900 ${className}`}
      {...props}
    >
      <path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z" />
    </motion.svg>
  );
}

/* ------------------------------------------------------------------ */
/* 4. IconCheck - Verification Success Pop                             */
/* ------------------------------------------------------------------ */
export function IconCheck({ size = 16, className = "", ...props }: IconProps) {
  const [hovered, setHovered] = useState(false);
  return (
    <motion.svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      animate={hovered ? { scale: [1, 1.25, 1.12], rotate: [0, -8, 0] } : { scale: 1, rotate: 0 }}
      transition={{ duration: 0.35, ease: "easeOut" }}
      className={`text-slate-900 ${className}`}
      {...props}
    >
      <motion.polyline
        points="20 6 9 17 4 12"
        animate={hovered ? { pathLength: [0.8, 1] } : { pathLength: 1 }}
        transition={{ duration: 0.3 }}
      />
    </motion.svg>
  );
}

/* ------------------------------------------------------------------ */
/* 5. IconDoubleCheck - Double verification check                     */
/* ------------------------------------------------------------------ */
export function IconDoubleCheck({ size = 16, className = "", ...props }: IconProps) {
  const [hovered, setHovered] = useState(false);
  return (
    <motion.svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      animate={hovered ? { scale: 1.18 } : { scale: 1 }}
      transition={{ type: "spring", stiffness: 400, damping: 15 }}
      className={`text-slate-900 ${className}`}
      {...props}
    >
      <path d="M18 6 7 17l-5-5" />
      <path d="m22 10-7.5 7.5L13 16" />
    </motion.svg>
  );
}

/* ------------------------------------------------------------------ */
/* 6. IconFlag - Ambiguity / Residual Flag Wave                       */
/* ------------------------------------------------------------------ */
export function IconFlag({ size = 16, className = "", ...props }: IconProps) {
  const [hovered, setHovered] = useState(false);
  return (
    <motion.svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      animate={
        hovered
          ? {
              rotate: [0, -12, 10, -5, 0],
              scale: 1.15,
            }
          : { rotate: 0, scale: 1 }
      }
      transition={{ duration: 0.5, ease: "easeInOut" }}
      className={`text-slate-900 ${className}`}
      {...props}
    >
      <path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z" />
      <line x1="4" x2="4" y1="22" y2="15" />
    </motion.svg>
  );
}

/* ------------------------------------------------------------------ */
/* 7. IconScale - Ledger Dry-Run Balance Tilt                         */
/* ------------------------------------------------------------------ */
export function IconScale({ size = 16, className = "", ...props }: IconProps) {
  const [hovered, setHovered] = useState(false);
  return (
    <motion.svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      animate={
        hovered
          ? {
              rotate: [0, -10, 8, -4, 0],
              scale: 1.15,
            }
          : { rotate: 0, scale: 1 }
      }
      transition={{ duration: 0.6, ease: "easeInOut" }}
      className={`text-slate-900 ${className}`}
      {...props}
    >
      <path d="m16 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z" />
      <path d="m2 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z" />
      <path d="M7 21h10" />
      <path d="M12 3v18" />
      <path d="M3 7h2c2 0 5-1 7-2 2 1 5 2 7 2h2" />
    </motion.svg>
  );
}

/* ------------------------------------------------------------------ */
/* 8. IconScroll - Audit Log Flight Recorder Unfurl                    */
/* ------------------------------------------------------------------ */
export function IconScroll({ size = 16, className = "", ...props }: IconProps) {
  const [hovered, setHovered] = useState(false);
  return (
    <motion.svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      animate={hovered ? { scale: 1.15, y: -1.5 } : { scale: 1, y: 0 }}
      transition={{ type: "spring", stiffness: 400, damping: 16 }}
      className={`text-slate-900 ${className}`}
      {...props}
    >
      <path d="M8 21h12a2 2 0 0 0 2-2v-2H10v2a2 2 0 1 1-4 0V5a2 2 0 1 0-4 0v3h4" />
      <path d="M19 17V5a2 2 0 0 0-2-2H4" />
      <path d="M15 8h-5" />
      <path d="M15 12h-5" />
    </motion.svg>
  );
}

/* ------------------------------------------------------------------ */
/* 9. IconRoute (GitFork) - Evidence Trace Node Branch                */
/* ------------------------------------------------------------------ */
export function IconRoute({ size = 16, className = "", ...props }: IconProps) {
  const [hovered, setHovered] = useState(false);
  return (
    <motion.svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      animate={hovered ? { scale: 1.18, rotate: 8 } : { scale: 1, rotate: 0 }}
      transition={{ type: "spring", stiffness: 450, damping: 16 }}
      className={`text-slate-900 ${className}`}
      {...props}
    >
      <circle cx="12" cy="18" r="3" />
      <circle cx="6" cy="6" r="3" />
      <circle cx="18" cy="6" r="3" />
      <path d="M18 9v2c0 .6-.4 1-1 1H7c-.6 0-1-.4-1-1V9" />
      <path d="M12 12v3" />
    </motion.svg>
  );
}

/* ------------------------------------------------------------------ */
/* 10. IconLayers - Duplicate / Stacking posting layers               */
/* ------------------------------------------------------------------ */
export function IconLayers({ size = 16, className = "", ...props }: IconProps) {
  const [hovered, setHovered] = useState(false);
  return (
    <motion.svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      animate={hovered ? { scale: 1.14, y: -1 } : { scale: 1, y: 0 }}
      transition={{ type: "spring", stiffness: 420, damping: 16 }}
      className={`text-slate-900 ${className}`}
      {...props}
    >
      <path d="m12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z" />
      <path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65" />
      <path d="m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65" />
    </motion.svg>
  );
}

/* ------------------------------------------------------------------ */
/* 11. IconArrowUp - Command Submit Launch                            */
/* ------------------------------------------------------------------ */
export function IconArrowUp({ size = 16, className = "", ...props }: IconProps) {
  const [hovered, setHovered] = useState(false);
  return (
    <motion.svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      animate={hovered ? { y: -2, scale: 1.15 } : { y: 0, scale: 1 }}
      transition={{ type: "spring", stiffness: 500, damping: 15 }}
      className={`text-slate-900 ${className}`}
      {...props}
    >
      <path d="m5 12 7-7 7 7" />
      <path d="M12 19V5" />
    </motion.svg>
  );
}

/* ------------------------------------------------------------------ */
/* 12. IconPlus - Action expand                                       */
/* ------------------------------------------------------------------ */
export function IconPlus({ size = 16, className = "", ...props }: IconProps) {
  const [hovered, setHovered] = useState(false);
  return (
    <motion.svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      animate={hovered ? { rotate: 90, scale: 1.18 } : { rotate: 0, scale: 1 }}
      transition={{ type: "spring", stiffness: 450, damping: 17 }}
      className={`text-slate-900 ${className}`}
      {...props}
    >
      <path d="M5 12h14" />
      <path d="M12 5v14" />
    </motion.svg>
  );
}

/* ------------------------------------------------------------------ */
/* 13. IconMic - Voice Controller Wave                                */
/* ------------------------------------------------------------------ */
export function IconMic({ size = 16, className = "", ...props }: IconProps) {
  const [hovered, setHovered] = useState(false);
  return (
    <motion.svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      animate={hovered ? { scale: [1, 1.2, 1.12], y: -1 } : { scale: 1, y: 0 }}
      transition={{ duration: 0.35 }}
      className={`text-slate-900 ${className}`}
      {...props}
    >
      <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
      <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
      <line x1="12" x2="12" y1="19" y2="22" />
    </motion.svg>
  );
}

/* ------------------------------------------------------------------ */
/* 14. IconPlug - Dataset Connector Snap                              */
/* ------------------------------------------------------------------ */
export function IconPlug({ size = 16, className = "", ...props }: IconProps) {
  const [hovered, setHovered] = useState(false);
  return (
    <motion.svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      animate={hovered ? { scale: 1.15, rotate: -10 } : { scale: 1, rotate: 0 }}
      transition={{ type: "spring", stiffness: 450, damping: 16 }}
      className={`text-slate-900 ${className}`}
      {...props}
    >
      <path d="M12 22v-5" />
      <path d="M9 8V2" />
      <path d="M15 8V2" />
      <path d="M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z" />
    </motion.svg>
  );
}

/* ------------------------------------------------------------------ */
/* 15. IconSparkles - AI Investigation Shimmer                        */
/* ------------------------------------------------------------------ */
export function IconSparkles({ size = 16, className = "", ...props }: IconProps) {
  const [hovered, setHovered] = useState(false);
  return (
    <motion.svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      animate={
        hovered
          ? {
              scale: [1, 1.25, 1.15],
              rotate: [0, 15, -10, 0],
            }
          : { scale: 1, rotate: 0 }
      }
      transition={{ duration: 0.45 }}
      className={`text-slate-900 ${className}`}
      {...props}
    >
      <path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z" />
      <path d="M20 3v4" />
      <path d="M22 5h-4" />
      <path d="M4 17v2" />
      <path d="M5 18H3" />
    </motion.svg>
  );
}

/* ------------------------------------------------------------------ */
/* 16. IconSearch - Inspection Zoom                                   */
/* ------------------------------------------------------------------ */
export function IconSearch({ size = 16, className = "", ...props }: IconProps) {
  const [hovered, setHovered] = useState(false);
  return (
    <motion.svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      animate={hovered ? { scale: 1.15, rotate: -8 } : { scale: 1, rotate: 0 }}
      transition={{ type: "spring", stiffness: 450, damping: 16 }}
      className={`text-slate-900 ${className}`}
      {...props}
    >
      <circle cx="11" cy="11" r="8" />
      <path d="m21 21-4.3-4.3" />
    </motion.svg>
  );
}

/* ------------------------------------------------------------------ */
/* 17. IconSidebar - Panel Collapse Toggle                            */
/* ------------------------------------------------------------------ */
export function IconSidebar({ size = 16, className = "", ...props }: IconProps) {
  const [hovered, setHovered] = useState(false);
  return (
    <motion.svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      animate={hovered ? { scale: 1.15 } : { scale: 1 }}
      transition={{ type: "spring", stiffness: 450, damping: 17 }}
      className={`text-slate-900 ${className}`}
      {...props}
    >
      <rect width="18" height="18" x="3" y="3" rx="2" />
      <path d="M9 3v18" />
    </motion.svg>
  );
}

/* ------------------------------------------------------------------ */
/* 18. IconPresentation - Deck Slide Pop                              */
/* ------------------------------------------------------------------ */
export function IconPresentation({ size = 16, className = "", ...props }: IconProps) {
  const [hovered, setHovered] = useState(false);
  return (
    <motion.svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      animate={hovered ? { scale: 1.15, y: -1 } : { scale: 1, y: 0 }}
      transition={{ type: "spring", stiffness: 450, damping: 16 }}
      className={`text-slate-900 ${className}`}
      {...props}
    >
      <path d="M2 3h20" />
      <path d="M21 3v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V3" />
      <path d="m7 21 5-5 5 5" />
    </motion.svg>
  );
}

/* ------------------------------------------------------------------ */
/* 19. IconBookOpen - Documentation Open Flip                         */
/* ------------------------------------------------------------------ */
export function IconBookOpen({ size = 16, className = "", ...props }: IconProps) {
  const [hovered, setHovered] = useState(false);
  return (
    <motion.svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      animate={hovered ? { scale: 1.15 } : { scale: 1 }}
      transition={{ type: "spring", stiffness: 450, damping: 17 }}
      className={`text-slate-900 ${className}`}
      {...props}
    >
      <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" />
      <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" />
    </motion.svg>
  );
}

/* ------------------------------------------------------------------ */
/* 20. IconActivity - Telemetry Pulse Wave                            */
/* ------------------------------------------------------------------ */
export function IconActivity({ size = 16, className = "", ...props }: IconProps) {
  const [hovered, setHovered] = useState(false);
  return (
    <motion.svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      animate={hovered ? { scale: [1, 1.25, 1.15] } : { scale: 1 }}
      transition={{ duration: 0.35 }}
      className={`text-slate-900 ${className}`}
      {...props}
    >
      <path d="M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.48 12H2" />
    </motion.svg>
  );
}

/* ------------------------------------------------------------------ */
/* 21. IconChevronDown / IconChevronUp                                */
/* ------------------------------------------------------------------ */
export function IconChevronDown({ size = 16, className = "", ...props }: IconProps) {
  return (
    <motion.svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`text-slate-900 ${className}`}
      {...props}
    >
      <path d="m6 9 6 6 6-6" />
    </motion.svg>
  );
}

export function IconChevronUp({ size = 16, className = "", ...props }: IconProps) {
  return (
    <motion.svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`text-slate-900 ${className}`}
      {...props}
    >
      <path d="m18 15-6-6-6 6" />
    </motion.svg>
  );
}

/* ------------------------------------------------------------------ */
/* 22. IconCornerUpLeft / IconClock / IconQuestion / IconX / IconCopy */
/* ------------------------------------------------------------------ */
export function IconCornerUpLeft({ size = 16, className = "", ...props }: IconProps) {
  return (
    <motion.svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      whileHover={{ x: -2 }}
      className={`text-slate-900 ${className}`}
      {...props}
    >
      <polyline points="9 14 4 9 9 4" />
      <path d="M20 20v-7a4 4 0 0 0-4-4H4" />
    </motion.svg>
  );
}

export function IconClock({ size = 16, className = "", ...props }: IconProps) {
  return (
    <motion.svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      whileHover={{ rotate: 45 }}
      transition={{ duration: 0.3 }}
      className={`text-slate-900 ${className}`}
      {...props}
    >
      <circle cx="12" cy="12" r="10" />
      <polyline points="12 6 12 12 16 14" />
    </motion.svg>
  );
}

export function IconQuestion({ size = 16, className = "", ...props }: IconProps) {
  return (
    <motion.svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      whileHover={{ rotate: 15, scale: 1.15 }}
      transition={{ duration: 0.25 }}
      className={`text-slate-900 ${className}`}
      {...props}
    >
      <circle cx="12" cy="12" r="10" />
      <path d="9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" />
      <path d="12 17h.01" />
    </motion.svg>
  );
}

export function IconX({ size = 16, className = "", ...props }: IconProps) {
  return (
    <motion.svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      whileHover={{ rotate: 90, scale: 1.15 }}
      transition={{ duration: 0.2 }}
      className={`text-slate-900 ${className}`}
      {...props}
    >
      <path d="M18 6 6 18" />
      <path d="m6 6 12 12" />
    </motion.svg>
  );
}

export function IconCopy({ size = 16, className = "", ...props }: IconProps) {
  return (
    <motion.svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      whileHover={{ scale: 1.15 }}
      className={`text-slate-900 ${className}`}
      {...props}
    >
      <rect width="14" height="14" x="8" y="8" rx="2" ry="2" />
      <path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" />
    </motion.svg>
  );
}

export function IconRefresh({ size = 16, className = "", ...props }: IconProps) {
  return (
    <motion.svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      whileHover={{ rotate: -180 }}
      transition={{ duration: 0.5, ease: "easeInOut" }}
      className={`text-slate-900 ${className}`}
      {...props}
    >
      <path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" />
      <path d="M21 3v5h-5" />
      <path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" />
      <path d="M8 16H3v5" />
    </motion.svg>
  );
}

export function IconRazorpay({ size = 16, className = "", ...props }: IconProps) {
  return (
    <motion.svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="currentColor"
      whileHover={{ scale: 1.1, rotate: -4 }}
      transition={{ type: "spring", stiffness: 400, damping: 20 }}
      className={`text-slate-900 ${className}`}
      {...props}
    >
      <path d="M14.52 2L6 14.5h5.18L8.74 22 20 8.5h-5.48L14.52 2z" />
    </motion.svg>
  );
}
