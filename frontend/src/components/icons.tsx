/**
 * Bespoke stroke icon set for ARGUS CONTROL.
 * Modern, clean, crisp 1.5px/1.75px SVG icons.
 */

import type { SVGProps } from "react";

export type IconProps = SVGProps<SVGSVGElement> & { size?: number };

function base(size: number | undefined, props: IconProps): IconProps {
  return {
    width: size ?? 16,
    height: size ?? 16,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.6,
    strokeLinecap: "round",
    strokeLinejoin: "round",
    "aria-hidden": true,
    ...props,
  };
}

export function IconHome({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
      <polyline points="9 22 9 12 15 12 15 22" />
    </svg>
  );
}

export function IconKey({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <circle cx="7.5" cy="15.5" r="5.5" />
      <path d="m21 2-9.6 9.6" />
      <path d="m15.5 7.5 3 3L22 7l-3-3" />
    </svg>
  );
}

export function IconPlug({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <path d="M12 22v-5" />
      <path d="M9 8V2" />
      <path d="M15 8V2" />
      <path d="M18 8v5a6 6 0 0 1-12 0V8z" />
    </svg>
  );
}

export function IconBrain({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96.44 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 4.44-2.04" />
      <path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-4.44-2.04" />
    </svg>
  );
}

export function IconUsage({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <path d="M21 12a9 9 0 1 1-6.219-8.56" />
      <polyline points="12 7 12 12 15 15" />
    </svg>
  );
}

export function IconBilling({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <rect width="20" height="14" x="2" y="5" rx="2" />
      <line x1="2" x2="22" y1="10" y2="10" />
      <path d="M6 15h2" />
      <path d="M12 15h6" />
    </svg>
  );
}

export function IconPricing({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4Z" />
      <path d="M3 6h18" />
      <path d="M16 10a4 4 0 0 1-8 0" />
    </svg>
  );
}

export function IconChat({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z" />
    </svg>
  );
}

export function IconSpeaker({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
      <path d="M15.54 8.46a5 5 0 0 1 0 7.07" />
      <path d="M19.07 4.93a10 10 0 0 1 0 14.14" />
    </svg>
  );
}

export function IconMic({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
      <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
      <line x1="12" x2="12" y1="19" y2="22" />
    </svg>
  );
}

export function IconLanguages({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <path d="m5 8 6 6" />
      <path d="m4 14 6-6 2-3" />
      <path d="M2 5h12" />
      <path d="M7 2h1" />
      <path d="m22 22-5-10-5 10" />
      <path d="M14 18h6" />
    </svg>
  );
}

export function IconActivity({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
    </svg>
  );
}

export function IconBookOpen({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" />
      <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" />
    </svg>
  );
}

export function IconSidebar({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <rect width="18" height="18" x="3" y="3" rx="2" />
      <path d="M9 3v18" />
    </svg>
  );
}

export function IconChevronUp({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <path d="m18 15-6-6-6 6" />
    </svg>
  );
}

export function IconChevronDown({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <path d="m6 9 6 6 6-6" />
    </svg>
  );
}

export function IconLayers({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <path d="M12 3 3.5 7.5 12 12l8.5-4.5L12 3Z" />
      <path d="M3.5 12.5 12 17l8.5-4.5" />
      <path d="M3.5 17 12 21.5l8.5-4.5" />
    </svg>
  );
}

export function IconCornerUpLeft({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <path d="M9 14 4 9l5-5" />
      <path d="M4 9h10a6 6 0 0 1 6 6v5" />
    </svg>
  );
}

export function IconClock({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <circle cx="12" cy="12" r="9" />
      <polyline points="12 6 12 12 16 14" />
    </svg>
  );
}

export function IconQuestion({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <circle cx="12" cy="12" r="9" />
      <path d="M9.6 9.3a2.5 2.5 0 1 1 3.4 2.33c-.8.32-1 .93-1 1.87" />
      <path d="M12 16.8h.01" />
    </svg>
  );
}

export function IconCheck({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

export function IconDoubleCheck({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <path d="m2 12 5 5L18 6" />
      <path d="m11 15 2 2 9-9" />
    </svg>
  );
}

export function IconX({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <path d="m6 6 12 12M18 6 6 18" />
    </svg>
  );
}

export function IconFlag({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z" />
      <line x1="4" x2="4" y1="22" y2="15" />
    </svg>
  );
}

export function IconSearch({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <circle cx="11" cy="11" r="7" />
      <path d="m21 21-4.3-4.3" />
    </svg>
  );
}

export function IconBolt({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
    </svg>
  );
}

export function IconShield({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
    </svg>
  );
}

export function IconCopy({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <rect width="13" height="13" x="9" y="9" rx="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  );
}

export function IconScale({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <path d="m16 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z" />
      <path d="m2 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z" />
      <path d="M7 21h10" />
      <path d="M12 3v18" />
      <path d="M3 7h2c2 0 5-1 7-2 2 1 5 2 7 2h2" />
    </svg>
  );
}

export function IconScroll({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <path d="M8 2h8a2 2 0 0 1 2 2v14a4 4 0 0 1-4 4H6a4 4 0 0 1-4-4V6a4 4 0 0 1 4-4h2z" />
      <path d="M8 2v4a2 2 0 0 1-2 2H2" />
      <path d="M8 13h8" />
      <path d="M8 17h6" />
    </svg>
  );
}

export function IconRoute({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <circle cx="6" cy="19" r="3" />
      <path d="M9 19h8.5a3.5 3.5 0 0 0 0-7h-11a3.5 3.5 0 0 1 0-7H15" />
      <circle cx="18" cy="5" r="3" />
    </svg>
  );
}

export function IconRefresh({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
      <path d="M3 3v5h5" />
      <path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16" />
      <path d="M16 21h5v-5" />
    </svg>
  );
}

export function IconPresentation({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <path d="M2 3h20" />
      <path d="M21 3v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V3" />
      <path d="m7 21 5-5 5 5" />
    </svg>
  );
}
