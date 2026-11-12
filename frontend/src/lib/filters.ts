import { NOTIFY_MIN, SOLID_MIN } from "./format";
import type { Offer } from "./supabase";

export type SortKey = "score" | "recent";

export type Filters = {
  query: string;
  minScore: number;
  remoteOnly: boolean;
  sort: SortKey;
};

/**
 * Filters are presentation only: they narrow the rows already fetched, so
 * changing one never costs a request and never needs a database change.
 */
export const DEFAULT_FILTERS: Filters = {
  query: "",
  minScore: 0,
  remoteOnly: false,
  sort: "score",
};

export const SCORE_FILTERS: readonly { label: string; value: number }[] = [
  { label: "Todo", value: 0 },
  { label: `${SOLID_MIN}+`, value: SOLID_MIN },
  { label: `${NOTIFY_MIN}+`, value: NOTIFY_MIN },
];

export function isRemote(offer: Offer): boolean {
  return offer.is_remote === true;
}

function timestamp(value: string | null | undefined): number {
  if (!value) return 0;
  const parsed = new Date(value).getTime();
  return Number.isNaN(parsed) ? 0 : parsed;
}

function matches(offer: Offer, filters: Filters): boolean {
  if ((offer.score ?? 0) < filters.minScore) return false;
  if (filters.remoteOnly && !isRemote(offer)) return false;
  const needle = filters.query.trim().toLowerCase();
  if (!needle) return true;
  return [offer.title, offer.company, offer.location]
    .filter((value): value is string => Boolean(value))
    .some((value) => value.toLowerCase().includes(needle));
}

export function hasActiveFilters(filters: Filters): boolean {
  return (
    filters.query.trim().length > 0 ||
    filters.minScore > 0 ||
    filters.remoteOnly ||
    filters.sort !== DEFAULT_FILTERS.sort
  );
}

/**
 * Returns a new array: the caller's state is never reordered in place, because
 * React keeps the same reference and would not re-render.
 */
export function applyFilters(offers: readonly Offer[], filters: Filters): Offer[] {
  const visible = [...offers.filter((offer) => matches(offer, filters))];
  if (filters.sort === "recent") {
    visible.sort(
      (a, b) =>
        timestamp(b.posted_at ?? b.last_seen_at) - timestamp(a.posted_at ?? a.last_seen_at),
    );
  } else {
    visible.sort(
      (a, b) =>
        (b.score ?? -1) - (a.score ?? -1) ||
        timestamp(b.last_seen_at) - timestamp(a.last_seen_at),
    );
  }
  return visible;
}

export type Overview = {
  total: number;
  notifyable: number;
  remote: number;
};

export function summarize(offers: readonly Offer[]): Overview {
  let notifyable = 0;
  let remote = 0;
  for (const offer of offers) {
    if ((offer.score ?? 0) >= NOTIFY_MIN) notifyable += 1;
    if (isRemote(offer)) remote += 1;
  }
  return { total: offers.length, notifyable, remote };
}
