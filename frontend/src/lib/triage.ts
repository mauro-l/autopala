/**
 * Owner triage state for offers: which ones were seen and which ones were
 * saved. Pure and import-free so it can be checked with plain `node` — this
 * project has no frontend test runner.
 *
 * The database stores timestamps (`seen_at`, `saved_at`) so the moment is
 * preserved; the UI only needs the boolean, and `null` means "not set".
 */

export type JobFlags = {
  seen: boolean;
  saved: boolean;
};

export type FlagMap = Record<string, JobFlags>;

export type FlagField = "seen" | "saved";

/** One shared constant, so an unflagged read never allocates. */
export const EMPTY_FLAGS: JobFlags = { seen: false, saved: false };

export type JobStateRow = {
  job_id: string;
  seen_at: string | null;
  saved_at: string | null;
};

/**
 * Turns the `job_state` rows read for the signed-in owner into the flag map.
 * Rows that carry no flag are left out: the map is a set of exceptions, not a
 * mirror of the jobs table, so `flagsOf` stays total for everything else.
 */
export function mergeState(rows: readonly JobStateRow[] | null | undefined): FlagMap {
  const map: FlagMap = {};
  for (const row of rows ?? []) {
    const seen = Boolean(row.seen_at);
    const saved = Boolean(row.saved_at);
    if (!seen && !saved) continue;
    map[row.job_id] = { seen, saved };
  }
  return map;
}

/** Flags for one offer. Unknown offers read as unflagged, never `undefined`. */
export function flagsOf(map: FlagMap, jobId: string): JobFlags {
  return map[jobId] ?? EMPTY_FLAGS;
}

/**
 * Returns a new map with one flag set. An entry that ends up carrying neither
 * flag is dropped, so the map stays small and `mergeState`'s shape is stable.
 */
export function setFlag(
  map: FlagMap,
  jobId: string,
  field: FlagField,
  value: boolean,
): FlagMap {
  const current = map[jobId] ?? EMPTY_FLAGS;
  const next: JobFlags = { ...current, [field]: value };
  const copy: FlagMap = { ...map };
  if (!next.seen && !next.saved) {
    delete copy[jobId];
  } else {
    copy[jobId] = next;
  }
  return copy;
}

/**
 * Rolls one flag back to the last value the DATABASE is known to hold.
 *
 * Reverting to the pre-tap value is wrong: with two fast taps on the same flag
 * where both writes fail, the second rollback would restore a value that only
 * ever existed optimistically, and the screen would claim a flag nobody stored.
 */
export function revertFlag(
  current: FlagMap,
  persisted: FlagMap,
  jobId: string,
  field: FlagField,
): FlagMap {
  return setFlag(current, jobId, field, flagsOf(persisted, jobId)[field]);
}

/** How many offers carry each flag, for the filter chips. */
export function countFlags(map: FlagMap): { seen: number; saved: number } {
  let seen = 0;
  let saved = 0;
  for (const flags of Object.values(map)) {
    if (flags.seen) seen += 1;
    if (flags.saved) saved += 1;
  }
  return { seen, saved };
}
