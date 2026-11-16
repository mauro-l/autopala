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

  return (
    <section className="filters" aria-label="Filtros de ofertas">
      <input
        type="search"
        value={filters.query}
        onChange={(event) => update({ query: event.target.value })}
        placeholder="Puesto, empresa o lugar"
        aria-label="Buscar ofertas"
        autoComplete="off"
      />

      <div className="filter-row between">
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

        <label className="toggle">
          <input
            type="checkbox"
            checked={filters.remoteOnly}
            onChange={(event) => update({ remoteOnly: event.target.checked })}
          />
          Solo remoto
        </label>
      </div>

      <div className="chips" role="group" aria-label="Estado de la oferta">
        {STATUS_FILTERS.map((option) => (
          <button
            key={option.value}
            type="button"
            className={filters.status === option.value ? "chip is-active" : "chip"}
            aria-pressed={filters.status === option.value}
            onClick={() => update({ status: option.value })}
          >
            {option.label}
            <span className="chip-count">{counts[option.value]}</span>
          </button>
        ))}
      </div>

      <div className="filter-row between">
        <span className="result-count">
          {visible} de {total} {total === 1 ? "oferta" : "ofertas"}
        </span>

        <div className="filter-row">
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
            </select>
          </label>

          <button
            type="button"
            className="ghost"
            disabled={!active}
            onClick={() => onChange(DEFAULT_FILTERS)}
          >
            Limpiar
          </button>
        </div>
      </div>
    </section>
  );
}
