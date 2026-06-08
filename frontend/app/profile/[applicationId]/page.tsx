"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { api, fileHref, type ApplicationRow } from "@/lib/api";
import { EmailDraftModal } from "@/components/EmailDraftModal";

const STAGE_ORDER = ["Sourced", "Reviewed", "Outreach", "Replied", "Interview", "Offer"];

export default function ProfilePage() {
  const { applicationId } = useParams<{ applicationId: string }>();
  const router = useRouter();
  const [data, setData] = useState<ApplicationRow | null>(null);
  const [loading, setLoading] = useState(true);
  const [modal, setModal] = useState<"proceed" | "reject" | null>(null);
  const [enriching, setEnriching] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await api.applicationDetail(applicationId));
    } catch {
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [applicationId]);

  useEffect(() => {
    load();
  }, [load]);

  if (loading) return <p className="text-sm text-slate-500">Loading…</p>;
  if (!data) return <p className="text-sm text-slate-500">Candidate not found.</p>;

  const c = data.candidate;
  const b = data.ats_breakdown;
  const e = c.enrichment as any;
  const stage = data.stage || "Sourced";
  const rejected = data.status === "rejected";

  async function enrich() {
    const cid = (c as any).id;
    setEnriching(true);
    setMsg(null);
    try {
      const r = await api.enrichCandidate(cid);
      setMsg(r.ok ? "Enriched ✓" : r.detail || "Enrichment unavailable");
      if (r.ok) await load();
    } catch (err) {
      setMsg((err as Error).message);
    } finally {
      setEnriching(false);
    }
  }

  return (
    <div className="pb-12">
      <button onClick={() => router.back()} className="text-sm text-indigo-600">← Back</button>

      {/* Header */}
      <div className="mt-3 flex items-start gap-5 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
        {e?.image_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={e.image_url} alt={c.full_name} className="h-20 w-20 rounded-full border border-slate-200 object-cover" />
        ) : (
          <span className="grid h-20 w-20 place-items-center rounded-full bg-slate-100 text-2xl font-semibold text-slate-400">
            {(c.full_name || "?").slice(0, 1).toUpperCase()}
          </span>
        )}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-2xl font-bold text-slate-900">{c.full_name}</h1>
            {data.ats_score != null && (
              <span className="rounded-full bg-indigo-50 px-3 py-1 text-sm font-semibold text-indigo-700">
                ATS {Math.round(data.ats_score)}
              </span>
            )}
          </div>
          {c.headline && <p className="text-slate-500">{c.headline}</p>}
          <div className="mt-1 flex flex-wrap gap-x-3 text-sm text-slate-400">
            {c.location && <span>{c.location}</span>}
            {data.job_title && <span>· {data.job_title}</span>}
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-2">
          {rejected ? (
            <span className="rounded-lg bg-rose-50 px-3 py-1.5 text-sm font-medium text-rose-700">Rejected</span>
          ) : data.status === "approved" ? (
            <div className="flex gap-2">
              <button onClick={() => setModal("reject")} className="btn-ghost">Reject</button>
            </div>
          ) : (
            <div className="flex gap-2">
              <button onClick={() => setModal("proceed")} className="btn-primary">Proceed</button>
              <button onClick={() => setModal("reject")} className="btn-ghost">Reject</button>
            </div>
          )}
        </div>
      </div>

      {/* Stage tracker */}
      {!rejected && (
        <div className="mt-4 flex flex-wrap items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
          {STAGE_ORDER.map((s, i) => {
            const reached = STAGE_ORDER.indexOf(stage) >= i;
            const current = stage === s;
            return (
              <div key={s} className="flex items-center">
                <span
                  className={`rounded-full px-3 py-1 text-xs font-medium ${
                    current
                      ? "bg-indigo-600 text-white"
                      : reached
                      ? "bg-indigo-50 text-indigo-700"
                      : "bg-slate-100 text-slate-400"
                  }`}
                >
                  {s}
                </span>
                {i < STAGE_ORDER.length - 1 && (
                  <span className={`mx-1 text-xs ${reached ? "text-indigo-300" : "text-slate-300"}`}>→</span>
                )}
              </div>
            );
          })}
        </div>
      )}

      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* Left: contact + skills + resume */}
        <div className="space-y-4 lg:col-span-1">
          <Card title="Contact">
            <Row label="Email" value={c.email} />
            <Row label="Phone" value={(c as any).phone} />
            <Row label="Location" value={c.location} />
            {c.linkedin_url && (
              <Row label="LinkedIn" value={<a className="text-indigo-600 underline" href={c.linkedin_url} target="_blank" rel="noreferrer">Profile</a>} />
            )}
            {(c as any).resume_url && (
              <Row label="Resume" value={<a className="text-indigo-600 underline" href={fileHref((c as any).resume_url)} target="_blank" rel="noreferrer">Open resume</a>} />
            )}
            <Row label="Source" value={data.source} />
          </Card>

          {c.skills && c.skills.length > 0 && (
            <Card title={`Skills (${c.skills.length})`}>
              <div className="flex flex-wrap gap-1">
                {c.skills.map((s) => (
                  <span key={s} className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">{s}</span>
                ))}
              </div>
            </Card>
          )}

          {b && (
            <Card title="ATS breakdown">
              <Bar label="Skill match" v={b.skill_match} />
              <Bar label="Experience fit" v={b.experience_fit} />
              <Bar label="Location match" v={b.location_match} />
              <Bar label="Tech stack overlap" v={b.tech_stack_overlap} />
              <p className="mt-2 text-xs italic text-slate-500">{b.reasoning}</p>
            </Card>
          )}
        </div>

        {/* Right: LinkedIn enrichment */}
        <div className="space-y-4 lg:col-span-2">
          <Card
            title="LinkedIn enrichment"
            action={
              <button onClick={enrich} disabled={enriching || !c.linkedin_url} className="btn-ghost text-xs">
                {enriching ? "Enriching…" : "Enrich from LinkedIn"}
              </button>
            }
          >
            {msg && <p className="mb-2 text-xs text-slate-500">{msg}</p>}
            {!e ? (
              <p className="text-sm text-slate-400">Not enriched yet. Click “Enrich from LinkedIn” (or it runs on Score).</p>
            ) : (
              <div className="space-y-4 text-sm">
                {e.about && <p className="text-slate-600">{e.about}</p>}
                {Array.isArray(e.experiences) && e.experiences.length > 0 && (
                  <div>
                    <p className="mb-1 text-xs font-semibold uppercase text-slate-400">Experience ({e.experiences.length})</p>
                    <div className="space-y-2">
                      {e.experiences.map((x: any, i: number) => (
                        <div key={i}>
                          <p className="text-slate-800">
                            {x.position_title || x.title}
                            <span className="text-slate-500"> · {(x.institution_name || x.company || "").replace(/\s*·.*$/, "")}</span>
                          </p>
                          <p className="text-[11px] text-slate-400">
                            {[x.from_date, x.to_date].filter(Boolean).join(" – ")}{x.duration ? ` (${x.duration})` : ""}
                          </p>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                {Array.isArray(e.educations) && e.educations.length > 0 && (
                  <div>
                    <p className="mb-1 text-xs font-semibold uppercase text-slate-400">Education</p>
                    {Array.from(new Map(e.educations.map((x: any) => [`${x.institution_name}|${x.degree}`, x])).values()).map((x: any, i: number) => (
                      <div key={i}>
                        <p className="text-slate-800">{x.institution_name}</p>
                        {x.degree && <p className="text-[11px] text-slate-400">{x.degree}</p>}
                      </div>
                    ))}
                  </div>
                )}
                {Array.isArray(e.skills) && e.skills.length > 0 && (
                  <div>
                    <p className="mb-1 text-xs font-semibold uppercase text-slate-400">LinkedIn skills ({e.skills.length})</p>
                    <div className="flex flex-wrap gap-1">
                      {e.skills.map((s: string) => (
                        <span key={s} className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">{s}</span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </Card>
        </div>
      </div>

      {modal && (
        <EmailDraftModal
          applicationId={applicationId}
          decision={modal}
          onClose={() => setModal(null)}
          onDone={() => load()}
        />
      )}
    </div>
  );
}

function Card({ title, action, children }: { title: string; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="mb-3 flex items-center justify-between">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">{title}</p>
        {action}
      </div>
      {children}
    </div>
  );
}

function Row({ label, value }: { label: string; value?: React.ReactNode }) {
  if (!value) return null;
  return (
    <div className="flex justify-between gap-3 py-1 text-sm">
      <span className="text-slate-400">{label}</span>
      <span className="text-right text-slate-700">{value}</span>
    </div>
  );
}

function Bar({ label, v }: { label: string; v: number }) {
  return (
    <div className="mb-1 flex items-center justify-between gap-2 text-xs">
      <span className="text-slate-500">{label}</span>
      <div className="flex items-center gap-2">
        <div className="h-1.5 w-28 rounded-full bg-slate-100">
          <div className="h-1.5 rounded-full bg-indigo-500" style={{ width: `${Math.round(v * 100)}%` }} />
        </div>
        <span className="w-8 text-right text-slate-600">{Math.round(v * 100)}%</span>
      </div>
    </div>
  );
}
