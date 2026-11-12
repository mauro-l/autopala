import { useCallback, useMemo, useState } from "react";
import {
  DEFAULT_FILTERS,
  applyFilters,
  hasActiveFilters,
  summarize,
  type Filters,
} from "../lib/filters";
import type { Offer } from "../lib/supabase";
import EmptyState from "./EmptyState";
import FilterBar from "./FilterBar";
import OfferCard from "./OfferCard";
import SettingsPanel from "./SettingsPanel";
import SkeletonList from "./SkeletonList";

export type ClaimState = "idle" | "claiming" | "claimed" | "taken" | "error";

type Props = {
  offers: readonly Offer[];
  email: string | null;
  userId: string | null;
  claim: ClaimState;
  loading: boolean;
  error: string | null;
  onSignOut: () => void;
  onReload: () => void;
};

const CLAIM_LABEL: Record<ClaimState, string> = {
  idle: "Acceso sin verificar",
  claiming: "Verificando acceso…",
  claimed: "Acceso de dueño",
  taken: "Acceso de otra cuenta",
  error: "No se pudo verificar el acceso",
};

export default function Dashboard({
  offers,
  email,
  userId,
  claim,
  loading,
  error,
  onSignOut,
  onReload,
}: Props) {
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const closeSettings = useCallback(() => setSettingsOpen(false), []);

  const visible = useMemo(() => applyFilters(offers, filters), [offers, filters]);
  const overview = useMemo(() => summarize(offers), [offers]);
  const filtered = hasActiveFilters(filters);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">
            PA
          </div>
          <div>
            <div className="brand-name">Pala Automática</div>
            <div className="brand-sub">{CLAIM_LABEL[claim]}</div>
          </div>
        </div>
        <div className="user">
          {email ? <span className="user-email">{email}</span> : null}
          {userId ? (
            <button
              type="button"
              className="icon-button"
              aria-label="Configuración"
              title="Configuración"
              onClick={() => setSettingsOpen(true)}
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
                <circle cx="12" cy="12" r="3" />
                <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9v.09a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
              </svg>
            </button>
          ) : null}
          <button type="button" className="ghost" onClick={onSignOut}>
            Salir
          </button>
        </div>
      </header>

      <section className="stats" aria-label="Resumen">
        <div className="stat">
          <span className="stat-value">{overview.total}</span>
          <span className="stat-label">Ofertas</span>
        </div>
        <div className="stat">
          <span
            className={overview.notifyable > 0 ? "stat-value is-good" : "stat-value"}
          >
            {overview.notifyable}
          </span>
          <span className="stat-label">Notificables</span>
        </div>
        <div className="stat">
          <span className="stat-value">{overview.remote}</span>
          <span className="stat-label">Remotas</span>
        </div>
      </section>

      <FilterBar
        filters={filters}
        visible={visible.length}
        total={offers.length}
        onChange={setFilters}
      />

      {claim === "taken" ? (
        <p className="error-box">
          Otra cuenta ya reclamó esta app, así que este usuario no puede leer las ofertas.
          Salí y entrá con la cuenta dueña.
        </p>
      ) : null}

      {error ? (
        <div className="error-box">
          <p>{error}</p>
          <button type="button" className="ghost" onClick={onReload}>
            Reintentar
          </button>
        </div>
      ) : null}

      {loading && offers.length === 0 ? <SkeletonList count={3} /> : null}

      {!loading && offers.length === 0 && !error && claim !== "taken" ? (
        <EmptyState
          title="Todavía no hay ofertas"
          hint="El worker escribe cuando corre una recolección. Si ya corrió, recargá."
          actionLabel="Recargar"
          onAction={onReload}
        />
      ) : null}

      {offers.length > 0 && visible.length === 0 ? (
        <EmptyState
          title="Ninguna oferta coincide"
          hint="Probá con otro puesto, bajá el puntaje mínimo o quitá el filtro de remoto."
          actionLabel="Limpiar filtros"
          onAction={() => setFilters(DEFAULT_FILTERS)}
        />
      ) : null}

      {visible.length > 0 ? (
        <ul className="offers">
          {visible.map((offer) => (
            <OfferCard key={offer.id} offer={offer} />
          ))}
        </ul>
      ) : null}

      {loading && offers.length > 0 ? <p className="muted small">Actualizando…</p> : null}

      {settingsOpen && userId ? (
        <SettingsPanel userId={userId} onClose={closeSettings} />
      ) : null}

      {!filtered && offers.length > 0 ? (
        <p className="muted small">
          Ordenadas por puntaje. La lista se lee de la base; el worker la mantiene al día.
        </p>
      ) : null}
    </div>
  );
}
