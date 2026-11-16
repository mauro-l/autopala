// The seen/saved toggle state machine, modelled over the real triage functions.
// Run: `npm run check:logic`
//
// There is no React test runner here, so this mirrors the component's
// toggle/loadState logic rather than rendering it. The rules it pins are the
// ones that broke: roll back to what was PERSISTED, never to the pre-tap value,
// and never run two writes for the same flag at once.
import assert from "node:assert/strict";
import { flagsOf, mergeState, revertFlag, setFlag } from "../../src/lib/triage.ts";

function board(rows) {
  let flags = mergeState(rows);
  let persisted = flags;
  let pending = new Set();
  const db = {};
  for (const row of rows) db[row.job_id] = { seen: !!row.seen_at, saved: !!row.saved_at };

  const pend = (key, on) => {
    const next = new Set(pending);
    if (on) next.add(key);
    else next.delete(key);
    pending = next;
  };

  return {
    async toggle(id, field, upsert) {
      const key = `${id}:${field}`;
      if (pending.has(key)) return { dropped: true };
      const next = !flagsOf(flags, id)[field];
      pend(key, true);
      flags = setFlag(flags, id, field, next);
      const result = await upsert(next);
      if (result.error) {
        flags = revertFlag(flags, persisted, id, field);
      } else {
        persisted = setFlag(persisted, id, field, next);
        db[id] = { ...(db[id] ?? { seen: false, saved: false }), [field]: next };
      }
      pend(key, false);
      return { dropped: false };
    },
    async loadState(rows, gate) {
      await gate;
      if (pending.size) return "skipped";
      flags = mergeState(rows);
      persisted = flags;
      return "applied";
    },
    ui: (id, field) => flagsOf(flags, id)[field],
    db: (id, field) => db[id]?.[field] ?? false,
  };
}

const fail = () => Promise.resolve({ error: { message: "boom" } });
const ok = () => Promise.resolve({ error: null });
const deferred = () => {
  let resolve;
  const promise = new Promise((res) => (resolve = res));
  return { promise, resolve };
};

// B1: two taps in the same tick, both writes fail. The second write must be
// dropped, and the screen must not claim a flag the database never stored.
{
  const b = board([]);
  const first = deferred();
  const second = deferred();
  const t1 = b.toggle("j", "seen", () => first.promise);
  const t2 = await b.toggle("j", "seen", () => second.promise);
  first.resolve({ error: { message: "boom" } });
  await t1;
  assert.equal(t2.dropped, true, "a same-tick second tap must not start another write");
  assert.equal(b.ui("j", "seen"), false);
  assert.equal(b.db("j", "seen"), false);
}

// A tap while the write is in flight is dropped; a later successful write lands.
{
  const b = board([]);
  const inFlight = deferred();
  const t1 = b.toggle("j", "seen", () => inFlight.promise);
  const t2 = await b.toggle("j", "seen", ok);
  inFlight.resolve({ error: { message: "boom" } });
  await t1;
  assert.equal(t2.dropped, true);
  await b.toggle("j", "seen", ok);
  assert.equal(b.ui("j", "seen"), true);
  assert.equal(b.db("j", "seen"), true);
}

// A rollback must leave the other flag alone.
{
  const b = board([{ job_id: "k", seen_at: null, saved_at: "2026-11-16T10:00:00Z" }]);
  await b.toggle("k", "seen", fail);
  assert.deepEqual([b.ui("k", "seen"), b.ui("k", "saved")], [false, true]);
}

// A response that arrives while a write is still pending must not be applied.
{
  const stale = [{ job_id: "r", seen_at: "2026-11-16T09:00:00Z", saved_at: null }];
  const b = board(stale);
  const gate = deferred();
  const inFlight = deferred();
  const read = b.loadState(stale, gate.promise);
  // Started, not awaited: the toggle parks on the in-flight write on purpose.
  const write = b.toggle("r", "seen", () => inFlight.promise);
  gate.resolve();
  assert.equal(await read, "skipped", "a read must not clobber an in-flight write");
  inFlight.resolve({ error: { message: "boom" } });
  await write;
}

console.log("toggle: ok");
