import { useCallback, useEffect, useRef, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import { clearPin, clearUnlocked, hasPin, isUnlocked, markUnlocked } from "../lib/pin";
import {
  isConfigured,
  JOB_STATE_SELECT,
  OFFER_SELECT,
  supabase,
  type Offer,
} from "../lib/supabase";
import {
  flagsOf,
  mergeState,
  revertFlag,
  setFlag,
  type FlagField,
  type FlagMap,
  type JobStateRow,
} from "../lib/triage";
import ConfigMissing from "./ConfigMissing";
import Dashboard, { type ClaimState } from "./Dashboard";
import LoginView from "./LoginView";
import PinLock from "./PinLock";

/** One column at a time, so an upsert cannot clear the other flag. */
type JobStatePayload = {
  user_id: string;
  job_id: string;
  seen_at?: string | null;
  saved_at?: string | null;
};

/**
 * Container: owns authentication, the ownership claim and the single read of
 * offers. Everything visual lives in `Dashboard` and its children.
 */
export default function OfferBoard() {
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  const [claim, setClaim] = useState<ClaimState>("idle");
  const [offers, setOffers] = useState<Offer[]>([]);
  const [flags, setFlags] = useState<FlagMap>({});
  const [stateError, setStateError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  // The optimistic map is also read outside render (two toggles in one tick),
  // so the ref is the source of truth and `setFlags` only mirrors it.
  const flagsRef = useRef<FlagMap>({});
  // What the database is known to hold. A failed write rolls back to this, not
  // to the pre-tap value, so a rollback can never restore optimism.
  const persistedRef = useRef<FlagMap>({});
  // `${jobId}:${field}` of the writes in flight. One write per flag at a time:
  // two concurrent upserts for the same flag can commit out of order and leave
  // the database disagreeing with the screen with no error to show.
  const pendingRef = useRef<Set<string>>(new Set());
  const [pending, setPending] = useState<ReadonlySet<string>>(new Set());
  // Bumped by the lock timer and by an unlock, so the gate re-evaluates.
  const [, setTick] = useState(0);

  const userId = session?.user.id ?? null;

  /** Marks one `${jobId}:${field}` write as in flight, and mirrors it for the UI. */
  const markPending = useCallback((key: string, on: boolean) => {
    const next = new Set(pendingRef.current);
    if (on) next.add(key);
    else next.delete(key);
    pendingRef.current = next;
    setPending(next);
  }, []);

  // Re-lock while the app stays open: the stamp expires on its own.
  useEffect(() => {
    if (!userId) return;
    const check = () => setTick((value) => value + 1);
    const timer = window.setInterval(check, 30_000);
    const onVisibility = () => {
      if (document.visibilityState === "visible") check();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [userId]);

  useEffect(() => {
    const client = supabase;
    if (!client) return;
    let active = true;

    void client.auth.getSession().then(({ data }) => {
      if (active) setSession(data.session);
    });

    const { data } = client.auth.onAuthStateChange((_event, next) => {
      setSession(next);
    });

    return () => {
      active = false;
      data.subscription.unsubscribe();
    };
  }, []);

  const loadOffers = useCallback(async () => {
    const client = supabase;
    if (!client) return;
    setLoading(true);
    setLoadError(null);

    const { data, error } = await client
      .from("jobs")
      .select(OFFER_SELECT)
      .not("score", "is", null)
      .order("score", { ascending: false })
      .order("last_seen_at", { ascending: false })
      .limit(100);

    if (error) {
      console.error("offers: select failed", error);
      setLoadError("No se pudieron leer las ofertas.");
      setOffers([]);
    } else {
      setOffers((data ?? []) as unknown as Offer[]);
    }
    setLoading(false);
  }, []);

  /**
   * Reads the owner's triage rows. RLS already limits them to `user_id =
   * auth.uid()`; the filter is passed anyway so the intent is explicit. A
   * failure is reported rather than swallowed: showing every offer as unseen
   * would be a quiet lie.
   */
  const loadState = useCallback(async () => {
    const client = supabase;
    if (!client || !userId) return;

    const { data, error } = await client
      .from("job_state")
      .select(JOB_STATE_SELECT)
      .eq("user_id", userId);

    if (error) {
      console.error("job_state: select failed", error);
      setStateError("No se pudo leer el estado de las ofertas.");
      return;
    }
    // A write in flight holds a newer value than this response; assigning now
    // would overwrite it with rows read before the write committed.
    if (pendingRef.current.size > 0) return;
    setStateError(null);
    flagsRef.current = mergeState((data ?? []) as unknown as JobStateRow[]);
    persistedRef.current = flagsRef.current;
    setFlags(flagsRef.current);
  }, [userId]);

  /**
   * Flips one flag optimistically and pulls the change back if the write fails,
   * so the screen never claims something the database does not hold.
   */
  const toggleFlag = useCallback(
    async (jobId: string, field: FlagField) => {
      const client = supabase;
      if (!client || !userId) return;

      const key = `${jobId}:${field}`;
      // The button is disabled while this is true; the guard also covers events
      // that land in the same tick, before React re-renders.
      if (pendingRef.current.has(key)) return;

      const next = !flagsOf(flagsRef.current, jobId)[field];
      markPending(key, true);
      flagsRef.current = setFlag(flagsRef.current, jobId, field, next);
      setFlags(flagsRef.current);
      setStateError(null);

      const stamp = next ? new Date().toISOString() : null;
      // Only the flipped column travels, so the upsert never clears the other
      // flag: PostgREST updates just the columns present in the payload.
      const payload: JobStatePayload =
        field === "seen"
          ? { user_id: userId, job_id: jobId, seen_at: stamp }
          : { user_id: userId, job_id: jobId, saved_at: stamp };

      try {
        const { error } = await client
          .from("job_state")
          .upsert(payload, { onConflict: "user_id,job_id" });

        if (error) {
          console.error("job_state: upsert failed", error);
          flagsRef.current = revertFlag(
            flagsRef.current,
            persistedRef.current,
            jobId,
            field,
          );
          setFlags(flagsRef.current);
          setStateError("No se pudo guardar el cambio; se revirtió.");
          return;
        }

        persistedRef.current = setFlag(persistedRef.current, jobId, field, next);
      } finally {
        markPending(key, false);
      }
    },
    [markPending, userId],
  );

  /**
   * Claims the app exactly once per signed-in user. `claim_ownership` returns
   * true only for the first account, so a second account is told why it reads
   * nothing instead of staring at an empty list.
   */
  useEffect(() => {
    const client = supabase;
    if (!client || !userId) {
      setClaim("idle");
      return;
    }
    let active = true;
    setClaim("claiming");

    void (async () => {
      const { data, error } = await client.rpc("claim_ownership");
      if (!active) return;
      if (error) {
        console.error("claim_ownership failed", error);
        setClaim("error");
        return;
      }
      setClaim(data === true ? "claimed" : "taken");
    })();

    return () => {
      active = false;
    };
  }, [userId]);

  // Only the owner can read rows: `is_owner()` gates every SELECT policy.
  useEffect(() => {
    if (claim !== "claimed") return;
    void loadOffers();
    void loadState();
  }, [claim, loadOffers, loadState]);

  const signIn = useCallback(
    async (email: string, password: string): Promise<string | null> => {
      const client = supabase;
      if (!client) return "Falta configuración.";
      const { error } = await client.auth.signInWithPassword({ email, password });
      if (!error) return null;
      console.error("signInWithPassword failed", error);
      return "Correo o contraseña incorrectos.";
    },
    [],
  );

  const signOut = useCallback(() => {
    const client = supabase;
    if (!client) return;
    // Signing out drops the grace stamp: the next login must ask for the PIN.
    if (userId) clearUnlocked(userId);
    void client.auth.signOut().finally(() => {
      setOffers([]);
      flagsRef.current = {};
      persistedRef.current = {};
      pendingRef.current = new Set();
      setPending(new Set());
      setFlags({});
      setStateError(null);
      setClaim("idle");
      setLoadError(null);
    });
  }, [userId]);

  if (!isConfigured || !supabase) {
    return <ConfigMissing />;
  }

  if (session === undefined) {
    return (
      <div className="centered">
        <main className="panel">
          <p className="muted">Cargando…</p>
        </main>
      </div>
    );
  }

  if (session === null) {
    return <LoginView onSignIn={signIn} />;
  }

  // A PIN is a local screen lock over the stored session, so it sits between
  // authentication and the dashboard and never replaces the password. The grace
  // stamp lives in localStorage, so a short absence does not ask again.
  if (hasPin(session.user.id) && !isUnlocked(session.user.id)) {
    return (
      <PinLock
        userId={session.user.id}
        onUnlock={() => {
          markUnlocked(session.user.id);
          setTick((value) => value + 1);
        }}
        onForgot={() => {
          clearPin(session.user.id);
          clearUnlocked(session.user.id);
          signOut();
        }}
      />
    );
  }

  return (
    <Dashboard
      offers={offers}
      flags={flags}
      pending={pending}
      email={session.user.email ?? null}
      userId={session.user.id}
      claim={claim}
      loading={loading}
      error={loadError}
      stateError={stateError}
      onSignOut={signOut}
      onReload={() => {
        void loadOffers();
        void loadState();
      }}
      onToggle={(jobId, field) => {
        void toggleFlag(jobId, field);
      }}
    />
  );
}
