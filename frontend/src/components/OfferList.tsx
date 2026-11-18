import { useMemo } from "react";
import { firstSeenDate, groupByDay } from "../lib/dates";
import type { SortKey } from "../lib/filters";
import type { Offer } from "../lib/supabase";
import { flagsOf, type FlagField, type FlagMap } from "../lib/triage";
import OfferCard from "./OfferCard";

type Props = {
  offers: readonly Offer[];
  flags: FlagMap;
  pending: ReadonlySet<string>;
  grouped: boolean;
  sort: SortKey;
  onToggle: (jobId: string, field: FlagField) => void;
};

/**
 * Renders the filtered offers either as one ranked list or as day sections.
 * Grouping is pure presentation: it takes the list the filters already narrowed
 * and never re-sorts across sections, so the chosen order is kept inside a day.
 */
export default function OfferList({ offers, flags, pending, grouped, sort, onToggle }: Props) {
  const renderCard = (offer: Offer) => (
    <OfferCard
      key={offer.id}
      offer={offer}
      flags={flagsOf(flags, offer.id)}
      pendingSeen={pending.has(`${offer.id}:seen`)}
      pendingSaved={pending.has(`${offer.id}:saved`)}
      onToggle={(field) => onToggle(offer.id, field)}
    />
  );

  const groups = useMemo(
    () => (grouped ? groupByDay(offers, Date.now(), sort === "discovered" ? firstSeenDate : undefined) : []),
    [grouped, offers, sort],
  );

  if (!grouped) {
    return (
      <section aria-label="Ofertas">
        {/* The cards use h3; a flat list still needs its own h2 so the heading
            order never skips a level. */}
        <h2 className="sr-only">Ofertas</h2>
        <ul className="offers">{offers.map(renderCard)}</ul>
      </section>
    );
  }

  return (
    <div className="offer-groups">
      {groups.map((group) => (
        <section className="offer-group" key={group.key} aria-label={group.label}>
          <h2 className="day-heading">
            {group.label}
            <span className="day-count">{group.items.length}</span>
          </h2>
          <ul className="offers">{group.items.map(renderCard)}</ul>
        </section>
      ))}
    </div>
  );
}
