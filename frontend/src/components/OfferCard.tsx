import { NOTIFY_MIN, SCORE_PARTS, partPercent } from "../lib/format";
import { formatDayDistance, offerDate } from "../lib/dates";
import { isRemote } from "../lib/filters";
import { applyUrl, type Offer } from "../lib/supabase";
import type { FlagField, JobFlags } from "../lib/triage";
import ScoreDial from "./ScoreDial";

type Props = {
  offer: Offer;
  flags: JobFlags;
  /** True while the corresponding write is in flight, so the control waits. */
  pendingSeen: boolean;
  pendingSaved: boolean;
  onToggle: (field: FlagField) => void;
};

const MAX_SKILLS = 10;

export default function OfferCard({
  offer,
  flags,
  pendingSeen,
  pendingSaved,
  onToggle,
}: Props) {
  const breakdown = offer.score_breakdown;
  const parts = breakdown?.parts ?? {};
  const skills = breakdown?.matched_skills ?? [];
  const years = breakdown?.required_years ?? null;
  const level = breakdown?.seniority_level ?? null;
  const englishRequired = breakdown?.english_required === true;
  const unpaid = breakdown?.unpaid === true;
  const sources = offer.job_sources ?? [];
  const link = applyUrl(offer);
  const remote = isRemote(offer);
  // Same date the day sections group by, and the same calendar-day unit, so the
  // card can never contradict its own section header. Epoch 0 means "no date".
  const age = formatDayDistance(offerDate(offer));
  const top = (offer.score ?? 0) >= NOTIFY_MIN;
  const shownSkills = skills.slice(0, MAX_SKILLS);
  const hiddenSkills = skills.length - shownSkills.length;

  const className = ["offer", top ? "is-top" : "", flags.seen ? "is-seen" : "", flags.saved ? "is-saved" : ""]
    .filter(Boolean)
    .join(" ");

  return (
    <li className={className}>
      <ScoreDial score={offer.score} />

      <div className="offer-body">
        <div className="offer-head">
          <h3 className="offer-title">
            {link ? (
              <a href={link} target="_blank" rel="noreferrer noopener">
                {offer.title}
              </a>
            ) : (
              offer.title
            )}
          </h3>

          <div className="offer-actions">
            <button
              type="button"
              className={flags.saved ? "action is-on" : "action"}
              aria-pressed={flags.saved}
              aria-label={flags.saved ? "Quitar de guardadas" : "Guardar oferta"}
              title={flags.saved ? "Quitar de guardadas" : "Guardar oferta"}
              disabled={pendingSaved}
              onClick={() => onToggle("saved")}
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
                <path d="M6 4h12a1 1 0 0 1 1 1v15l-7-4-7 4V5a1 1 0 0 1 1-1z" />
              </svg>
            </button>

            <button
              type="button"
              className={flags.seen ? "action is-on" : "action"}
              aria-pressed={flags.seen}
              aria-label={flags.seen ? "Marcar como no vista" : "Marcar como vista"}
              title={flags.seen ? "Marcar como no vista" : "Marcar como vista"}
              disabled={pendingSeen}
              onClick={() => onToggle("seen")}
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
                <path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6z" />
                <circle cx="12" cy="12" r="2.5" />
              </svg>
            </button>
          </div>
        </div>

        <p className="offer-meta">
          <span>{offer.company}</span>
          {offer.location ? (
            <>
              <span className="dot" aria-hidden="true">
                ·
              </span>
              <span>{offer.location}</span>
            </>
          ) : null}
          <span className="dot" aria-hidden="true">
            ·
          </span>
          <span>{age}</span>
          {flags.saved ? <span className="tag is-saved">guardada</span> : null}
          {flags.seen ? <span className="tag is-seen">vista</span> : null}
          {remote ? <span className="tag is-remote">remoto</span> : null}
          {level ? <span className="tag is-level">{level}</span> : null}
          {years ? (
            <span className="tag is-level">
              {years} {years === 1 ? "año" : "años"}
            </span>
          ) : null}
          {englishRequired ? <span className="tag is-warn">inglés</span> : null}
          {unpaid ? <span className="tag is-warn">no remunerada</span> : null}
        </p>

        <div className="meters">
          {SCORE_PARTS.map((part) => {
            const value = parts[part.key] ?? 0;
            return (
              <div className="meter" key={part.key} aria-label={`${part.label}: ${value} de ${part.max}`}>
                <span className="meter-label">{part.label}</span>
                <span className="meter-track">
                  <span
                    className={value > 0 ? "meter-fill" : "meter-fill is-empty"}
                    style={{ width: `${partPercent(value, part.max)}%` }}
                  />
                </span>
              </div>
            );
          })}
        </div>

        {shownSkills.length > 0 ? (
          <div className="skills">
            {shownSkills.map((skill) => (
              <span className="skill" key={skill}>
                {skill}
              </span>
            ))}
            {hiddenSkills > 0 ? <span className="skill">+{hiddenSkills}</span> : null}
          </div>
        ) : null}

        {sources.length > 0 ? (
          <p className="sources">
            <span className="small muted">
              {sources.length === 1 ? "Fuente" : "Fuentes"}:
            </span>
            {sources.map((source) => (
              <a
                key={`${source.source}-${source.url}`}
                href={source.url}
                target="_blank"
                rel="noreferrer noopener"
              >
                {source.source}
              </a>
            ))}
          </p>
        ) : null}
      </div>
    </li>
  );
}
