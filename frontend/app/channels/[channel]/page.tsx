"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { api, type ChannelJob, type Job } from "@/lib/api";

export default function ChannelJobsPage() {
  const { channel } = useParams<{ channel: string }>();
  const [label, setLabel] = useState("");
  const [subtitle, setSubtitle] = useState("");
  const [jobs, setJobs] = useState<ChannelJob[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await api.channelJobs(channel);
      setLabel(res.label);
      setSubtitle(res.subtitle);
      setJobs(res.jobs);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [channel]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div>
      <Link href="/dashboard" className="text-sm text-indigo-600">← Dashboard</Link>
      <div className="mb-6 mt-2">
        <h1 className="text-2xl font-bold text-slate-900">{label || "Channel"}</h1>
        <p className="text-sm text-slate-500">{subtitle} · pick a job to see its candidates</p>
      </div>

      {channel === "talent-hunt" && <HuntForm onDone={load} />}
      {channel === "forms" && <FormsSyncPanel onDone={load} />}
      {channel === "website" && <WebsiteSyncPanel onDone={load} />}

      {error && (
        <div className="mb-6 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          {error}
        </div>
      )}

      {jobs.length === 0 ? (
        <p className="text-sm text-slate-400">
          No jobs have candidates in this channel yet.
        </p>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {jobs.map((j) => (
            <Link
              key={j.job_id}
              href={`/channels/${channel}/${j.job_id}`}
              className="group rounded-xl border border-slate-200 bg-white p-5 shadow-sm transition hover:-translate-y-0.5 hover:border-indigo-300 hover:shadow-md"
            >
              <div className="flex items-start justify-between">
                <h3 className="font-semibold text-slate-900 group-hover:text-indigo-700">
                  {j.title}
                </h3>
                <span className="text-2xl font-bold text-slate-900">{j.count}</span>
              </div>
              {j.location && <p className="text-sm text-slate-500">{j.location}</p>}
              <div className="mt-3 flex gap-3 text-xs text-slate-500">
                <span>{j.scored} scored</span>
                <span>{j.reviewed} reviewed</span>
              </div>
              <p className="mt-3 text-sm font-medium text-indigo-600">View candidates →</p>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

function WebsiteSyncPanel({ onDone }: { onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  async function sync() {
    setBusy(true);
    setMsg(null);
    try {
      const res = await api.syncWebsiteJobs();
      setMsg(`Synced ${res.count} active job(s) from your careers site.`);
      onDone();
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mb-6 flex items-center justify-between rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div>
        <p className="text-sm font-medium text-slate-700">Active jobs from your website</p>
        <p className="text-xs text-slate-400">
          Pull the live jobs from your careers portal. Applicants who apply on your site land here
          and are also forwarded to your external partner ATS.
        </p>
        {msg && <p className="mt-1 text-xs text-slate-500">{msg}</p>}
      </div>
      <button onClick={sync} disabled={busy} className="btn-primary shrink-0">
        {busy ? "Syncing…" : "Sync website jobs"}
      </button>
    </div>
  );
}

function FormsSyncPanel({ onDone }: { onDone: () => void }) {
  const [position, setPosition] = useState("");
  const [formRef, setFormRef] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  async function sync() {
    if (!position.trim()) {
      setMsg("Enter the job position to sync the form into.");
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      const { job_id } = await api.ensureJob(position.trim());
      const res = await api.syncForms(job_id, formRef.trim() || undefined);
      setMsg(`Synced ${res.count} response(s) into “${position.trim()}”.`);
      onDone();
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mb-6 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <p className="mb-1 text-sm font-medium text-slate-700">Sync a Google Form into a job</p>
      <p className="mb-3 text-xs text-slate-400">
        Type the job position, then paste the form&apos;s <strong>edit</strong> link
        (…/forms/d/&lt;id&gt;/edit). The link is remembered per job, so next time you can leave it
        blank.
      </p>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-6">
        <input
          value={position}
          onChange={(e) => setPosition(e.target.value)}
          placeholder="Job position (e.g. Backend Engineer)"
          className="input sm:col-span-2"
        />
        <input
          value={formRef}
          onChange={(e) => setFormRef(e.target.value)}
          placeholder="Google Form edit link or ID (optional)"
          className="input sm:col-span-3"
        />
        <button onClick={sync} disabled={busy} className="btn-primary sm:col-span-1">
          {busy ? "Syncing…" : "Sync form"}
        </button>
      </div>
      {msg && <p className="mt-2 text-xs text-slate-500">{msg}</p>}
    </div>
  );
}

function HuntForm({ onDone }: { onDone: () => void }) {
  const [position, setPosition] = useState("");
  const [skills, setSkills] = useState("");
  const [exp, setExp] = useState("");
  const [location, setLocation] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  async function hunt() {
    if (!position.trim()) {
      setMsg("Enter the job position to hunt for.");
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      const { job_id } = await api.ensureJob(position.trim());
      const res = await api.talentHunt(job_id, {
        role: position.trim(),
        skills: skills.split(",").map((s) => s.trim()).filter(Boolean),
        experience_min: exp ? Number(exp) : undefined,
        location: location.trim() || undefined,
      });
      setMsg(`Added ${res.count} candidate(s) for “${position.trim()}”.`);
      onDone();
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mb-6 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <p className="mb-3 text-sm font-medium text-slate-700">Find candidates</p>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-6">
        <input
          value={position}
          onChange={(e) => setPosition(e.target.value)}
          placeholder="Job position (e.g. Backend Engineer)"
          className="input sm:col-span-2"
        />
        <input value={skills} onChange={(e) => setSkills(e.target.value)} placeholder="Skills (comma)" className="input sm:col-span-2" />
        <input value={exp} onChange={(e) => setExp(e.target.value)} placeholder="Min exp" type="number" className="input" />
        <input value={location} onChange={(e) => setLocation(e.target.value)} placeholder="Location" className="input" />
        <button onClick={hunt} disabled={busy} className="btn-primary sm:col-span-1">
          {busy ? "Hunting…" : "Hunt"}
        </button>
      </div>
      {msg && <p className="mt-2 text-xs text-slate-500">{msg}</p>}
    </div>
  );
}
