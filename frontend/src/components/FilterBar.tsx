import { useState } from "react";
import {
  DEFAULT_FILTERS,
  SCORE_FILTERS,
  STATUS_FILTERS,
  hasActiveFilters,
  type Filters,
  type SortKey,
  type StatusFilter,
} from "../lib/filters";

type Props = {
  filters: Filters;
  visible: number;
  total: number;
  /** Per-status totals over the rows that pass every other filter. */
  counts: Record<StatusFilter, number>;
  grouped: boolean;
  onChange: (next: Filters) => void;
  onGroupedChange: (grouped: boolean) => void;
};

export default function FilterBar({
  filters,
  visible,
  total,
  counts,
  grouped,
  onChange,
  onGroupedChange,
}: Props) {
  const update = (patch: Partial<Filters>) => onChange({ ...filters, ...patch });
  const active = hasActiveFilters(filters);
  const activeCount =
    Number(filters.query.trim().length > 0) +
    Number(filters.minScore > 0) +
    Number(filters.remoteOnly) +
    Number(filters.status !== DEFAULT_FILTERS.status);
  const [advancedOpen, setAdvancedOpen] = useState(false);

  return (
    <section className="filters" aria-label="Filtros de ofertas">
      <div className="searchline">
        <input
          className="search"
          type="search"
          value={filters.query}
          onChange={(event) => update({ query: event.target.value })}
          placeholder="Puesto, empresa o lugar"
          aria-label="Buscar ofertas"
          autoComplete="off"
        />
        <button
          type="button"
          className="filter-toggle"
          aria-expanded={advancedOpen}
          aria-controls="advanced-filters"
          onClick={() => setAdvancedOpen((open) => !open)}
        >
          <span aria-hidden="true">☷</span> Filtros
          <span className="filter-count" aria-label={`${activeCount} filtros activos`}>
            {activeCount}
          </span>
        </button>
      </div>

      <div className="chips status-tabs" role="group" aria-label="Estado de ofertas">
        {STATUS_FILTERS.map((option) => (
          <button
            key={option.value}
            type="button"
            className={filters.status === option.value ? "status-tab is-active" : "status-tab"}
            aria-pressed={filters.status === option.value}
            onClick={() => update({ status: option.value })}
          >
            {option.label}
            <span className="chip-count">{counts[option.value]}</span>
          </button>
        ))}
      </div>

      <div className="results-line">
        <span className="result-count" aria-live="polite">
          {visible} de {total} {total === 1 ? "oferta" : "ofertas"}
        </span>
      </div>

      <div
        className="advanced-content"
        id="advanced-filters"
        hidden={!advancedOpen}
      >
          <div className="filter-group">
            <span className="filter-label">Puntaje mínimo</span>
            <div className="chips" role="group" aria-label="Puntaje mínimo">
              {SCORE_FILTERS.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  className={filters.minScore === option.value ? "chip is-active" : "chip"}
                  aria-pressed={filters.minScore === option.value}
                  onClick={() => update({ minScore: option.value })}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>

          <label className="toggle">
            <input
              type="checkbox"
              checked={filters.remoteOnly}
              onChange={(event) => update({ remoteOnly: event.target.checked })}
            />
            Solo remoto
          </label>

          <label className="toggle">
            <input
              type="checkbox"
              checked={grouped}
              onChange={(event) => onGroupedChange(event.target.checked)}
            />
            Por día
          </label>

          <label className="sort">
            Orden
            <select
              value={filters.sort}
              onChange={(event) => update({ sort: event.target.value as SortKey })}
            >
              <option value="score">Mejor puntaje</option>
              <option value="recent">Más recientes</option>
              <option value="discovered">Recién encontradas</option>
            </select>
          </label>

          <button
            type="button"
            className="clear-filters"
            disabled={!active}
            onClick={() => onChange(DEFAULT_FILTERS)}
          >
            Limpiar filtros
          </button>
      </div>
    </section>
  );
}
