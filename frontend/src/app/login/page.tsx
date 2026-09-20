"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Scale } from "lucide-react";
import { errorMessage, fetchAuthConfig, login, register } from "@/lib/api";
import type { AuthConfig } from "@/lib/types";

const MIN_PASSWORD = 10;

export default function LoginPage() {
  const router = useRouter();
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);
  const [mode, setMode] = useState<"signin" | "register">("signin");
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchAuthConfig()
      .then(setConfig)
      .catch((e: unknown) => setConfigError(errorMessage(e, "Could not reach the server.")));
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting) return;
    setError(null);

    if (mode === "register" && password.length < MIN_PASSWORD) {
      setError(`Password must be at least ${MIN_PASSWORD} characters.`);
      return;
    }

    setSubmitting(true);
    try {
      if (mode === "register") {
        await register(email.trim(), name.trim(), password);
      } else {
        await login(email.trim(), password);
      }
      router.push("/");
    } catch (err: unknown) {
      setError(errorMessage(err, "Could not complete the request."));
    } finally {
      setSubmitting(false);
    }
  }

  const registering = mode === "register";
  const inputClass =
    "w-full px-3 py-2.5 bg-white border border-slate-300 rounded-xl text-sm text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500";

  return (
    <div className="min-h-full flex items-center justify-center p-4 sm:p-8">
      <div className="w-full max-w-sm space-y-6">
        <div className="flex items-center gap-3 justify-center">
          <div className="flex items-center justify-center w-10 h-10 rounded-xl bg-gradient-to-tr from-blue-600 to-indigo-500 shadow-md">
            <Scale className="w-5 h-5 text-white" aria-hidden="true" />
          </div>
          <span className="font-bold text-slate-900 text-xl tracking-tight">ContractLens</span>
        </div>

        {configError ? (
          <div role="alert" className="p-4 rounded-2xl bg-red-50 border border-red-200 text-sm text-red-800">
            {configError}
          </div>
        ) : !config ? (
          <p className="text-center text-sm text-slate-500">Loading...</p>
        ) : config.mode === "demo" ? (
          <div className="p-6 bg-white rounded-2xl border border-slate-200 shadow-sm space-y-3 text-center">
            <h1 className="text-base font-bold text-slate-900">Sign-in is disabled</h1>
            <p className="text-sm text-slate-600">
              This server is running in demo mode. There are no accounts and no login is needed.
            </p>
            <Link href="/" className="inline-block text-sm font-semibold text-blue-600 hover:text-blue-800">
              Go to the dashboard
            </Link>
          </div>
        ) : (
          <form
            onSubmit={handleSubmit}
            className="p-5 sm:p-6 bg-white rounded-2xl border border-slate-200 shadow-sm space-y-4"
          >
            <h1 className="text-base font-bold text-slate-900">
              {registering ? "Create an account" : "Sign in"}
            </h1>

            {registering && (
              <div className="space-y-1">
                <label htmlFor="name" className="text-xs font-semibold text-slate-600">
                  Name
                </label>
                <input
                  id="name"
                  type="text"
                  required
                  autoComplete="name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className={inputClass}
                />
              </div>
            )}

            <div className="space-y-1">
              <label htmlFor="email" className="text-xs font-semibold text-slate-600">
                Email
              </label>
              <input
                id="email"
                type="email"
                required
                autoComplete="email"
                autoCapitalize="none"
                spellCheck={false}
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className={inputClass}
              />
            </div>

            <div className="space-y-1">
              <label htmlFor="password" className="text-xs font-semibold text-slate-600">
                Password
              </label>
              <input
                id="password"
                type="password"
                required
                minLength={registering ? MIN_PASSWORD : undefined}
                autoComplete={registering ? "new-password" : "current-password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className={inputClass}
              />
              {registering && (
                <p className="text-[11px] text-slate-500">At least {MIN_PASSWORD} characters.</p>
              )}
            </div>

            {error && (
              <div role="alert" className="p-3 rounded-xl bg-red-50 border border-red-200 text-xs text-red-800 break-words">
                {error}
              </div>
            )}

            <button
              type="submit"
              disabled={submitting}
              className="w-full px-4 py-2.5 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-700 hover:to-indigo-700 disabled:opacity-60 text-white font-semibold rounded-xl text-sm shadow-md transition-all focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
            >
              {submitting ? "Please wait..." : registering ? "Create account" : "Sign in"}
            </button>

            {(registering || config.registration_open) && (
              <p className="text-center text-xs text-slate-500">
                {registering ? "Already have an account?" : "No account yet?"}{" "}
                <button
                  type="button"
                  onClick={() => {
                    setMode(registering ? "signin" : "register");
                    setError(null);
                  }}
                  className="font-semibold text-blue-600 hover:text-blue-800 rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
                >
                  {registering ? "Sign in" : "Create one"}
                </button>
              </p>
            )}
            {!config.registration_open && !registering && (
              <p className="text-center text-[11px] text-slate-400">Registration is closed on this server.</p>
            )}
          </form>
        )}
      </div>
    </div>
  );
}
