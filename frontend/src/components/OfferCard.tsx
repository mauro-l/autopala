import { NOTIFY_MIN, SCORE_PARTS, formatAge, partPercent } from "../lib/format";
import { isRemote } from "../lib/filters";
import { applyUrl, type Offer } from "../lib/supabase";
import ScoreDial from "./ScoreDial";

type Props = {
  offer: Offer;
};

const MAX_SKILLS = 10;

export default function OfferCard({ offer }: Props) {
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
  const age = formatAge(offer.posted_at ?? offer.last_seen_at);
  const top = (offer.score ?? 0) >= NOTIFY_MIN;
  const shownSkills = skills.slice(0, MAX_SKILLS);
  const hiddenSkills = skills.length - shownSkills.length;

  return (
    <li className={top ? "offer is-top" : "offer"}>
      <ScoreDial score={offer.score} />

      <div className="offer-body">
        <h2 className="offer-title">
          {link ? (
            <a href={link} target="_blank" rel="noreferrer noopener">
              {offer.title}
            </a>
          ) : (
            offer.title
          )}
        </h2>

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
