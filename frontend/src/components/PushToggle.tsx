import { useEffect, useState } from "react";
import {
  disablePush,
  enablePush,
  pushStatus,
  registerServiceWorker,
  type PushStatus,
} from "../lib/push";

type Props = {
  userId: string | null;
};

const NOTE: Record<PushStatus, string> = {
  unsupported: "Este navegador no puede mostrar avisos push.",
  unconfigured: "Falta la clave pública de avisos en la configuración.",
  denied: "El permiso de avisos está bloqueado en el navegador.",
  on: "Los avisos llegan a este teléfono.",
  off: "Activá los avisos para enterarte de las ofertas de 85 o más.",
};

const ACTION: Partial<Record<PushStatus, string>> = {
  on: "Desactivar avisos",
  off: "Activar avisos",
};

/**
 * One explicit control for the phone: subscribing needs a user gesture, so this
 * button is where the permission prompt comes from. It is the only writable
 * action in an otherwise read-only UI.
 */
export default function PushToggle({ userId }: Props) {
  const [status, setStatus] = useState<PushStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    void registerServiceWorker();
    void pushStatus().then(setStatus);
  }, []);

  const toggle = async () => {
    if (!userId || busy || !status) return;
    setBusy(true);
    setMessage(null);

    if (status === "on") {
      const error = await disablePush();
      setStatus(error ? "on" : "off");
      setMessage(error);
    } else {
      const error = await enablePush(userId);
      setStatus(await pushStatus());
      setMessage(error);
    }
    setBusy(false);
  };

  if (status === null) return null;

  const action = ACTION[status];

  return (
    <div className="push-row">
      <span className="push-note">{NOTE[status]}</span>
      {action ? (
        <button
          type="button"
          className="ghost"
          onClick={() => {
            void toggle();
          }}
          disabled={busy || !userId}
        >
          {busy ? "Un momento…" : action}
        </button>
      ) : null}
      {message ? <span className="push-warn">{message}</span> : null}
    </div>
  );
}
