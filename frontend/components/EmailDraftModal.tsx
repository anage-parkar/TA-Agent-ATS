"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export function EmailDraftModal({
  applicationId,
  decision,
  onClose,
  onDone,
}: {
  applicationId: string;
  decision: "proceed" | "reject";
  onClose: () => void;
  onDone: (status: "approved" | "rejected") => void;
}) {
  const [loading, setLoading] = useState(true);
  const [to, setTo] = useState<string | undefined>();
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [aiBusy, setAiBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    // Instant template draft (no LLM) so the modal is responsive.
    api
      .draftEmail(applicationId, decision, false)
      .then((d) => {
        setTo(d.to);
        setSubject(d.subject);
        setBody(d.body);
      })
      .catch((e) => setError((e as Error).message))
      .finally(() => setLoading(false));
  }, [applicationId, decision]);

  async function rewriteWithAI() {
    setAiBusy(true);
    setError(null);
    try {
      const d = await api.draftEmail(applicationId, decision, true);
      setSubject(d.subject);
      setBody(d.body);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setAiBusy(false);
    }
  }

  async function send() {
    setBusy(true);
    setError(null);
    try {
      const res = await api.sendEmail(applicationId, decision, subject, body);
      if (res.sent) {
        onDone(res.status as "approved" | "rejected");
        onClose();
      } else {
        // Decision applied, but email couldn't go out (not configured).
        setNote(res.detail || "Email not sent (not configured). Decision recorded.");
        onDone(res.status as "approved" | "rejected");
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function skip() {
    setBusy(true);
    try {
      const res = await api.decide(applicationId, decision);
      onDone(res.status as "approved" | "rejected");
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const isProceed = decision === "proceed";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      onClick={(e) => e.stopPropagation()}
    >
      <div className="absolute inset-0 bg-slate-900/30" onClick={onClose} />
      <div className="relative w-full max-w-lg rounded-2xl border border-slate-200 bg-white p-5 shadow-2xl">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="font-semibold text-slate-900">
            {isProceed ? "Advance candidate — draft email" : "Reject candidate — draft email"}
          </h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-700">✕</button>
        </div>

        {loading ? (
          <p className="py-8 text-center text-sm text-slate-500">Drafting with AI…</p>
        ) : (
          <div className="space-y-3">
            {to && (
              <p className="text-xs text-slate-500">
                To: <span className="text-slate-700">{to}</span>
              </p>
            )}
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-500">Subject</label>
              <input value={subject} onChange={(e) => setSubject(e.target.value)} className="input w-full" />
            </div>
            <div>
              <div className="mb-1 flex items-center justify-between">
                <label className="text-xs font-medium text-slate-500">Body</label>
                <button
                  onClick={rewriteWithAI}
                  disabled={aiBusy}
                  className="text-xs font-medium text-indigo-600 hover:text-indigo-700 disabled:opacity-50"
                  title="Generate a richer, personalised version (slower — uses the LLM)"
                >
                  {aiBusy ? "Rewriting…" : "✨ Rewrite with AI"}
                </button>
              </div>
              <textarea
                value={body}
                onChange={(e) => setBody(e.target.value)}
                rows={10}
                className="input w-full font-mono text-xs leading-relaxed"
              />
            </div>

            {error && <p className="text-sm text-rose-600">{error}</p>}
            {note && (
              <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-700">{note}</p>
            )}

            <div className="flex items-center justify-between pt-1">
              <button onClick={skip} disabled={busy} className="text-xs text-slate-500 hover:text-slate-700">
                Skip email — just {isProceed ? "advance" : "reject"}
              </button>
              <div className="flex gap-2">
                <button onClick={onClose} className="btn-ghost">Cancel</button>
                <button
                  onClick={send}
                  disabled={busy || !to}
                  className={`rounded-lg px-4 py-2 text-sm font-medium text-white transition disabled:opacity-50 ${
                    isProceed ? "bg-indigo-600 hover:bg-indigo-700" : "bg-rose-600 hover:bg-rose-700"
                  }`}
                  title={!to ? "Candidate has no email address" : ""}
                >
                  {busy ? "Sending…" : isProceed ? "Send & advance" : "Send & reject"}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
