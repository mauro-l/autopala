/**
 * Rendered when the build had no `PUBLIC_SUPABASE_*` variables. It is a build
 * state, not an error: the static bundle succeeded and holds no key material.
 */
export default function ConfigMissing() {
  return (
    <div className="centered">
      <main className="panel">
        <h1>Falta configuración</h1>
        <p className="muted small">
          Definí <code>PUBLIC_SUPABASE_URL</code> y <code>PUBLIC_SUPABASE_ANON_KEY</code> en{" "}
          <code>frontend/.env</code> y volvé a compilar. Sin esas variables la app no puede
          leer ofertas.
        </p>
      </main>
    </div>
  );
}
