// Day grouping rules. Run: `npm run check:logic`
// Node >= 22.18 strips the TypeScript types, so the real module is exercised
// with no test framework and no extra dependency.
import assert from "node:assert/strict";
import {
  dayDifference,
  dayLabel,
  dayKey,
  firstSeenDate,
  formatDayDistance,
  groupByDay,
  offerDate,
} from "../../src/lib/dates.ts";

// Local dates, so the cases do not depend on the machine timezone.
const at = (y, m, d, h = 12) => new Date(y, m - 1, d, h, 0, 0, 0).getTime();
const NOW = at(2026, 11, 16);

// --- posted_at wins; first_seen_at is the only fallback
assert.equal(
  offerDate({ posted_at: "2026-11-10", first_seen_at: "2026-11-01T10:00:00Z" }),
  at(2026, 11, 10, 0),
  "posted_at must win over first_seen_at",
);
assert.equal(
  offerDate({ posted_at: null, first_seen_at: "2026-11-15T10:00:00Z" }),
  new Date("2026-11-15T10:00:00Z").getTime(),
  "a null posted_at falls back to first_seen_at",
);
assert.equal(
  offerDate({ posted_at: null, first_seen_at: null, last_seen_at: "2026-11-16T12:00:00Z" }),
  0,
  "last_seen_at must never be used: the hourly worker advances it on every scrape",
);
assert.equal(offerDate({ posted_at: "not-a-date", first_seen_at: null }), 0);
assert.equal(
  firstSeenDate({ posted_at: "2026-11-10", first_seen_at: "2026-11-16T10:00:00Z" }),
  new Date("2026-11-16T10:00:00Z").getTime(),
  "discovery date must use first_seen_at even when posted_at exists",
);

// A date-only posted_at is a calendar date, not an instant. Parsing it as UTC
// midnight would file it under Nov 15 for a Buenos Aires user (UTC-3).
assert.equal(
  dayKey(offerDate({ posted_at: "2026-11-10", first_seen_at: null })),
  "2026-11-10",
  "a date-only posted_at must keep its own local day",
);

// --- calendar-day arithmetic, not elapsed milliseconds
assert.equal(dayDifference(at(2026, 11, 16, 1), NOW), 0, "same calendar day is 0");
assert.equal(dayDifference(at(2026, 11, 15, 23), NOW), 1, "16 hours earlier is still 1 day");
assert.equal(dayDifference(at(2026, 11, 10), NOW), 6);

// --- labels
assert.equal(dayLabel(at(2026, 11, 16), NOW), "Hoy");
assert.equal(dayLabel(at(2026, 11, 15), NOW), "Ayer");
assert.equal(dayLabel(at(2026, 11, 10), NOW), "10/11", "same year drops the year");
assert.equal(dayLabel(at(2025, 12, 31), NOW), "31/12/2025", "another year keeps it");
assert.equal(dayLabel(0, NOW), "sin fecha");

// The card label uses the same calendar unit as the section header, so a card
// can never say "hoy" inside the "Ayer" section.
assert.equal(formatDayDistance(at(2026, 11, 16, 0), at(2026, 11, 16, 23)), "hoy");
assert.equal(formatDayDistance(at(2026, 11, 15, 23), at(2026, 11, 16, 0)), "ayer");
assert.equal(formatDayDistance(0, NOW), "sin fecha");

// --- grouping: newest first, undated last, in-section order preserved
const items = [
  { id: "a", posted_at: "2026-11-16", first_seen_at: null },
  { id: "b", posted_at: "2026-11-10", first_seen_at: null },
  { id: "c", posted_at: null, first_seen_at: new Date(at(2026, 11, 16)).toISOString() },
  { id: "d", posted_at: null, first_seen_at: null },
];
assert.deepEqual(
  groupByDay(items, NOW).map((group) => [group.key, group.label, group.items.map((i) => i.id)]),
  [
    ["2026-11-16", "Hoy", ["a", "c"]],
    ["2026-11-10", "10/11", ["b"]],
    ["undated", "sin fecha", ["d"]],
  ],
);
assert.deepEqual(groupByDay([], NOW), []);
const discoveryItems = [
  { id: "a", posted_at: "2026-11-10", first_seen_at: new Date(at(2026, 11, 16)).toISOString() },
  { id: "b", posted_at: "2026-11-16", first_seen_at: new Date(at(2026, 11, 15)).toISOString() },
  { id: "c", posted_at: null, first_seen_at: new Date(at(2026, 11, 16, 1)).toISOString() },
  { id: "d", posted_at: "2026-11-16", first_seen_at: null },
];
assert.deepEqual(
  groupByDay(discoveryItems, NOW, firstSeenDate).map((group) => [group.key, group.items.map((i) => i.id)]),
  [
    ["2026-11-16", ["a", "c"]],
    ["2026-11-15", ["b"]],
    ["undated", ["d"]],
  ],
  "discovery grouping must bucket by first_seen_at instead of posted_at",
);
assert.deepEqual(
  items.map((i) => i.id),
  ["a", "b", "c", "d"],
  "groupByDay must not reorder the caller's array",
);

// Two local days sharing a UTC day must not merge.
const dusk = { id: "x", posted_at: null, first_seen_at: new Date(at(2026, 11, 15, 23)).toISOString() };
const dawn = { id: "y", posted_at: null, first_seen_at: new Date(at(2026, 11, 16, 1)).toISOString() };
assert.deepEqual(
  groupByDay([dusk, dawn], NOW).map((group) => group.key),
  ["2026-11-16", "2026-11-15"],
  "local days must not merge across midnight",
);

console.log("dates: ok");
