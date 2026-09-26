/** A small meter with `used / allowed` printed inside it, turning red once
 * the cap is hit. The fill is a translucent tint so the count stays legible
 * over both the filled and empty parts. `suffix` is appended to the count
 * (e.g. "tokens today"); size and width come from `className`. */
export default function UsageMeter({
  label,
  used,
  allowed,
  suffix,
  className = "h-4 w-full",
}: {
  label: string;
  used: number;
  allowed: number;
  suffix?: string;
  className?: string;
}) {
  const exhausted = used >= allowed;
  const pct = allowed > 0 ? Math.min(100, (used / allowed) * 100) : 100;
  return (
    <div
      role="meter"
      aria-label={label}
      aria-valuenow={used}
      aria-valuemin={0}
      aria-valuemax={allowed}
      className={`relative overflow-hidden rounded-full bg-base-200 ${className}`}
    >
      <div
        className={`absolute inset-y-0 left-0 ${exhausted ? "bg-error/40" : "bg-primary/30"}`}
        style={{ width: `${pct}%` }}
      />
      <span
        className={`relative flex h-full items-center justify-center whitespace-nowrap px-2 text-[10px] leading-none tabular-nums ${
          exhausted ? "text-error font-semibold" : ""
        }`}
      >
        {used.toLocaleString()} / {allowed.toLocaleString()}
        {suffix && ` ${suffix}`}
      </span>
    </div>
  );
}
