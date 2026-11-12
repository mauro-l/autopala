import { useState } from "react";
import { isValidPin, verifyPin } from "../lib/pin";

type Props = {
  userId: string;
  onUnlock: () => void;
  /** Clears the PIN and signs out: the password is the real credential. */
  onForgot: () => void;
};

export default function PinLock({ userId, onUnlock, onForgot }: Props) {
  const [pin, setPin] = useState("");
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    if (pin.length < 4 || checking) return;
    setChecking(true);
    setError(null);
    const ok = await verifyPin(userId, pin);
    setChecking(false);
    if (ok) {
      onUnlock();
      return;
    }
    setPin("");
    setError("PIN incorrecto.");
  };

  return (
    <div className="centered">
      <main className="panel hero">
        <div className="hero-mark" aria-hidden="true">
          PA
        </div>
        <h1>Pala Automática</h1>
        <p className="muted small">Ingresá tu PIN para ver las ofertas.</p>

        <form
          className="login"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <label htmlFor="pin">PIN</label>
          <input
            id="pin"
            type="password"
            name="pin"
            value={pin}
            onChange={(event) => setPin(event.target.value.replace(/\D/g, "").slice(0, 6))}
            inputMode="numeric"
            autoComplete="off"
            pattern="\d{4,6}"
            maxLength={6}
            autoFocus
            required
            disabled={checking}
          />
          <button type="submit" disabled={checking || !isValidPin(pin)}>
            {checking ? "Verificando…" : "Desbloquear"}
          </button>
          {error ? <p className="error-box">{error}</p> : null}
          <button
            type="button"
            className="ghost"
            onClick={onForgot}
            disabled={checking}
          >
            Olvidé el PIN
          </button>
          <p className="field-hint">
            El PIN es un bloqueo local. Si lo olvidás, se borra y volvés a entrar con tu contraseña.
          </p>
        </form>
      </main>
    </div>
  );
}
