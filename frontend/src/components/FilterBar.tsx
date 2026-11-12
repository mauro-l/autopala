import {
  DEFAULT_FILTERS,
  SCORE_FILTERS,
  hasActiveFilters,
  type Filters,
  type SortKey,
} from "../lib/filters";

type Props = {
  filters: Filters;
  visible: number;
  total: number;
  onChange: (next: Filters) => void;
};

export default function FilterBar({ filters, visible, total, onChange }: Props) {
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

      <div className="filter-row between">
        <span className="result-count">
          {visible} de {total} {total === 1 ? "oferta" : "ofertas"}
        </span>

        <div className="filter-row">
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
