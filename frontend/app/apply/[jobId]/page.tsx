"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8001";

interface ApplyInfo {
  job_id: string;
  title: string;
  location?: string;
  skills?: string[];
}

export default function ApplyPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const [info, setInfo] = useState<ApplyInfo | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API}/api/jobs/${jobId}/apply-info`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then(setInfo)
      .catch(() => setNotFound(true));
  }, [jobId]);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const fd = new FormData(e.currentTarget);
      const res = await fetch(`${API}/api/jobs/${jobId}/apply`, {
        method: "POST",
        body: fd,
      });
      if (!res.ok) throw new Error(await res.text());
      setDone(true);
    } catch (err) {
      setError((err as Error).message || "Submission failed");
    } finally {
      setBusy(false);
    }
  }

  if (notFound) {
    return <Shell><p className="text-slate-500">This job posting was not found.</p></Shell>;
  }
  if (done) {
    return (
      <Shell>
        <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-6 text-center">
          <h2 className="text-xl font-semibold text-emerald-700">Application received ✅</h2>
          <p className="mt-2 text-sm text-slate-600">
            Thanks for applying to <strong>{info?.title}</strong>. Our team will review
            your profile and be in touch.
          </p>
        </div>
      </Shell>
    );
  }

  return (
    <Shell>
      <h1 className="text-2xl font-bold text-slate-900">{info?.title ?? "Apply"}</h1>
      {info?.location && <p className="text-sm text-slate-500">{info.location}</p>}
      {info?.skills && info.skills.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {info.skills.slice(0, 10).map((s) => (
            <span key={s} className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">
              {s}
            </span>
          ))}
        </div>
      )}

      <form onSubmit={submit} className="mt-6 space-y-4">
        <Field name="full_name" label="Full name" required />
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field name="email" label="Email" type="email" />
          <Field name="phone" label="Phone" />
        </div>
        <Field name="linkedin_url" label="LinkedIn profile URL" />
        <Field name="headline" label="Headline / current title" />
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field name="location" label="Location" />
          <Field name="experience_years" label="Years of experience" type="number" />
        </div>
        <Field name="skills" label="Skills (comma-separated)" placeholder="Python, FastAPI, PostgreSQL" />
        <div>
          <label className="mb-1 block text-sm text-slate-500">Cover note</label>
          <textarea
            name="cover_note"
            rows={3}
            className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 outline-none focus:border-indigo-500"
          />
        </div>
        <div>
          <label className="mb-1 block text-sm text-slate-500">Resume (PDF / DOC, ≤10 MB)</label>
          <input
            type="file"
            name="resume"
            accept=".pdf,.doc,.docx,.txt,.rtf"
            className="block w-full text-sm text-slate-600 file:mr-3 file:rounded-lg file:border-0 file:bg-indigo-600 file:px-4 file:py-2 file:text-white hover:file:bg-indigo-700"
          />
        </div>

        {error && (
          <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
            {error}
          </div>
        )}

        <button
          disabled={busy}
          className="w-full rounded-lg bg-indigo-600 px-5 py-2.5 font-medium text-white transition hover:bg-indigo-700 disabled:opacity-50"
        >
          {busy ? "Submitting…" : "Submit application"}
        </button>
      </form>
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="mx-auto max-w-xl px-6 py-12">
      <p className="mb-6 text-xs uppercase tracking-wide text-slate-400">TA Agent · Careers</p>
      {children}
    </div>
  );
}

function Field({
  name,
  label,
  type = "text",
  required = false,
  placeholder,
}: {
  name: string;
  label: string;
  type?: string;
  required?: boolean;
  placeholder?: string;
}) {
  return (
    <div>
      <label className="mb-1 block text-sm text-slate-500">
        {label} {required && <span className="text-rose-500">*</span>}
      </label>
      <input
        name={name}
        type={type}
        required={required}
        placeholder={placeholder}
        className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 outline-none focus:border-indigo-500"
      />
    </div>
  );
}
