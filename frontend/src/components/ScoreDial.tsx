import type { CSSProperties } from "react";
import { formatScore, scoreTier } from "../lib/format";

type Props = {
  score: number | null;
};

/**
 * Total score as a ring. The percentage lives in a CSS custom property so the
 * conic gradient can be drawn by `global.css` without inline colors.
 */
export default function ScoreDial({ score }: Props) {
  const clamped = score === null ? 0 : Math.max(0, Math.min(100, score));
  const style = { "--value": `${clamped}%` } as CSSProperties;

  return (
    <div
      className="dial"
      data-tier={scoreTier(score)}
      style={style}
      role="img"
      aria-label={`Puntaje ${formatScore(score)} sobre 100`}
    >
      <span className="dial-value">{formatScore(score)}</span>
    </div>
  );
}
