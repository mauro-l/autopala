import { useState } from "react";

type Props = {
  /** Resolves to an error message, or `null` when the session was created. */
  onSignIn: (email: string, password: string) => Promise<string | null>;
};

export default function LoginView({ onSignIn }: Props) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    const value = email.trim();
    if (!value || !password || sending) return;
    setSending(true);
    setError(null);
    const failure = await onSignIn(value, password);
    setSending(false);
    if (failure) setError(failure);
  };

  return (
    <div className="centered">
      <main className="panel hero">
        <div className="hero-mark" aria-hidden="true">
          PA
        </div>
        <h1>Pala Automática</h1>
        <p className="muted small">
          Ofertas relevadas todos los días y puntuadas contra tu stack. Entrá con tu correo y tu
          contraseña.
        </p>

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
            autoComplete="username"
            inputMode="email"
            required
            disabled={sending}
          />
          <label htmlFor="login-password">Contraseña</label>
          <input
            id="login-password"
            type="password"
            name="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
            required
            disabled={sending}
          />
          <button type="submit" disabled={sending || !email.trim() || !password}>
            {sending ? "Entrando…" : "Entrar"}
          </button>
          {error ? <p className="error-box">{error}</p> : null}
        </form>
      </main>
    </div>
  );
}
