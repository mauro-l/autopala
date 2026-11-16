// Triage flag map, including the rollback rule. Run: `npm run check:logic`
import assert from "node:assert/strict";
import { EMPTY_FLAGS, countFlags, flagsOf, mergeState, revertFlag, setFlag } from "../../src/lib/triage.ts";

// --- mergeState: rows carry timestamps, the map carries booleans
const map = mergeState([
  { job_id: "a", seen_at: "2026-11-16T10:00:00Z", saved_at: null },
  { job_id: "b", seen_at: null, saved_at: "2026-11-16T11:00:00Z" },
  { job_id: "c", seen_at: "2026-11-16T12:00:00Z", saved_at: "2026-11-16T12:00:00Z" },
  { job_id: "d", seen_at: null, saved_at: null },
]);
assert.deepEqual(map.a, { seen: true, saved: false });
assert.deepEqual(map.b, { seen: false, saved: true });
assert.deepEqual(map.c, { seen: true, saved: true });
assert.equal("d" in map, false, "a row with no flag must not occupy the map");
assert.deepEqual(mergeState([]), {});
assert.deepEqual(mergeState(null), {});

// --- flagsOf is total
assert.equal(flagsOf(map, "zzz"), EMPTY_FLAGS);
assert.deepEqual(flagsOf(map, "a"), { seen: true, saved: false });

// --- setFlag is immutable and drops entries that carry nothing
const seenB = setFlag(map, "b", "seen", true);
assert.deepEqual(seenB.b, { seen: true, saved: true });
assert.deepEqual(map.b, { seen: false, saved: true }, "setFlag must not mutate its input");
assert.notEqual(seenB, map);
assert.equal("a" in setFlag(map, "a", "seen", false), false, "clearing the last flag drops the entry");

assert.deepEqual(countFlags(map), { seen: 2, saved: 2 });
assert.deepEqual(countFlags({}), { seen: 0, saved: 0 });

// --- revertFlag targets the PERSISTED value, never the pre-tap value.
// This is the regression test for the double-tap bug: two taps on the same flag
// where both writes fail used to leave the screen claiming a flag nobody stored.
const persisted = {}; // nothing stored: the flag was never written successfully

// One failed tap on an unseen offer.
const tap1 = setFlag(persisted, "j", "seen", true);
assert.deepEqual(flagsOf(revertFlag(tap1, persisted, "j", "seen"), "j"), { seen: false, saved: false });

// Two taps, both failing. The second rollback must not restore tap1's optimism.
const tap2 = setFlag(tap1, "j", "seen", false);
assert.deepEqual(
  flagsOf(revertFlag(tap2, persisted, "j", "seen"), "j"),
  { seen: false, saved: false },
  "both writes failed, so both flags must read unset",
);

// A failure must not disturb the other flag, stored or not.
const both = setFlag(persisted, "k", "saved", true);
const optimistic = setFlag(both, "k", "seen", true);
assert.deepEqual(
  flagsOf(revertFlag(optimistic, both, "k", "seen"), "k"),
  { seen: false, saved: true },
  "reverting seen must keep the saved flag intact",
);

console.log("triage: ok");
