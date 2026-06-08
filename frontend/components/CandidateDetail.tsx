"use client";

import { useEffect, useState } from "react";
import type { ApplicationRow } from "@/lib/api";
import { api } from "@/lib/api";

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8001";

export function CandidateDetail({
  applicationId,
  onClose,
}: {
  applicationId: string | null;
  onClose: () => void;
}) {
  const [data, setData] = useState<ApplicationRow | null>(null);
  const [loading, setLoading] = useState(false);
  const [enriching, setEnriching] = useState(false);
  const [enrichMsg, setEnrichMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!applicationId) return;
    setData(null);
    setEnrichMsg(null);
    setLoading(true);
    api
      .applicationDetail(applicationId)
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [applicationId]);

  if (!applicationId) return null;

  const c = data?.candidate;
  const b = data?.ats_breakdown;
  const enrichment = c?.enrichment;

  async function enrich() {
    if (!c) return;
    const cid = (c as any).id;
    setEnriching(true);
    setEnrichMsg(null);
    try {
      const res = await api.enrichCandidate(cid);
      if (res.ok) {
        setEnrichMsg("Enriched from LinkedIn ✓");
        const fresh = await api.applicationDetail(applicationId!);
        setData(fresh);
      } else {
        setEnrichMsg(res.detail || "Enrichment unavailable");
      }
    } catch (err) {
      setEnrichMsg((err as Error).message);
    } finally {
      setEnriching(false);
    }
  }

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <div className="absolute inset-0 bg-slate-900/30" onClick={onClose} />
      <aside className="relative h-full w-full max-w-md overflow-y-auto bg-white shadow-2xl">
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
          <h2 className="font-semibold text-slate-900">Candidate</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-700">✕</button>
        </div>

        {loading && <p className="p-5 text-sm text-slate-500">Loading…</p>}

        {c && (
          <div className="space-y-5 p-5">
            <div>
              <div className="flex items-center gap-3">
                {enrichment?.image_url ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={enrichment.image_url}
                    alt={c.full_name}
                    className="h-12 w-12 shrink-0 rounded-full border border-slate-200 object-cover"
                  />
                ) : (
                  <span className="grid h-12 w-12 shrink-0 place-items-center rounded-full bg-slate-100 text-sm font-semibold text-slate-400">
                    {(c.full_name || "?").slice(0, 1).toUpperCase()}
                  </span>
                )}
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <h3 className="truncate text-lg font-bold text-slate-900">{c.full_name}</h3>
                    {data?.ats_score != null && (
                      <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-sm font-semibold text-indigo-700">
                        {Math.round(data.ats_score)}
                      </span>
                    )}
                  </div>
                  {c.headline && <p className="truncate text-sm text-slate-500">{c.headline}</p>}
                </div>
              </div>
              {data?.job_title && (
                <p className="mt-1 text-xs text-slate-400">Applied to: {data.job_title}</p>
              )}
              <div className="mt-2 flex items-center gap-2">
                {data?.stage && (
                  <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-xs font-medium text-indigo-700">
                    Stage: {data.stage}
                  </span>
                )}
                {data?.status && (
                  <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-500">
                    {data.status}
                  </span>
                )}
              </div>
            </div>

            <Section title="Contact">
              <Row label="Email" value={c.email} />
              <Row label="Phone" value={(c as any).phone} />
              <Row label="Location" value={c.location} />
              {c.linkedin_url && (
                <Row
                  label="LinkedIn"
                  value={
                    <a href={c.linkedin_url} target="_blank" rel="noreferrer" className="text-indigo-600 underline">
                      {c.linkedin_url}
                    </a>
                  }
                />
              )}
              {(c as any).resume_url && (
                <Row
                  label="Resume"
                  value={
                    <a href={`${API}${(c as any).resume_url}`} target="_blank" rel="noreferrer" className="text-indigo-600 underline">
                      Open resume
                    </a>
                  }
                />
              )}
            </Section>

            {c.skills && c.skills.length > 0 && (
              <Section title="Skills">
                <div className="flex flex-wrap gap-1">
                  {c.skills.map((s) => (
                    <span key={s} className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">{s}</span>
                  ))}
                </div>
              </Section>
            )}

            {b && (
              <Section title="ATS breakdown">
                <Bar label="Skill match" v={b.skill_match} />
                <Bar label="Experience fit" v={b.experience_fit} />
                <Bar label="Location match" v={b.location_match} />
                <Bar label="Tech stack overlap" v={b.tech_stack_overlap} />
                <p className="mt-2 text-xs italic text-slate-500">{b.reasoning}</p>
              </Section>
            )}

            {/* LinkedIn enrichment */}
            <Section title="LinkedIn enrichment">
              {enrichment ? (
                <div className="space-y-3 text-sm">
                  {enrichment.headline && (
                    <p className="font-medium text-slate-700">{enrichment.headline}</p>
                  )}
                  {enrichment.about && <p className="text-slate-600">{enrichment.about}</p>}

                  {Array.isArray(enrichment.experiences) && enrichment.experiences.length > 0 && (
                    <div>
                      <p className="mb-1 text-xs font-semibold text-slate-500">
                        Experience ({enrichment.experiences.length})
                      </p>
                      <div className="space-y-1.5">
                        {enrichment.experiences.map((e: any, i: number) => {
                          const role = e.position_title || e.title || e.position;
                          const company = (e.institution_name || e.company || "").replace(/\s*·.*$/, "");
                          const span = [e.from_date, e.to_date].filter(Boolean).join(" – ");
                          return (
                            <div key={i}>
                              <p className="text-slate-800">
                                {role}
                                {company ? <span className="text-slate-500"> · {company}</span> : null}
                              </p>
                              {(span || e.duration) && (
                                <p className="text-[11px] text-slate-400">
                                  {span}
                                  {e.duration ? ` (${e.duration})` : ""}
                                  {e.location ? ` · ${e.location.replace(/\s*·.*$/, "")}` : ""}
                                </p>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}

                  {Array.isArray(enrichment.educations) && enrichment.educations.length > 0 && (
                    <div>
                      <p className="mb-1 text-xs font-semibold text-slate-500">Education</p>
                      <div className="space-y-1.5">
                        {/* de-dupe by institution+degree */}
                        {Array.from(
                          new Map(
                            enrichment.educations.map((e: any) => [
                              `${e.institution_name}|${e.degree}`,
                              e,
                            ])
                          ).values()
                        ).map((e: any, i: number) => (
                          <div key={i}>
                            <p className="text-slate-800">{e.institution_name}</p>
                            {e.degree && <p className="text-[11px] text-slate-400">{e.degree}</p>}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {enrichment.certifications && (
                    <div>
                      <p className="mb-1 text-xs font-semibold text-slate-500">Certifications</p>
                      <pre className="whitespace-pre-wrap text-[11px] text-slate-600">
                        {typeof enrichment.certifications === "string"
                          ? enrichment.certifications
                          : JSON.stringify(enrichment.certifications, null, 1)}
                      </pre>
                    </div>
                  )}

                  {Array.isArray(enrichment.skills) && enrichment.skills.length > 0 && (
                    <div>
                      <p className="mb-1 text-xs font-semibold text-slate-500">
                        Skills ({enrichment.skills.length})
                      </p>
                      <div className="flex flex-wrap gap-1">
                        {enrichment.skills.map((s: string) => (
                          <span key={s} className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">{s}</span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              ) : (
                <p className="text-xs text-slate-400">
                  Not enriched yet. Pulls public profile data via LinkedIn (uses your logged-in
                  session — opt-in).
                </p>
              )}
              <button
                onClick={enrich}
                disabled={enriching || !c.linkedin_url}
                className="mt-3 rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 transition hover:bg-slate-50 disabled:opacity-50"
                title={!c.linkedin_url ? "No LinkedIn URL on this candidate" : ""}
              >
                {enriching ? "Enriching…" : "Enrich from LinkedIn"}
              </button>
              {enrichMsg && <p className="mt-2 text-xs text-slate-500">{enrichMsg}</p>}
            </Section>
          </div>
        )}
      </aside>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">{title}</p>
      {children}
    </div>
  );
}

function Row({ label, value }: { label: string; value?: React.ReactNode }) {
  if (!value) return null;
  return (
    <div className="flex justify-between gap-3 py-0.5 text-sm">
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
        <div className="h-1.5 w-24 rounded-full bg-slate-100">
          <div className="h-1.5 rounded-full bg-indigo-500" style={{ width: `${Math.round(v * 100)}%` }} />
        </div>
        <span className="w-8 text-right text-slate-600">{Math.round(v * 100)}%</span>
      </div>
    </div>
  );
}
