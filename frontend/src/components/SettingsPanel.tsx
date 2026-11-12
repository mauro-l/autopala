import { useEffect } from "react";
import PinSettings from "./PinSettings";
import PushToggle from "./PushToggle";

type Props = {
  userId: string;
  onClose: () => void;
};

/** Device settings: notifications and the local PIN. Nothing here is job data. */
export default function SettingsPanel({ userId, onClose }: Props) {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [onClose]);

  return (
    <div
      className="sheet-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="Configuración"
      onClick={onClose}
    >
      <div className="sheet" onClick={(event) => event.stopPropagation()}>
        <div className="sheet-head">
          <h2 className="sheet-title">Configuración</h2>
          <button type="button" className="ghost" onClick={onClose}>
            Cerrar
          </button>
        </div>

        <section className="sheet-section">
          <span className="sheet-label">Avisos en este teléfono</span>
          <PushToggle userId={userId} />
        </section>

        <section className="sheet-section">
          <span className="sheet-label">Bloqueo con PIN</span>
          <PinSettings userId={userId} />
        </section>

        <p className="sheet-note">
          El PIN es un bloqueo local de esta pantalla, no reemplaza tu contraseña.
        </p>
      </div>
    </div>
  );
}
