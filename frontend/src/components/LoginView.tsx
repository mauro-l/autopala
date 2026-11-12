import { useState } from "react";

type Props = {
  /** Resolves to an error message, or `null` when the link was sent. */
  onSignIn: (email: string) => Promise<string | null>;
};

export default function LoginView({ onSignIn }: Props) {
  const [email, setEmail] = useState("");
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    const value = email.trim();
    if (!value || sending) return;
    setSending(true);
    setError(null);
    const failure = await onSignIn(value);
    setSending(false);
    if (failure) {
      setError(failure);
      return;
    }
    setSent(true);
  };

  return (
    <div className="centered">
      <main className="panel hero">
        <div className="hero-mark" aria-hidden="true">
          PA
        </div>
        <h1>Pala Automática</h1>
        <p className="muted small">
          Ofertas relevadas todos los días y puntuadas contra tu stack. Entrá con tu correo: te
          mandamos un enlace de un solo uso.
        </p>

        {sent ? (
          <div className="notice">
            <p className="small">
              Te enviamos el enlace a <strong>{email}</strong>. Abrilo en este dispositivo para
              entrar.
            </p>
            <button
              type="button"
              className="ghost"
              onClick={() => {
                setSent(false);
                setError(null);
              }}
            >
              Usar otro correo
            </button>
          </div>
        ) : (
          <form
            className="login"
            onSubmit={(event) => {
              event.preventDefault();
              void submit();
            }}
          >
            <label htmlFor="login-email">Correo</label>
            <input
              id="login-email"
              type="email"
              name="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="vos@ejemplo.com"
              autoComplete="email"
              inputMode="email"
              required
              disabled={sending}
            />
            <button type="submit" disabled={sending || !email.trim()}>
              {sending ? "Enviando…" : "Enviarme el enlace"}
            </button>
            <p className="field-hint">
              Sin contraseña. El enlace vence pronto, así que usalo apenas llegue.
            </p>
            {error ? <p className="error-box">{error}</p> : null}
          </form>
        )}
      </main>
    </div>
  );
}
