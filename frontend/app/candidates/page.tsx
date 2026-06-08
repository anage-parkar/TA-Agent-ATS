"use client";

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { api, type ApplicationRow } from "@/lib/api";
import { CandidateCard } from "@/components/CandidateCard";

const GROUPS = {
  linkedin: ["offsite_form", "linkedin_apply_connect", "linkedin_mock"],
  forms: ["google_form", "microsoft_form"],
  talent: ["talent_hunt", "manual", "sourced"],
} as const;

type GroupKey = keyof typeof GROUPS;

function groupOf(source: string): GroupKey {
  if (GROUPS.linkedin.includes(source as never)) return "linkedin";
  if (GROUPS.forms.includes(source as never)) return "forms";
  return "talent";
}

function CandidatesInner() {
  const params = useSearchParams();
  const jobId = params.get("job");

  const [rows, setRows] = useState<ApplicationRow[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    if (!jobId) return;
    try {
      const res = await api.listCandidates(jobId);
      setRows(res.candidates);
    } catch {
      /* none yet */
    }
  }, [jobId]);

  useEffect(() => {
    load();
  }, [load]);

  const sortByScore = (a: ApplicationRow, b: ApplicationRow) =>
    (b.ats_score ?? -1) - (a.ats_score ?? -1);

  // Rejected candidates live in their own section and are excluded from the
  // active channels (and won't be re-scored / re-surfaced on re-sync).
  const rejected = useMemo(
    () => rows.filter((r) => r.status === "rejected").sort(sortByScore),
    [rows]
  );

  const grouped = useMemo(() => {
    const g: Record<GroupKey, ApplicationRow[]> = { linkedin: [], forms: [], talent: [] };
    for (const r of rows) {
      if (r.status === "rejected") continue; // excluded from active sections
      g[groupOf(r.source)].push(r);
    }
    (Object.keys(g) as GroupKey[]).forEach((k) => g[k].sort(sortByScore));
    return g;
  }, [rows]);

  const unscored = rows.filter((r) => r.ats_score === null && r.status !== "rejected").length;

  async function run(key: string, fn: () => Promise<unknown>) {
    setBusy(key);
    setError(null);
    try {
      await fn();
      await load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  }

  if (!jobId) {
    return (
      <p className="text-sm text-slate-500">
        Pick a job from the{" "}
        <a href="/jobs" className="text-indigo-400 underline">Jobs</a> page.
      </p>
    );
  }

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Candidates</h1>
          <p className="text-sm text-slate-500">
            Three sourcing channels. {unscored > 0 ? `${unscored} unscored.` : "All scored."}
          </p>
        </div>
        <button
          onClick={() => run("score", () => api.scoreApplicants(jobId))}
          disabled={!!busy || unscored === 0}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-700 disabled:opacity-50"
        >
          {busy === "score" ? "Scoring…" : `Score new (${unscored})`}
        </button>
      </div>

      {error && (
        <div className="mb-6 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          {error}
        </div>
      )}

      {/* Section 1 — LinkedIn Job Post */}
      <Section
        title="LinkedIn Job Post"
        subtitle="Applicants who applied to your posting (apply link / Apply Connect)"
        count={grouped.linkedin.length}
        onChanged={load}
        action={
          <button
            onClick={() => {
              navigator.clipboard.writeText(`${window.location.origin}/apply/${jobId}`);
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            }}
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs text-slate-600 transition hover:bg-slate-50"
          >
            {copied ? "Copied ✓" : "Copy apply link"}
          </button>
        }
        rows={grouped.linkedin}
        empty="No applicants yet. Share the apply link on your LinkedIn job post."
      />

      {/* Section 2 — Microsoft / Google Forms */}
      <FormsSection
        rows={grouped.forms}
        busy={busy === "forms"}
        onChanged={load}
        onSync={(formRef) => run("forms", () => api.syncForms(jobId, formRef))}
      />

      {/* Section 3 — Talent Hunt */}
      <TalentHuntSection
        rows={grouped.talent}
        busy={busy === "hunt"}
        onChanged={load}
        onHunt={(criteria) => run("hunt", () => api.talentHunt(jobId, criteria))}
      />

      {/* Rejected — excluded from the active channels above */}
      {rejected.length > 0 && (
        <Section
          title="Rejected"
          subtitle="Declined by a recruiter — kept here and not re-surfaced on re-sync"
          count={rejected.length}
          rows={rejected}
          empty=""
          onChanged={load}
        />
      )}

    </div>
  );
}

function Section({
  title,
  subtitle,
  count,
  action,
  rows,
  empty,
  children,
  onChanged,
}: {
  title: string;
  subtitle: string;
  count: number;
  action?: React.ReactNode;
  rows: ApplicationRow[];
  empty: string;
  children?: React.ReactNode;
  onChanged?: () => void;
}) {
  return (
    <section className="mb-8">
      <div className="mb-3 flex items-end justify-between border-b border-slate-200 pb-2">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">
            {title} <span className="text-sm font-normal text-slate-400">({count})</span>
          </h2>
          <p className="text-xs text-slate-400">{subtitle}</p>
        </div>
        {action}
      </div>
      {children}
      <div className="space-y-3">
        {rows.length === 0 ? (
          <p className="text-sm text-slate-400">{empty}</p>
        ) : (
          rows.map((r) => <CandidateCard key={r.id} item={r} onChanged={onChanged} />)
        )}
      </div>
    </section>
  );
}

function FormsSection({
  rows,
  busy,
  onSync,
  onChanged,
}: {
  rows: ApplicationRow[];
  busy: boolean;
  onSync: (formRef?: string) => void;
  onChanged?: () => void;
}) {
  const [formRef, setFormRef] = useState("");
  return (
    <Section
      title="Microsoft / Google Forms"
      subtitle="Paste a Google Form link to sync its responses"
      count={rows.length}
      rows={rows}
      onChanged={onChanged}
      empty="No form responses yet. Paste a Google Form link and sync below."
      action={
        <div className="flex gap-2">
          <input
            value={formRef}
            onChange={(e) => setFormRef(e.target.value)}
            placeholder="Google Form link or ID"
            className="w-64 rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-xs text-slate-900 outline-none focus:border-indigo-500"
          />
          <button
            onClick={() => onSync(formRef.trim() || undefined)}
            disabled={busy}
            className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-indigo-700 disabled:opacity-50"
          >
            {busy ? "Syncing…" : "Sync form"}
          </button>
        </div>
      }
    />
  );
}

function TalentHuntSection({
  rows,
  busy,
  onHunt,
  onChanged,
}: {
  rows: ApplicationRow[];
  busy: boolean;
  onHunt: (c: { role?: string; skills?: string[]; experience_min?: number; location?: string }) => void;
  onChanged?: () => void;
}) {
  const [role, setRole] = useState("");
  const [skills, setSkills] = useState("");
  const [exp, setExp] = useState("");
  const [location, setLocation] = useState("");

  return (
    <Section
      title="Talent Hunt"
      subtitle="Outbound search by skills, role, experience & location (Apollo / scraper)"
      count={rows.length}
      rows={rows}
      onChanged={onChanged}
      empty="No hunted candidates yet. Set criteria and hunt below."
    >
      <div className="mb-4 grid grid-cols-1 gap-2 rounded-lg border border-slate-200 bg-white p-3 sm:grid-cols-5">
        <input value={role} onChange={(e) => setRole(e.target.value)} placeholder="Role"
          className="rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-xs text-slate-900 outline-none focus:border-indigo-500" />
        <input value={skills} onChange={(e) => setSkills(e.target.value)} placeholder="Skills (comma)"
          className="rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-xs text-slate-900 outline-none focus:border-indigo-500 sm:col-span-2" />
        <input value={exp} onChange={(e) => setExp(e.target.value)} placeholder="Min exp (yrs)" type="number"
          className="rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-xs text-slate-900 outline-none focus:border-indigo-500" />
        <input value={location} onChange={(e) => setLocation(e.target.value)} placeholder="Location"
          className="rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-xs text-slate-900 outline-none focus:border-indigo-500" />
      </div>
      <div className="mb-4">
        <button
          onClick={() =>
            onHunt({
              role: role.trim() || undefined,
              skills: skills.split(",").map((s) => s.trim()).filter(Boolean),
              experience_min: exp ? Number(exp) : undefined,
              location: location.trim() || undefined,
            })
          }
          disabled={busy}
          className="rounded-lg bg-indigo-600 px-4 py-1.5 text-xs font-medium text-white transition hover:bg-indigo-700 disabled:opacity-50"
        >
          {busy ? "Hunting…" : "Hunt candidates"}
        </button>
      </div>
    </Section>
  );
}

export default function CandidatesPage() {
  return (
    <Suspense fallback={<p className="text-sm text-slate-500">Loading…</p>}>
      <CandidatesInner />
    </Suspense>
  );
}
