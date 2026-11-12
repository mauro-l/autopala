import { supabase } from "./supabase";

/**
 * Browser side of Web Push. The subscription is owned by the phone: this module
 * asks for permission, subscribes with the public VAPID key and stores the
 * endpoint plus its two encryption keys in `push_subscriptions`. The private
 * VAPID key never reaches the browser.
 */

const VAPID_PUBLIC_KEY = (import.meta.env.PUBLIC_VAPID_PUBLIC_KEY ?? "").trim();

export type PushStatus = "unsupported" | "unconfigured" | "denied" | "on" | "off";

export function pushSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window
  );
}

/** `applicationServerKey` wants bytes, not the base64url string the env carries. */
function urlBase64ToBytes(value: string): Uint8Array<ArrayBuffer> {
  const padding = "=".repeat((4 - (value.length % 4)) % 4);
  const binary = atob(value.replace(/-/g, "+").replace(/_/g, "/") + padding);
  const bytes = new Uint8Array(new ArrayBuffer(binary.length));
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

const PERMISSION_TIMEOUT_MS = 10_000;

/**
 * Some browsers never settle the permission promise (dismissed prompt, odd
 * embedding). A hung promise would leave the button spinning forever, so give
 * up on it and surface a real state instead.
 */
function withTimeout<T>(promise: Promise<T>, fallback: T, ms = PERMISSION_TIMEOUT_MS): Promise<T> {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(fallback), ms);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      () => {
        clearTimeout(timer);
        resolve(fallback);
      },
    );
  });
}

function bytesToBase64Url(buffer: ArrayBuffer | null): string {
  if (!buffer) return "";
  let binary = "";
  for (const byte of new Uint8Array(buffer)) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export async function registerServiceWorker(): Promise<ServiceWorkerRegistration | null> {
  if (!pushSupported()) return null;
  try {
    return await navigator.serviceWorker.register("/sw.js");
  } catch (error) {
    console.error("service worker registration failed", error);
    return null;
  }
}

async function activeSubscription(): Promise<PushSubscription | null> {
  if (!pushSupported()) return null;
  const registration = await navigator.serviceWorker.getRegistration("/");
  return (await registration?.pushManager.getSubscription()) ?? null;
}

export async function pushStatus(): Promise<PushStatus> {
  if (!pushSupported()) return "unsupported";
  if (!VAPID_PUBLIC_KEY) return "unconfigured";
  if (Notification.permission === "denied") return "denied";
  const subscription = await activeSubscription();
  if (!subscription) return "off";

  // A browser subscription the database never stored cannot deliver anything,
  // so the panel must not claim it is on.
  const client = supabase;
  if (client) {
    const { data, error } = await client
      .from("push_subscriptions")
      .select("endpoint")
      .eq("endpoint", subscription.endpoint)
      .maybeSingle();
    if (!error && !data) return "off";
  }
  return "on";
}

/** Returns an error message for the UI, or `null` on success. */
export async function enablePush(userId: string): Promise<string | null> {
  if (!pushSupported()) {
    return "Este navegador no soporta notificaciones push.";
  }
  if (!VAPID_PUBLIC_KEY) {
    return "Falta PUBLIC_VAPID_PUBLIC_KEY en la configuración del sitio.";
  }
  const client = supabase;
  if (!client) return "Falta configuración de Supabase.";
  if (Notification.permission === "denied") {
    return "El navegador bloqueó los avisos para este sitio. Habilitalos en la configuración del navegador.";
  }

  try {
    // Permission may only be requested from a user gesture; this call is one.
    const permission = await withTimeout<NotificationPermission | "timeout">(
      Notification.requestPermission(),
      "timeout",
    );
    if (permission === "timeout") {
      return "El navegador no respondió al pedido de permiso. Reintentá desde la app instalada.";
    }
    if (permission !== "granted") {
      return "No diste permiso para mostrar notificaciones.";
    }

    const registration = await registerServiceWorker();
    if (!registration) {
      return "No se pudo registrar el service worker. Recargá la página e intentá de nuevo.";
    }

    const existing = await registration.pushManager.getSubscription();
    const subscription =
      existing ??
      (await withTimeout<PushSubscription | null>(
        registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToBytes(VAPID_PUBLIC_KEY),
        }),
        null,
      ));
    if (!subscription) {
      return "El navegador no completó la suscripción push.";
    }

    const { error } = await client.from("push_subscriptions").upsert(
      {
        user_id: userId,
        endpoint: subscription.endpoint,
        p256dh: bytesToBase64Url(subscription.getKey("p256dh")),
        auth: bytesToBase64Url(subscription.getKey("auth")),
        user_agent: navigator.userAgent.slice(0, 300),
        updated_at: new Date().toISOString(),
      },
      { onConflict: "endpoint" },
    );

    if (error) {
      console.error("push subscription save failed", error);
      // Do not leave a subscription the server will never use.
      await subscription.unsubscribe();
      return `No se pudo guardar la suscripción en la base (${error.code ?? "sin código"}).`;
    }
    return null;
  } catch (error) {
    console.error("push subscribe failed", error);
    const name = error instanceof Error ? error.name : "Error";
    return `No se pudo suscribir este dispositivo (${name}).`;
  }
}

/** Returns an error message for the UI, or `null` on success. */
export async function disablePush(): Promise<string | null> {
  if (!pushSupported()) return null;
  const client = supabase;

  try {
    const subscription = await activeSubscription();
    if (!subscription) return null;

    const endpoint = subscription.endpoint;
    await subscription.unsubscribe();

    if (client) {
      const { error } = await client
        .from("push_subscriptions")
        .delete()
        .eq("endpoint", endpoint);
      if (error) {
        console.error("push subscription delete failed", error);
        return "Se desactivó en este teléfono, pero no se pudo borrar en la base.";
      }
    }
    return null;
  } catch (error) {
    console.error("push unsubscribe failed", error);
    return "No se pudo desactivar en este dispositivo.";
  }
}
