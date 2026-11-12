type Props = {
  title: string;
  hint?: string;
  actionLabel?: string;
  onAction?: () => void;
};

export default function EmptyState({ title, hint, actionLabel, onAction }: Props) {
  return (
    <div className="empty">
      <p className="empty-title">{title}</p>
      {hint ? <p className="muted small">{hint}</p> : null}
      {actionLabel && onAction ? (
        <button type="button" className="ghost" onClick={onAction}>
          {actionLabel}
        </button>
      ) : null}
    </div>
  );
}
