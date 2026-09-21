// Adapted from assets/open-health-data-platform-mark.svg, recolored to the
// site's own daisyUI theme tokens (src/index.css) instead of the brand's
// fixed blue/teal hex values, so the mark stays on-palette across themes
// (including ohdp-dark) instead of carrying its own separate palette.
export default function LogoMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 512 512"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      role="img"
      aria-label="Open Health Data Platform"
    >
      <defs>
        <linearGradient id="ohdpRingGradient" x1="90" y1="80" x2="420" y2="430" gradientUnits="userSpaceOnUse">
          <stop stopColor="var(--color-secondary)" />
          <stop offset="0.55" stopColor="color-mix(in oklch, var(--color-secondary), var(--color-primary))" />
          <stop offset="1" stopColor="var(--color-primary)" />
        </linearGradient>
        <linearGradient id="ohdpBarGradient1" x1="180" y1="250" x2="230" y2="360" gradientUnits="userSpaceOnUse">
          <stop stopColor="var(--color-secondary)" />
          <stop offset="1" stopColor="color-mix(in oklch, var(--color-secondary), white 25%)" />
        </linearGradient>
        <linearGradient id="ohdpBarGradient2" x1="230" y1="235" x2="280" y2="360" gradientUnits="userSpaceOnUse">
          <stop stopColor="color-mix(in oklch, var(--color-primary), black 10%)" />
          <stop offset="1" stopColor="var(--color-primary)" />
        </linearGradient>
        <linearGradient id="ohdpBarGradient3" x1="280" y1="200" x2="330" y2="390" gradientUnits="userSpaceOnUse">
          <stop stopColor="color-mix(in oklch, var(--color-secondary), black 25%)" />
          <stop offset="1" stopColor="var(--color-secondary)" />
        </linearGradient>
      </defs>

      <circle cx="256" cy="256" r="196" fill="var(--color-base-100)" />

      <line
        x1="160"
        y1="340"
        x2="352"
        y2="340"
        stroke="var(--color-base-300)"
        strokeWidth="8"
        strokeLinecap="round"
        opacity="0.9"
      />

      <g opacity="0.35" stroke="var(--color-base-300)" strokeWidth="4">
        <line x1="170" y1="120" x2="342" y2="120" />
        <line x1="170" y1="170" x2="342" y2="170" />
        <line x1="170" y1="220" x2="342" y2="220" />
        <line x1="170" y1="270" x2="342" y2="270" />
        <line x1="170" y1="320" x2="342" y2="320" />
      </g>

      <circle cx="256" cy="256" r="160" stroke="url(#ohdpRingGradient)" strokeWidth="28" fill="none" />

      <rect x="170" y="256" width="48" height="84" rx="16" fill="url(#ohdpBarGradient1)" />
      <rect x="228" y="218" width="48" height="122" rx="16" fill="url(#ohdpBarGradient2)" />
      <rect x="286" y="178" width="48" height="162" rx="16" fill="url(#ohdpBarGradient3)" />
    </svg>
  );
}
