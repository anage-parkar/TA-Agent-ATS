"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { api, CHANNEL_SOURCES, type ApplicationRow } from "@/lib/api";
import { CandidateCard } from "@/components/CandidateCard";

export default function ChannelJobCandidatesPage() {
  const { channel, jobId } = useParams<{ channel: string; jobId: string }>();
  const sources = CHANNEL_SOURCES[channel] ?? [];

  const [rows, setRows] = useState<ApplicationRow[]>([]);
  const [jobTitle, setJobTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [res, job] = await Promise.all([api.listCandidates(jobId), api.getJob(jobId)]);
      setRows(res.candidates.filter((r) => sources.includes(r.source)));
      setJobTitle(job.title);
    } catch (e) {
      setError((e as Error).message);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, channel]);

  useEffect(() => {
    load();
  }, [load]);

  const active = useMemo(
    () => rows.filter((r) => r.status !== "rejected").sort((a, b) => (b.ats_score ?? -1) - (a.ats_score ?? -1)),
    [rows]
  );
  const rejected = useMemo(() => rows.filter((r) => r.status === "rejected"), [rows]);
  const unscored = active.filter((r) => r.ats_score === null).length;

  async function score() {
    setBusy(true);
    setError(null);
    try {
      await api.scoreApplicants(jobId);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function syncForm() {
    setBusy(true);
    setError(null);
    try {
      await api.syncForms(jobId); // uses the job's remembered form
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <Link href={`/channels/${channel}`} className="text-sm text-indigo-600">← Back to jobs</Link>
      <div className="mb-6 mt-2 flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">{jobTitle || "Candidates"}</h1>
          <p className="text-sm text-slate-500">
            {active.length} candidate{active.length === 1 ? "" : "s"} ·{" "}
            {unscored > 0 ? `${unscored} unscored` : "all scored"}
          </p>
        </div>
        <div className="flex gap-2">
          {channel === "linkedin" && (
            <button
              onClick={() => {
                navigator.clipboard.writeText(`${window.location.origin}/apply/${jobId}`);
                setCopied(true);
                setTimeout(() => setCopied(false), 1500);
              }}
              className="btn-ghost"
            >
              {copied ? "Copied ✓" : "Copy apply link"}
            </button>
          )}
          {channel === "forms" && (
            <button onClick={syncForm} disabled={busy} className="btn-ghost">
              {busy ? "Working…" : "Sync form"}
            </button>
          )}
          {unscored > 0 && (
            <button onClick={score} disabled={busy} className="btn-primary">
              {busy ? "Scoring…" : `Score new (${unscored})`}
            </button>
          )}
        </div>
      </div>

      {error && (
        <div className="mb-6 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          {error}
        </div>
      )}

      <div className="space-y-3">
        {active.length === 0 ? (
          <p className="text-sm text-slate-400">No candidates in this channel for this job yet.</p>
        ) : (
          active.map((r) => <CandidateCard key={r.id} item={r} onChanged={load} />)
        )}
      </div>

      {rejected.length > 0 && (
        <div className="mt-8">
          <h2 className="mb-3 border-b border-slate-200 pb-2 text-lg font-semibold text-slate-900">
            Rejected <span className="text-sm font-normal text-slate-400">({rejected.length})</span>
          </h2>
          <div className="space-y-3">
            {rejected.map((r) => (
              <CandidateCard key={r.id} item={r} onChanged={load} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
