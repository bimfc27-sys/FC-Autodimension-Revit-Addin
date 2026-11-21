// Password hashing, tokens and AES-256-GCM encryption using WebCrypto only.

const enc = new TextEncoder();
const dec = new TextDecoder();

export function b64(bytes: ArrayBuffer | Uint8Array): string {
  const u = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  let s = "";
  for (let i = 0; i < u.length; i++) s += String.fromCharCode(u[i]);
  return btoa(s);
}

export function unb64(s: string): Uint8Array {
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export function randomBytes(n: number): Uint8Array {
  const b = new Uint8Array(n);
  crypto.getRandomValues(b);
  return b;
}

export function randomToken(bytes = 32): string {
  return b64(randomBytes(bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export async function sha256Hex(s: string): Promise<string> {
  const d = await crypto.subtle.digest("SHA-256", enc.encode(s));
  return [...new Uint8Array(d)].map((x) => x.toString(16).padStart(2, "0")).join("");
}

function timingSafeEqual(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a[i] ^ b[i];
  return r === 0;
}

// ---------------------------------------------------------------- passwords
// argon2id is not feasible within the Workers free-plan CPU budget, so PBKDF2-SHA256
// (the Workers maximum of 100,000 iterations by default) with a 16-byte random salt.

async function pbkdf2(password: string, salt: Uint8Array, iterations: number): Promise<Uint8Array> {
  const key = await crypto.subtle.importKey("raw", enc.encode(password), "PBKDF2", false, ["deriveBits"]);
  const bits = await crypto.subtle.deriveBits({ name: "PBKDF2", hash: "SHA-256", salt, iterations }, key, 256);
  return new Uint8Array(bits);
}

export async function hashPassword(password: string, iterations: number): Promise<string> {
  const salt = randomBytes(16);
  const h = await pbkdf2(password, salt, iterations);
  return `pbkdf2-sha256$${iterations}$${b64(salt)}$${b64(h)}`;
}

const DUMMY_SALT = new Uint8Array(16);

export async function verifyPassword(password: string, stored: string | null, iterations: number): Promise<boolean> {
  if (!stored) {
    await pbkdf2(password, DUMMY_SALT, iterations); // equalise timing for unknown accounts
    return false;
  }
  const [alg, it, salt, hash] = stored.split("$");
  if (alg !== "pbkdf2-sha256") return false;
  const h = await pbkdf2(password, unb64(salt), parseInt(it, 10));
  return timingSafeEqual(h, unb64(hash));
}

// ------------------------------------------------------------ AES-256-GCM
// CREDENTIAL_KEY secret: "kid:base64(32 bytes)". AAD binds ciphertext to the row and owner,
// so encrypted credentials can't be copied onto another camera or user.

async function aesKey(secret: string): Promise<{ kid: string; key: CryptoKey }> {
  const [kid, k] = secret.includes(":") ? secret.split(":", 2) : ["k1", secret];
  const raw = unb64(k);
  if (raw.length !== 32) throw new Error("CREDENTIAL_KEY must be 32 bytes (base64)");
  return { kid, key: await crypto.subtle.importKey("raw", raw, "AES-GCM", false, ["encrypt", "decrypt"]) };
}

export interface Sealed {
  ct: string;
  iv: string;
  kid: string;
}

export async function seal(secret: string, data: unknown, rowId: string, ownerId: string): Promise<Sealed> {
  const { kid, key } = await aesKey(secret);
  const iv = randomBytes(12);
  const ct = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv, additionalData: enc.encode(`camerascope:v1:${rowId}:${ownerId}`) },
    key,
    enc.encode(JSON.stringify(data)),
  );
  return { ct: b64(ct), iv: b64(iv), kid };
}

export async function open<T>(secret: string, s: Sealed, rowId: string, ownerId: string): Promise<T> {
  const { kid, key } = await aesKey(secret);
  if (kid !== s.kid) throw new Error("credential key id mismatch");
  const pt = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: unb64(s.iv), additionalData: enc.encode(`camerascope:v1:${rowId}:${ownerId}`) },
    key,
    unb64(s.ct),
  );
  return JSON.parse(dec.decode(pt)) as T;
}
