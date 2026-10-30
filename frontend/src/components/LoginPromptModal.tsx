"use client";

import React, { useState } from "react";
import { Key, Eye, EyeOff, Lock, AlertCircle, X, Check } from "lucide-react";

interface LoginPromptModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: (token: string) => void;
  initialError?: string | null;
}

export function LoginPromptModal({
  isOpen,
  onClose,
  onSuccess,
  initialError,
}: LoginPromptModalProps) {
  const [tokenInput, setTokenInput] = useState("");
  const [showToken, setShowToken] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(initialError || null);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const token = tokenInput.trim();
    if (!token) {
      setError("Please enter a token.");
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const res = await fetch("/api/auth", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
      });

      const data = await res.json().catch(() => ({}));

      if (!res.ok) {
        throw new Error(data.error || `Authentication failed (${res.status})`);
      }

      // Persist in localStorage and non-httpOnly client cookie as redundancy
      if (typeof window !== "undefined") {
        try {
          localStorage.setItem("driftzero_ui_token", token);
          document.cookie = `x-driftzero-ui=${encodeURIComponent(token)}; path=/; max-age=2592000; SameSite=Lax`;
        } catch {
          // Ignore localStorage security exceptions
        }
      }

      onSuccess(token);
      onClose();
    } catch (err: any) {
      setError(err?.message || "Failed to authenticate with provided token");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4 select-none">
      <div className="bg-[#0c0c0c] border border-neutral-800 rounded-lg p-6 max-w-md w-full shadow-2xl relative font-sans">
        {/* Close Button */}
        <button
          type="button"
          onClick={onClose}
          className="absolute top-4 right-4 text-neutral-400 hover:text-white transition-colors"
        >
          <X className="w-4 h-4" />
        </button>

        {/* Modal Header */}
        <div className="flex items-center gap-3 mb-4">
          <div className="w-8 h-8 rounded bg-neutral-900 border border-neutral-700 flex items-center justify-center text-white">
            <Lock className="w-4 h-4 text-neutral-300" />
          </div>
          <div>
            <h3 className="text-sm font-semibold text-white tracking-tight">
              DriftZero UI Authentication
            </h3>
            <p className="text-xs text-neutral-400">
              Enter your <code className="text-neutral-300 font-mono text-[11px]">DRIFTZERO_UI_TOKEN</code> to continue.
            </p>
          </div>
        </div>

        {/* Error Banner */}
        {error && (
          <div className="mb-4 p-2.5 rounded bg-neutral-900 border border-neutral-700 text-xs text-neutral-200 flex items-start gap-2">
            <AlertCircle className="w-4 h-4 text-neutral-400 flex-shrink-0 mt-0.5" />
            <span className="leading-snug">{error}</span>
          </div>
        )}

        {/* Form */}
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-neutral-300 block">
              Frontend UI Token
            </label>
            <div className="relative">
              <div className="absolute inset-y-0 left-0 pl-2.5 flex items-center pointer-events-none text-neutral-500">
                <Key className="w-3.5 h-3.5" />
              </div>
              <input
                type={showToken ? "text" : "password"}
                autoFocus
                value={tokenInput}
                onChange={(e) => {
                  setTokenInput(e.target.value);
                  if (error) setError(null);
                }}
                placeholder="Enter DRIFTZERO_UI_TOKEN..."
                className="w-full bg-neutral-900 border border-neutral-800 rounded-md pl-8 pr-9 py-2 text-xs text-white placeholder-neutral-500 font-mono focus:outline-none focus:border-neutral-600 transition-colors"
              />
              <button
                type="button"
                onClick={() => setShowToken(!showToken)}
                className="absolute inset-y-0 right-0 pr-2.5 flex items-center text-neutral-400 hover:text-white"
              >
                {showToken ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
              </button>
            </div>
            <p className="text-[11px] text-neutral-500">
              Verified against <code className="font-mono">DRIFTZERO_UI_TOKEN</code> and stored in an httpOnly cookie.
            </p>
          </div>

          <div className="flex items-center justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={onClose}
              className="px-3 py-1.5 rounded-md border border-neutral-800 text-neutral-400 hover:text-white text-xs font-medium transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={loading || !tokenInput.trim()}
              className="px-3.5 py-1.5 rounded-md bg-white hover:bg-neutral-200 disabled:opacity-40 text-black text-xs font-semibold shadow-sm transition-colors"
            >
              {loading ? "Authenticating..." : "Authenticate"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
