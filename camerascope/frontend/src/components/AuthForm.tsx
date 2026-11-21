"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";
import { api } from "@/lib/api";

export default function AuthForm({ mode }: { mode: "login" | "register" }) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [regOpen, setRegOpen] = useState<boolean | null>(null);

  useEffect(() => {
    api<{ open: boolean }>("/api/auth/registration").then((r) => setRegOpen(r.open)).catch(() => setRegOpen(null));
  }, []);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api(`/api/auth/${mode}`, {
        method: "POST",
        json: mode === "login" ? { email, password } : { email, password, display_name: name },
      });
      router.replace("/dashboard");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-wrap">
      <form className="auth-card" onSubmit={submit}>
        <div className="brand">
          <div className="brand-logo">◉</div> CameraScope
        </div>
        <div className="muted">
          {mode === "login" ? "Sign in to your dashboard." : "Create an account to save cameras and add your own devices."}
        </div>
        {mode === "register" && (
          <label className="field">
            Display name
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} required maxLength={120} />
          </label>
        )}
        <label className="field">
          E-mail
          <input className="input" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label className="field">
          Password
          <input
            className="input"
            type="password"
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={mode === "register" ? 12 : 1}
          />
          {mode === "register" && <span className="small">At least 12 characters mixing 3 of: lower, upper, digits, symbols.</span>}
        </label>
        {mode === "register" && regOpen === false && <div className="notice small">Self-registration is closed on this server. Ask the administrator to create your account.</div>}
        {error && <div className="error">{error}</div>}
        <button className="btn primary" disabled={busy}>
          {busy ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}
        </button>
        <div className="small muted">
          {mode === "login" ? (
            regOpen === false ? <>Accounts are created by the administrator.</> : <>No account? <Link href="/register">Create one</Link></>
          ) : (
            <>Already registered? <Link href="/login">Sign in</Link></>
          )}
        </div>
      </form>
    </div>
  );
}
