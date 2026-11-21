"use client";

import { useEffect } from "react";
import { FREE } from "@/lib/edition";

/** Registers the service worker (Free Edition production builds) so the app can be installed on iPhone. */
export default function PwaRegister() {
  useEffect(() => {
    if (!FREE || process.env.NODE_ENV !== "production" || !("serviceWorker" in navigator)) return;
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }, []);
  return null;
}
