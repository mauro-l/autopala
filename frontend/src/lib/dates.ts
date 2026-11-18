/**
 * Day bucketing for the offer list. Self-contained on purpose: no imports, so
 * it can be checked with plain `node` (this project has no frontend test
 * runner) and so the day rules stay in one place.
 *
 * The grouping date is `posted_at` when the source gave one, otherwise
 * `first_seen_at` — the moment the offer entered the app. `last_seen_at` is
 * deliberately EXCLUDED, not used as a last resort: the hourly worker advances
 * it on every scrape, so it would file every row under "Hoy" and say nothing
 * about when the offer actually appeared.
 *
 * Every boundary is computed in LOCAL time. `posted_at` is a Postgres `date`,
 * so it arrives as `YYYY-MM-DD`; `new Date("2026-11-16")` parses that as UTC
 * midnight, which is still the 15th in Buenos Aires. A date-only value is
 * therefore pinned to local midnight instead.
 */

export type DatedOffer = {
  posted_at: string | null;
  first_seen_at?: string | null;
};

export type DayGroup<T> = {
  key: string;
  label: string;
  at: number;
  items: T[];
};

export const UNDATED_KEY = "undated";
export const UNDATED_LABEL = "sin fecha";

const DAY_MS = 86_400_000;
const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/;

/** Epoch ms for an ISO timestamp, or local midnight for a date-only string. */
function parseDate(value: string | null | undefined): number {
  if (!value) return 0;
  const dateOnly = DATE_ONLY.exec(value);
  if (dateOnly) {
    return new Date(Number(dateOnly[1]), Number(dateOnly[2]) - 1, Number(dateOnly[3])).getTime();
  }
  const parsed = new Date(value).getTime();
  return Number.isNaN(parsed) ? 0 : parsed;
}

function localMidnight(epochMs: number): number {
  const date = new Date(epochMs);
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

/** The date the list groups by. 0 means "no usable date". */
export function offerDate(offer: DatedOffer): number {
  return parseDate(offer.posted_at) || parseDate(offer.first_seen_at);
}

/** The date the app first observed an offer. 0 means "not recorded". */
export function firstSeenDate(offer: DatedOffer): number {
  return parseDate(offer.first_seen_at);
}

/** Local calendar day as `YYYY-MM-DD`, or `undated`. */
export function dayKey(epochMs: number): string {
  if (!epochMs || Number.isNaN(epochMs)) return UNDATED_KEY;
  const date = new Date(epochMs);
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

/**
 * Whole calendar days between two instants. Counting local midnights instead of
 * dividing elapsed milliseconds by 86 400 000 keeps the label right across a
 * daylight-saving shift.
 */
export function dayDifference(then: number, now: number): number {
  if (!then || Number.isNaN(then) || Number.isNaN(now)) return Number.NaN;
  return Math.round((localMidnight(now) - localMidnight(then)) / DAY_MS);
}

/** `Hoy`, `Ayer`, `dd/mm`, `dd/mm/yyyy` or `sin fecha`. */
export function dayLabel(epochMs: number, now: number = Date.now()): string {
  if (!epochMs || Number.isNaN(epochMs)) return UNDATED_LABEL;
  const days = dayDifference(epochMs, now);
  if (days === 0) return "Hoy";
  if (days === 1) return "Ayer";
  const then = new Date(epochMs);
  const day = String(then.getDate()).padStart(2, "0");
  const month = String(then.getMonth() + 1).padStart(2, "0");
  return then.getFullYear() === new Date(now).getFullYear()
    ? `${day}/${month}`
    : `${day}/${month}/${then.getFullYear()}`;
}

/**
 * `hoy`, `ayer`, `hace N días`, `hace un mes`, `hace N meses`, or `sin fecha`.
 *
 * Counted in CALENDAR days, the same unit the day sections use. Using elapsed
 * milliseconds instead makes the card contradict its own section: first seen
 * yesterday at 23:30, read today at 00:10, is 40 minutes old ("hoy") but sits
 * under "Ayer".
 */
export function formatDayDistance(epochMs: number, now: number = Date.now()): string {
  if (!epochMs || Number.isNaN(epochMs)) return UNDATED_LABEL;
  const days = dayDifference(epochMs, now);
  if (days <= 0) return "hoy";
  if (days === 1) return "ayer";
  if (days < 30) return `hace ${days} días`;
  const months = Math.floor(days / 30);
  return months === 1 ? "hace un mes" : `hace ${months} meses`;
}

/**
 * Splits already-ordered offers into day sections, newest day first, undated
 * last. The relative order inside a section is the caller's (score or recency),
 * and the input array is never mutated.
 */
export function groupByDay<T extends DatedOffer>(
  offers: readonly T[],
  now: number = Date.now(),
  dateOf: (offer: T) => number = offerDate,
): DayGroup<T>[] {
  const buckets = new Map<string, DayGroup<T>>();

  for (const offer of offers) {
    const at = dateOf(offer);
    const key = dayKey(at);
    const existing = buckets.get(key);
    if (existing) {
      existing.items.push(offer);
      continue;
    }
    buckets.set(key, { key, label: dayLabel(at, now), at, items: [offer] });
  }

  // `at` is always inside its own bucket's day, so ordering buckets by it orders
  // the days; the undated bucket keeps at = 0 and sinks to the end.
  return [...buckets.values()].sort((a, b) => b.at - a.at);
}
