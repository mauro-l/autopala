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
  return subscription ? "on" : "off";
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

  // Permission may only be requested from a user gesture; this call is one.
  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    return "El navegador no dio permiso para mostrar notificaciones.";
  }

  const registration =
    (await registerServiceWorker()) ?? (await navigator.serviceWorker.ready);

  const existing = await registration.pushManager.getSubscription();
  const subscription =
    existing ??
    (await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToBytes(VAPID_PUBLIC_KEY),
    }));

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
    return "No se pudo guardar la suscripción en la base.";
  }
  return null;
}

/** Returns an error message for the UI, or `null` on success. */
export async function disablePush(): Promise<string | null> {
  if (!pushSupported()) return null;
  const client = supabase;

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
}
