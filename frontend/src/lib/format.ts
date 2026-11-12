export type ScoreTier = "high" | "mid" | "low";

/** Mirrors the backend thresholds: only the top tier triggers a notification. */
export const NOTIFY_MIN = 85;
export const SOLID_MIN = 70;

export function scoreTier(score: number | null): ScoreTier {
  if (score === null) return "low";
  if (score >= NOTIFY_MIN) return "high";
  if (score >= SOLID_MIN) return "mid";
  return "low";
}

export function formatScore(score: number | null): string {
  return score === null ? "—" : String(score);
}

export function formatAge(value: string | null): string {
  if (!value) return "sin fecha";
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return "sin fecha";
  const days = Math.floor((Date.now() - then) / 86_400_000);
  if (days <= 0) return "hoy";
  if (days === 1) return "ayer";
  if (days < 30) return `hace ${days} días`;
  const months = Math.floor(days / 30);
  return months === 1 ? "hace un mes" : `hace ${months} meses`;
}

export type ScorePartKey = "stack" | "seniority" | "experience" | "location" | "recency";

export type ScorePart = {
  key: ScorePartKey;
  label: string;
  max: number;
};

/**
 * Budgets duplicate `backend/app/scoring.py` on purpose: they are the display
 * scale, and the UI must not require a backend round trip to draw a bar.
 */
export const SCORE_PARTS: readonly ScorePart[] = [
  { key: "stack", label: "Stack", max: 40 },
  { key: "seniority", label: "Nivel", max: 25 },
  { key: "experience", label: "Exp.", max: 15 },
  { key: "location", label: "Ubicación", max: 10 },
  { key: "recency", label: "Recencia", max: 10 },
];

export function partPercent(value: number | null | undefined, max: number): number {
  if (!value || value <= 0) return 0;
  return Math.min(100, Math.round((value / max) * 100));
}
