/** Zero-dependency SVG sparkline. Good enough for M1; upgrade to recharts in M2+ if needed. */
import { useMemo } from "react";

interface Props {
  points: number[];
  width?: number;
  height?: number;
  stroke?: string;
}

export function Sparkline({ points, width = 180, height = 40, stroke = "currentColor" }: Props) {
  const path = useMemo(() => {
    if (points.length === 0) return "";
    const min = Math.min(...points);
    const max = Math.max(...points);
    const range = max - min || 1;
    const stepX = points.length > 1 ? width / (points.length - 1) : 0;
    return points
      .map((v, i) => {
        const x = i * stepX;
        const y = height - ((v - min) / range) * height;
        return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ");
  }, [points, width, height]);

  if (points.length === 0) {
    return <div className="text-[10px] text-muted-foreground italic">no points</div>;
  }

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className="text-primary">
      <path d={path} fill="none" stroke={stroke} strokeWidth={1.5} />
    </svg>
  );
}
