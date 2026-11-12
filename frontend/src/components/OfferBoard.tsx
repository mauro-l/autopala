import { useCallback, useEffect, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import { clearPin, hasPin } from "../lib/pin";
import { isConfigured, OFFER_SELECT, supabase, type Offer } from "../lib/supabase";
import ConfigMissing from "./ConfigMissing";
import Dashboard, { type ClaimState } from "./Dashboard";
import LoginView from "./LoginView";
import PinLock from "./PinLock";

/**
 * Container: owns authentication, the ownership claim and the single read of
 * offers. Everything visual lives in `Dashboard` and its children.
 */
export default function OfferBoard() {
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  const [claim, setClaim] = useState<ClaimState>("idle");
  const [offers, setOffers] = useState<Offer[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  // In-memory only: closing the app re-locks it, which is the point of the PIN.
  const [unlocked, setUnlocked] = useState(false);

  const userId = session?.user.id ?? null;

  useEffect(() => {
    setUnlocked(false);
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
  }, [claim, loadOffers]);

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
    void client.auth.signOut().finally(() => {
      setOffers([]);
      setClaim("idle");
      setLoadError(null);
    });
  }, []);

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
  // authentication and the dashboard and never replaces the password.
  if (hasPin(session.user.id) && !unlocked) {
    return (
      <PinLock
        userId={session.user.id}
        onUnlock={() => setUnlocked(true)}
        onForgot={() => {
          clearPin(session.user.id);
          signOut();
        }}
      />
    );
  }

  return (
    <Dashboard
      offers={offers}
      email={session.user.email ?? null}
      userId={session.user.id}
      claim={claim}
      loading={loading}
      error={loadError}
      onSignOut={signOut}
      onReload={() => {
        void loadOffers();
      }}
    />
  );
}
