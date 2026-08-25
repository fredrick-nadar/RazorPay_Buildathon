/** Shared arrow glyph for landing buttons and loop chips. */

export function LandingArrow({ className = "" }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 20 20"
      aria-hidden="true"
      className={className}
    >
      <path
        d="M4 10h10.2M10.4 5.6 15.2 10l-4.8 4.4"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
