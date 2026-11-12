type Props = {
  count?: number;
};

/**
 * Placeholder rows shown only on the first load. They exist so the shell does
 * not flash an empty state while the first query is still in flight.
 */
export default function SkeletonList({ count = 3 }: Props) {
  return (
    <div className="offers" aria-hidden="true">
      {Array.from({ length: count }, (_, index) => (
        <div className="skeleton-card" key={index}>
          <div className="sk sk-circle" />
          <div className="sk-lines">
            <div className="sk sk-line wide" />
            <div className="sk sk-line narrow" />
            <div className="sk sk-line wide" />
          </div>
        </div>
      ))}
    </div>
  );
}
