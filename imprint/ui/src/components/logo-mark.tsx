/** The Imprint mark (site/public/logo-mark.svg): two fingerprint ridges
 * around an i, drawn in the current text colour. */
export function LogoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="2.48 4.75 59.04 54.85" className={className} aria-hidden="true">
      <path
        d="M6.08 56V34.27A25.92 25.92 0 0 1 57.92 34.27V38 M19.04 47V34.27A12.96 12.96 0 0 1 44.96 34.27V56 M32 48.09V56"
        fill="none"
        stroke="currentColor"
        strokeWidth={7.2}
        strokeLinecap="round"
      />
      <circle cx={32} cy={35.13} r={4.46} fill="currentColor" />
    </svg>
  );
}
