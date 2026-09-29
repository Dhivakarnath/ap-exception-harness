import { cn } from "@/lib/cn";

/**
 * Minimal SVG sparkline — no chart library, just a polyline over normalized points.
 * Null values are skipped when scaling so sparse days do not flatten the line.
 */
export function Sparkline({
  points,
  className,
  width = 80,
  height = 28,
}: {
  points: Array<number | null>;
  className?: string;
  width?: number;
  height?: number;
}) {
  const values = points.filter((v): v is number => v != null);
  if (values.length < 2) return null;

  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const pad = 2;

  const coords: string[] = [];
  let placed = 0;
  points.forEach((v, i) => {
    if (v == null) return;
    const x = (i / (points.length - 1)) * width;
    const y = height - pad - ((v - min) / range) * (height - pad * 2);
    coords.push(`${x.toFixed(1)},${y.toFixed(1)}`);
    placed += 1;
  });

  if (placed < 2) return null;

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      className={cn("shrink-0 text-brand/70", className)}
      aria-hidden
    >
      <polyline
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
        points={coords.join(" ")}
      />
    </svg>
  );
}
