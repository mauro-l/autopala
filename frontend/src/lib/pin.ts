/**
 * Local PIN lock. Convenience, not security: it keeps the dashboard hidden
 * behind a short code on this device, while the real credential stays the
 * Supabase password. Anyone with access to this browser's storage can clear the
 * PIN, so it must never be treated as an authorization boundary — RLS still
 * decides what the session can read.
 *
 * The PIN is never stored: only a PBKDF2-SHA256 hash and a random salt.
 */

const KEY_PREFIX = "pala.pin.";
const ITERATIONS = 150_000;
const MIN_DIGITS = 4;
const MAX_DIGITS = 6;

export type PinRecord = {
  salt: string;
  hash: string;
  iterations: number;
};

export function pinSupported(): boolean {
  return typeof window !== "undefined" && typeof crypto?.subtle?.deriveBits === "function";
}

export function isValidPin(pin: string): boolean {
  return new RegExp(`^\\d{${MIN_DIGITS},${MAX_DIGITS}}$`).test(pin);
}

function storageKey(userId: string): string {
  return `${KEY_PREFIX}${userId}`;
}

function toBase64(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

function fromBase64(value: string): Uint8Array<ArrayBuffer> {
  const binary = atob(value);
  const bytes = new Uint8Array(new ArrayBuffer(binary.length));
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

async function derive(
  pin: string,
  salt: Uint8Array<ArrayBuffer>,
  iterations: number,
): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(pin),
    "PBKDF2",
    false,
    ["deriveBits"],
  );
  const bits = await crypto.subtle.deriveBits(
    { name: "PBKDF2", salt, iterations, hash: "SHA-256" },
    key,
    256,
  );
  return toBase64(new Uint8Array(bits));
}

export function readPin(userId: string): PinRecord | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(storageKey(userId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<PinRecord>;
    if (!parsed.salt || !parsed.hash || !parsed.iterations) return null;
    return { salt: parsed.salt, hash: parsed.hash, iterations: parsed.iterations };
  } catch {
    return null;
  }
}

export function hasPin(userId: string): boolean {
  return readPin(userId) !== null;
}

export async function setPin(userId: string, pin: string): Promise<void> {
  if (!pinSupported()) throw new Error("Web Crypto is unavailable in this context.");
  if (!isValidPin(pin)) throw new Error("The PIN must be 4 to 6 digits.");

  const salt = crypto.getRandomValues(new Uint8Array(new ArrayBuffer(16)));
  const hash = await derive(pin, salt, ITERATIONS);
  const record: PinRecord = { salt: toBase64(salt), hash, iterations: ITERATIONS };
  window.localStorage.setItem(storageKey(userId), JSON.stringify(record));
}

export async function verifyPin(userId: string, pin: string): Promise<boolean> {
  const record = readPin(userId);
  if (!record || !isValidPin(pin)) return false;
  const candidate = await derive(pin, fromBase64(record.salt), record.iterations);
  return candidate === record.hash;
}

export function clearPin(userId: string): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(storageKey(userId));
}
