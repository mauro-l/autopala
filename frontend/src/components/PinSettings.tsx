import { useState } from "react";
import {
  clearPin,
  clearUnlocked,
  hasPin,
  isValidPin,
  markUnlocked,
  pinSupported,
  setPin as savePin,
} from "../lib/pin";

type Props = {
  userId: string;
};

/**
 * Sets, changes or removes the local PIN. It lives in the topbar because it is
 * device configuration, not app data: nothing about it reaches Supabase.
 */
export default function PinSettings({ userId }: Props) {
  const [installed, setInstalled] = useState(() => hasPin(userId));
  const [open, setOpen] = useState(false);
  const [pin, setPin] = useState("");
  const [repeat, setRepeat] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!pinSupported()) return null;

  const start = () => {
    setPin("");
    setRepeat("");
    setError(null);
    setOpen(true);
  };

  const save = async () => {
    if (busy) return;
    if (!isValidPin(pin)) {
      setError("El PIN debe tener entre 4 y 6 dígitos.");
      return;
    }
    if (pin !== repeat) {
      setError("Los PIN no coinciden.");
      return;
    }
    setBusy(true);
    try {
      await savePin(userId, pin);
      // Setting a PIN must not lock the screen the user is standing in front of.
      markUnlocked(userId);
      setInstalled(true);
      setOpen(false);
      setPin("");
      setRepeat("");
    } catch {
      setError("No se pudo guardar el PIN en este dispositivo.");
    } finally {
      setBusy(false);
    }
  };

  const remove = () => {
    clearPin(userId);
    clearUnlocked(userId);
    setInstalled(false);
    setOpen(false);
  };

  if (!open) {
    return (
      <div className="pin-settings">
        <button type="button" className="ghost" onClick={start}>
          {installed ? "Cambiar PIN" : "Activar PIN"}
        </button>
        {installed ? (
          <button type="button" className="ghost" onClick={remove}>
            Quitar
          </button>
        ) : null}
      </div>
    );
  }

  return (
    <form
      className="pin-settings"
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <input
        type="password"
        value={pin}
        onChange={(event) => setPin(event.target.value.replace(/\D/g, "").slice(0, 6))}
        inputMode="numeric"
        autoComplete="off"
        placeholder="PIN"
        aria-label="PIN nuevo"
        maxLength={6}
        autoFocus
      />
      <input
        type="password"
        value={repeat}
        onChange={(event) => setRepeat(event.target.value.replace(/\D/g, "").slice(0, 6))}
        inputMode="numeric"
        autoComplete="off"
        placeholder="Repetir"
        aria-label="Repetir PIN"
        maxLength={6}
      />
      <button type="submit" disabled={busy}>
        {busy ? "Guardando…" : "Guardar"}
      </button>
      <button type="button" className="ghost" onClick={() => setOpen(false)} disabled={busy}>
        Cancelar
      </button>
      {error ? <span className="pin-error">{error}</span> : null}
    </form>
  );
}
