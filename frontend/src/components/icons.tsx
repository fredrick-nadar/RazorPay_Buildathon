/**
 * Bespoke stroke icon set for ARGUS CONTROL.
 * Single visual language: 1.5px stroke, round caps, 16/20px grid.
 */

import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement> & { size?: number };

function base(size: number | undefined, props: IconProps): IconProps {
  return {
    width: size ?? 16,
    height: size ?? 16,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.5,
    strokeLinecap: "round",
    strokeLinejoin: "round",
    "aria-hidden": true,
    ...props,
  };
}

export function IconAperture({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 3v6l4.5 7.8" />
      <path d="M12 9 4.2 13.5" />
      <path d="m16.5 16.8-4.5-1.8-8.1 1.5" />
      <path d="M12 3v18" opacity={0} />
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
      <path d="M12 7v5l3.5 2" />
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
      <path d="m4.5 12.5 5 5L19.5 7" />
    </svg>
  );
}

export function IconDoubleCheck({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <path d="m2.5 13 4.5 4.5L15.5 8" />
      <path d="m11 15.5 1.5 2L21.5 8" />
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
      <path d="M5 21V4.5C8 3 10.5 3 12 4.5s4 1.5 7 0V15c-3 1.5-5.5 1.5-7 0s-4-1.5-7 0" />
    </svg>
  );
}

export function IconSearch({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <circle cx="11" cy="11" r="6.5" />
      <path d="m16 16 4.5 4.5" />
    </svg>
  );
}

export function IconBolt({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <path d="M13 2 4.5 13.5H11L9.5 22 19 10h-6.5L13 2Z" />
    </svg>
  );
}

export function IconShield({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <path d="M12 2.5 4.5 5.5v6c0 4.7 3.2 8.1 7.5 10 4.3-1.9 7.5-5.3 7.5-10v-6L12 2.5Z" />
      <path d="m9 11.5 2.2 2.3L15.5 9" />
    </svg>
  );
}

export function IconCopy({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <rect x="9" y="9" width="12" height="12" rx="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  );
}

export function IconScale({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <path d="M12 3v18M4 7h16" />
      <path d="M6.5 7 3 14h7L6.5 7ZM17.5 7 14 14h7l-3.5-7Z" />
      <path d="M8 21h8" />
    </svg>
  );
}

export function IconScroll({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <path d="M19 17V5a2 2 0 0 0-2-2H7a2 2 0 0 0-2 2v1" />
      <path d="M19 17a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5" />
      <path d="M5 17a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2" opacity={0} />
      <path d="M9 7h6M9 11h6" />
    </svg>
  );
}

export function IconRoute({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <circle cx="6" cy="19" r="2.5" />
      <circle cx="18" cy="5" r="2.5" />
      <path d="M15.5 5H10a3.5 3.5 0 0 0 0 7h4a3.5 3.5 0 0 1 0 7H8.5" />
    </svg>
  );
}

export function IconRefresh({ size, ...props }: IconProps) {
  return (
    <svg {...base(size, props)}>
      <path d="M20.5 12a8.5 8.5 0 1 1-2.5-6" />
      <path d="M20.5 3.5V8H16" />
    </svg>
  );
}
