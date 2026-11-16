// Filters, exercised against the REAL src/lib/filters.ts. Run: `npm run check:logic`
//
// filters.ts imports "./format" and "./dates" without extensions, which plain
// Node cannot resolve, so the module is bundled first with the esbuild already
// present in devDependencies. The bundle goes to the OS temp dir: nothing is
// written inside the repository.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const frontend = new URL("../../", import.meta.url).pathname;
const outDir = mkdtempSync(join(tmpdir(), "pala-filters-"));
const outFile = join(outDir, "filters.mjs");

execFileSync(
  join(frontend, "node_modules", ".bin", "esbuild"),
  [
    join(frontend, "src", "lib", "filters.ts"),
    "--bundle",
    "--format=esm",
    "--platform=neutral",
    `--outfile=${outFile}`,
    "--log-level=warning",
  ],
  { stdio: "inherit" },
);

const { DEFAULT_FILTERS, applyFilters, hasActiveFilters, statusCounts, summarize } = await import(
  pathToFileURL(outFile).href
);

const offer = (id, extra = {}) => ({
  id,
  title: `Puesto ${id}`,
  company: "Acme",
  location: "",
  is_remote: false,
  score: 50,
  score_breakdown: null,
  posted_at: null,
  first_seen_at: null,
  last_seen_at: null,
  job_sources: [],
  ...extra,
});

const offers = [
  offer("a", { score: 90, is_remote: true, first_seen_at: "2026-11-16T10:00:00Z" }),
  offer("b", { score: 75, first_seen_at: "2026-11-15T10:00:00Z" }),
  offer("c", { score: 60, title: "Backend", company: "Globex", first_seen_at: "2026-11-10T10:00:00Z" }),
  offer("d", { score: 40 }),
];

const flags = { a: { seen: true, saved: true }, c: { seen: true, saved: false } };
const ids = (list) => list.map((item) => item.id);

// --- status dimension
assert.deepEqual(ids(applyFilters(offers, DEFAULT_FILTERS, flags)), ["a", "b", "c", "d"]);
assert.deepEqual(
  ids(applyFilters(offers, { ...DEFAULT_FILTERS, status: "unseen" }, flags)),
  ["b", "d"],
  "unseen must exclude every offer with a seen timestamp",
);
assert.deepEqual(ids(applyFilters(offers, { ...DEFAULT_FILTERS, status: "saved" }, flags)), ["a"]);
assert.deepEqual(ids(applyFilters(offers, { ...DEFAULT_FILTERS, status: "saved" }, {})), []);

// --- chip counts are taken over the rows passing the OTHER filters
assert.deepEqual(statusCounts(offers, DEFAULT_FILTERS, flags), { all: 4, unseen: 2, saved: 1 });
assert.deepEqual(
  statusCounts(offers, { ...DEFAULT_FILTERS, remoteOnly: true }, flags),
  { all: 1, unseen: 0, saved: 1 },
  "counts must respect the remote-only filter too",
);

// --- the pre-existing dimensions still behave
assert.deepEqual(ids(applyFilters(offers, { ...DEFAULT_FILTERS, minScore: 70 }, flags)), ["a", "b"]);
assert.deepEqual(ids(applyFilters(offers, { ...DEFAULT_FILTERS, remoteOnly: true }, flags)), ["a"]);
assert.deepEqual(ids(applyFilters(offers, { ...DEFAULT_FILTERS, query: "globex" }, flags)), ["c"]);
assert.deepEqual(ids(applyFilters(offers, { ...DEFAULT_FILTERS, query: "  BACKEND " }, flags)), ["c"]);
assert.deepEqual(summarize(offers), { total: 4, notifyable: 1, remote: 1 });

// --- sorting
assert.deepEqual(ids(applyFilters(offers, DEFAULT_FILTERS, flags)), ["a", "b", "c", "d"]);
assert.deepEqual(ids(applyFilters(offers, { ...DEFAULT_FILTERS, sort: "recent" }, flags)), [
  "a",
  "b",
  "c",
  "d",
]);
assert.deepEqual(
  ids(applyFilters([offers[3], offers[1], offers[0]], { ...DEFAULT_FILTERS, sort: "recent" }, flags)),
  ["a", "b", "d"],
  "recent sorts by the offer's own date, not by input order",
);

// --- the input array is never reordered in place
const input = [offers[3], offers[0], offers[1], offers[2]];
const snapshot = [...input];
applyFilters(input, DEFAULT_FILTERS, flags);
assert.deepEqual(input, snapshot);

// --- active-filter detection
assert.equal(hasActiveFilters(DEFAULT_FILTERS), false);
assert.equal(hasActiveFilters({ ...DEFAULT_FILTERS, status: "unseen" }), true);
assert.equal(hasActiveFilters({ ...DEFAULT_FILTERS, sort: "recent" }), true);
assert.equal(hasActiveFilters({ ...DEFAULT_FILTERS, query: "   " }), false);

console.log("filters: ok");
