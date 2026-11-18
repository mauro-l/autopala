import { firstSeenDate, offerDate } from "./dates";
import { NOTIFY_MIN, SOLID_MIN } from "./format";
import type { Offer } from "./supabase";
import { flagsOf, type FlagMap } from "./triage";

export type SortKey = "score" | "recent" | "discovered";

/**
 * Triage state filter. `unseen` is the triage queue: offers with no seen
 * timestamp. `saved` is the shortlist.
 */
export type StatusFilter = "all" | "unseen" | "saved";

export type Filters = {
  query: string;
  minScore: number;
  remoteOnly: boolean;
  status: StatusFilter;
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
  status: "all",
  sort: "score",
};

export const SCORE_FILTERS: readonly { label: string; value: number }[] = [
  { label: "Todo", value: 0 },
  { label: `${SOLID_MIN}+`, value: SOLID_MIN },
  { label: `${NOTIFY_MIN}+`, value: NOTIFY_MIN },
];

export const STATUS_FILTERS: readonly { label: string; value: StatusFilter }[] = [
  { label: "Todas", value: "all" },
  { label: "Sin ver", value: "unseen" },
  { label: "Guardadas", value: "saved" },
];

export function isRemote(offer: Offer): boolean {
  return offer.is_remote === true;
}

function timestamp(value: string | null | undefined): number {
  if (!value) return 0;
  const parsed = new Date(value).getTime();
  return Number.isNaN(parsed) ? 0 : parsed;
}

/** Everything except the status dimension, so the chips can count themselves. */
function matchesBase(offer: Offer, filters: Filters): boolean {
  if ((offer.score ?? 0) < filters.minScore) return false;
  if (filters.remoteOnly && !isRemote(offer)) return false;
  const needle = filters.query.trim().toLowerCase();
  if (!needle) return true;
  return [offer.title, offer.company, offer.location]
    .filter((value): value is string => Boolean(value))
    .some((value) => value.toLowerCase().includes(needle));
}

function matchesStatus(offer: Offer, filters: Filters, flags: FlagMap): boolean {
  if (filters.status === "all") return true;
  const own = flagsOf(flags, offer.id);
  if (filters.status === "unseen") return !own.seen;
  return own.saved;
}

export function hasActiveFilters(filters: Filters): boolean {
  return (
    filters.query.trim().length > 0 ||
    filters.minScore > 0 ||
    filters.remoteOnly ||
    filters.status !== DEFAULT_FILTERS.status ||
    filters.sort !== DEFAULT_FILTERS.sort
  );
}

/**
 * Returns a new array: the caller's state is never reordered in place, because
 * React keeps the same reference and would not re-render.
 */
export function applyFilters(
  offers: readonly Offer[],
  filters: Filters,
  flags: FlagMap,
): Offer[] {
  const visible = offers.filter(
    (offer) => matchesBase(offer, filters) && matchesStatus(offer, filters, flags),
  );
  if (filters.sort === "recent") {
    // `offerDate` is the same key the day sections group by, so a flat "most
    // recent" list and the grouped one agree. Sorting by `last_seen_at` was
    // meaningless: the hourly worker advances it for every row it re-scrapes.
    visible.sort((a, b) => offerDate(b) - offerDate(a));
  } else if (filters.sort === "discovered") {
    // Newest-first by when this app first saw each offer, independently of its
    // original publication date and the worker's frequently updated last_seen_at.
    visible.sort((a, b) => firstSeenDate(b) - firstSeenDate(a));
  } else {
    visible.sort(
      (a, b) =>
        (b.score ?? -1) - (a.score ?? -1) ||
        timestamp(b.last_seen_at) - timestamp(a.last_seen_at),
    );
  }
  return visible;
}

/**
 * Counts for the status chips, over the rows that pass every other filter, so
 * the number next to "Sin ver" matches what selecting it will show.
 */
export function statusCounts(
  offers: readonly Offer[],
  filters: Filters,
  flags: FlagMap,
): Record<StatusFilter, number> {
  let total = 0;
  let unseen = 0;
  let saved = 0;
  for (const offer of offers) {
    if (!matchesBase(offer, filters)) continue;
    total += 1;
    const own = flagsOf(flags, offer.id);
    if (!own.seen) unseen += 1;
    if (own.saved) saved += 1;
  }
  return { all: total, unseen, saved };
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
