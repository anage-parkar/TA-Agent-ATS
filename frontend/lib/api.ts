// Thin client for the FastAPI backend.

const BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8001";

/** Resolve a resume/file URL: absolute (e.g. Google Drive) as-is, else prefix the API host. */
export function fileHref(url?: string | null): string | undefined {
  if (!url) return undefined;
  return /^https?:\/\//i.test(url) ? url : `${BASE}${url}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    cache: "no-store",
    ...init,
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

// ── Types ───────────────────────────────────────────────────────────
export interface ATSBreakdown {
  skill_match: number;
  experience_fit: number;
  location_match: number;
  tech_stack_overlap: number;
  overall_score: number;
  reasoning: string;
}

export interface CandidateProfile {
  full_name: string;
  linkedin_url?: string;
  email?: string;
  headline?: string;
  skills: string[];
  experience_years?: number;
  location?: string;
}

export interface ScoredCandidate {
  application_id: string;
  candidate: CandidateProfile;
  ats_score: number;
  ats_breakdown: ATSBreakdown;
}

export interface Job {
  id: string;
  title: string;
  seniority?: string;
  location?: string;
  skills?: string[];
}

// A stored application row (any channel), as returned by GET /api/candidates.
export interface ApplicationRow {
  id: string; // application id
  candidate: CandidateProfile & {
    phone?: string;
    resume_url?: string;
    raw_profile?: any;
    enrichment?: any;
  };
  ats_score: number | null;
  ats_breakdown: ATSBreakdown | null;
  status: string;
  stage?: string | null;
  source: string;
  job_title?: string;
  job_id?: string;
}

export interface ChannelSummary {
  channel: string;
  label: string;
  subtitle: string;
  count: number;
}

export interface ChannelJob {
  job_id: string;
  title: string;
  location?: string;
  count: number;
  scored: number;
  reviewed: number;
}

// Which application `source` values belong to each channel (mirror of backend).
export const CHANNEL_SOURCES: Record<string, string[]> = {
  linkedin: ["offsite_form", "linkedin_apply_connect", "linkedin_mock"],
  forms: ["google_form", "microsoft_form"],
  "talent-hunt": ["talent_hunt", "manual", "sourced"],
  website: ["website_portal"],
};

export interface TalentHuntCriteria {
  role?: string;
  skills?: string[];
  experience_min?: number;
  location?: string;
  limit?: number;
}

// ── Endpoints ───────────────────────────────────────────────────────
export const api = {
  syncJob: (linkedin_url: string) =>
    request<{ job_id: string; parsed_fields: any }>("/api/jobs/sync", {
      method: "POST",
      body: JSON.stringify({ linkedin_url }),
    }),

  listJobs: () => request<{ jobs: Job[] }>("/api/jobs"),

  getJob: (job_id: string) => request<Job>(`/api/jobs/${encodeURIComponent(job_id)}`),

  ensureJob: (title: string) =>
    request<{ job_id: string; title: string }>("/api/jobs/ensure", {
      method: "POST",
      body: JSON.stringify({ title }),
    }),

  sourceCandidates: (job_id: string, limit = 10) =>
    request<{ job_id: string; count: number; candidates: ScoredCandidate[] }>(
      "/api/candidates/source",
      { method: "POST", body: JSON.stringify({ job_id, limit }) }
    ),

  scoreApplicants: (job_id: string) =>
    request<{ job_id: string; scored: number; candidates: ScoredCandidate[] }>(
      `/api/jobs/${encodeURIComponent(job_id)}/score-applicants`,
      { method: "POST" }
    ),

  listCandidates: (job_id: string) =>
    request<{ job_id: string; candidates: ApplicationRow[] }>(
      `/api/candidates?job_id=${encodeURIComponent(job_id)}`
    ),

  talentHunt: (job_id: string, criteria: TalentHuntCriteria) =>
    request<{ count: number }>(`/api/jobs/${encodeURIComponent(job_id)}/talent-hunt`, {
      method: "POST",
      body: JSON.stringify(criteria),
    }),

  // formRef may be a full Google Form URL or a bare form ID.
  syncForms: (job_id: string, formRef?: string) =>
    request<{ count: number }>(`/api/jobs/${encodeURIComponent(job_id)}/sync-forms`, {
      method: "POST",
      body: JSON.stringify(formRef ? { form_id: formRef } : {}),
    }),

  decide: (application_id: string, decision: "proceed" | "reject") =>
    request<{ application_id: string; status: string }>(
      `/api/candidates/${application_id}/decision`,
      { method: "POST", body: JSON.stringify({ decision }) }
    ),

  draftEmail: (application_id: string, decision: "proceed" | "reject", use_ai = false) =>
    request<{ decision: string; to?: string; subject: string; body: string }>(
      `/api/applications/${application_id}/draft-email`,
      { method: "POST", body: JSON.stringify({ decision, use_ai }) }
    ),

  sendEmail: (
    application_id: string,
    decision: "proceed" | "reject",
    subject: string,
    body: string
  ) =>
    request<{ ok: boolean; sent: boolean; status: string; detail?: string }>(
      `/api/applications/${application_id}/send-email`,
      { method: "POST", body: JSON.stringify({ decision, subject, body }) }
    ),

  pollReplies: () =>
    request<{ ok: boolean; replies?: number; advanced_to_replied?: number; detail?: string }>(
      "/api/emails/poll-replies",
      { method: "POST" }
    ),

  dashboardSummary: () =>
    request<{ channels: ChannelSummary[]; jobs: number }>("/api/dashboard/summary"),

  channel: (channel: string) =>
    request<{ channel: string; label: string; subtitle: string; candidates: ApplicationRow[] }>(
      `/api/channels/${channel}`
    ),

  channelJobs: (channel: string) =>
    request<{ channel: string; label: string; subtitle: string; jobs: ChannelJob[] }>(
      `/api/channels/${channel}/jobs`
    ),

  syncWebsiteJobs: () =>
    request<{ count: number; jobs: { job_id: string; title: string }[] }>(
      "/api/website/sync-jobs",
      { method: "POST" }
    ),

  setStage: (application_id: string, stage: string) =>
    request<{ application_id: string; stage: string }>(
      `/api/applications/${application_id}/stage`,
      { method: "POST", body: JSON.stringify({ stage }) }
    ),

  applicationDetail: (application_id: string) =>
    request<ApplicationRow & { job_title?: string }>(`/api/applications/${application_id}`),

  enrichCandidate: (candidate_id: string) =>
    request<{ ok: boolean; enrichment?: any; detail?: string }>(
      `/api/candidates/${candidate_id}/enrich`,
      { method: "POST" }
    ),
};
