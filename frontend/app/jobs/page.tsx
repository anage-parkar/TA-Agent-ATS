"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, type Job } from "@/lib/api";

export default function JobsPage() {
  const router = useRouter();
  const [url, setUrl] = useState("");
  const [jobs, setJobs] = useState<Job[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  async function refresh() {
    try {
      const res = await api.listJobs();
      setJobs(res.jobs);
    } catch {
      /* backend may not be up yet */
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function sync(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await api.syncJob(url.trim());
      setUrl("");
      await refresh();
      router.push(`/candidates?job=${res.job_id}`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <h1 className="mb-1 text-2xl font-bold text-slate-900">Jobs</h1>
      <p className="mb-6 text-sm text-slate-500">
        Paste a LinkedIn job URL — the JD parser agent extracts structured fields.
      </p>

      <form onSubmit={sync} className="mb-8 flex gap-3">
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          required
          placeholder="https://www.linkedin.com/jobs/view/..."
          className="input flex-1 px-4 py-2.5"
        />
        <button disabled={busy} className="btn-primary px-5 py-2.5">
          {busy ? "Syncing…" : "Sync job"}
        </button>
      </form>

      {error && (
        <div className="mb-6 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          {error}
        </div>
      )}

      <div className="space-y-3">
        {jobs.length === 0 && (
          <p className="text-sm text-slate-400">No jobs yet. Sync one above.</p>
        )}
        {jobs.map((job) => (
          <div key={job.id} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="flex items-center justify-between">
              <h3 className="font-semibold text-slate-900">{job.title}</h3>
              <span className="text-xs uppercase text-slate-400">{job.seniority}</span>
            </div>
            <p className="text-sm text-slate-500">{job.location}</p>
            <div className="mt-2 flex flex-wrap gap-1">
              {(job.skills ?? []).slice(0, 8).map((s) => (
                <span key={s} className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">
                  {s}
                </span>
              ))}
            </div>
            <div className="mt-3 flex gap-2 border-t border-slate-100 pt-3">
              <button
                onClick={() => router.push(`/candidates?job=${job.id}`)}
                className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-indigo-700"
              >
                View applicants
              </button>
              <button
                onClick={() => {
                  const link = `${window.location.origin}/apply/${job.id}`;
                  navigator.clipboard.writeText(link);
                  setCopied(job.id);
                  setTimeout(() => setCopied(null), 1500);
                }}
                className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs text-slate-600 transition hover:bg-slate-50"
                title="Use this as the Apply URL on your LinkedIn job post"
              >
                {copied === job.id ? "Copied ✓" : "Copy apply link"}
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
