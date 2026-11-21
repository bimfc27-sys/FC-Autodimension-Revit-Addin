// Per-device preferences (this browser only). Never security-relevant.
export type Transport = "auto" | "hls";
const KEY = "camerascope.transport";

export function getTransport(): Transport {
  try {
    return localStorage.getItem(KEY) === "hls" ? "hls" : "auto";
  } catch {
    return "auto";
  }
}

export function setTransport(t: Transport): void {
  try {
    localStorage.setItem(KEY, t);
  } catch {
    /* private mode: setting just isn't remembered */
  }
}
