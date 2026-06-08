"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import type { ApplicationRow } from "@/lib/api";
import { fileHref } from "@/lib/api";
import { ATSScoreBadge } from "./ATSScoreBadge";
import { EmailDraftModal } from "./EmailDraftModal";

const SOURCE_LABEL: Record<string, string> = {
  offsite_form: "LinkedIn apply",
  linkedin_apply_connect: "Apply Connect",
  linkedin_mock: "LinkedIn",
  google_form: "Google Form",
  microsoft_form: "MS Form",
  talent_hunt: "Talent Hunt",
  manual: "Sourced",
  sourced: "Sourced",
};

export function CandidateCard({
  item,
  onChanged,
  showJob = false,
}: {
  item: ApplicationRow;
  onChanged?: () => void;
  showJob?: boolean;
}) {
  const router = useRouter();
  const { candidate, ats_score, ats_breakdown, id, source } = item;
  const initial =
    item.status === "rejected" ? "rejected" : item.status === "approved" ? "approved" : "open";
  const [status, setStatus] = useState<"open" | "approved" | "rejected">(initial);
  const [modal, setModal] = useState<"proceed" | "reject" | null>(null);

  function openModal(e: React.MouseEvent, decision: "proceed" | "reject") {
    e.stopPropagation();
    setModal(decision);
  }

  return (
    <div
      onClick={() => router.push(`/profile/${id}`)}
      className="flex cursor-pointer items-start gap-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm transition hover:border-indigo-300 hover:shadow-md"
    >
      <ATSScoreBadge score={ats_score} breakdown={ats_breakdown} />

      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="truncate font-semibold text-slate-900">{candidate.full_name}</h3>
          {candidate.experience_years != null && (
            <span className="text-xs text-slate-400">{candidate.experience_years} yrs</span>
          )}
          <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-indigo-600">
            {SOURCE_LABEL[source] ?? source}
          </span>
          {item.stage && status === "open" && (
            <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-slate-500">
              {item.stage}
            </span>
          )}
          {showJob && item.job_title && (
            <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-500">
              {item.job_title}
            </span>
          )}
        </div>
        {candidate.headline && (
          <p className="truncate text-sm text-slate-500">{candidate.headline}</p>
        )}
        <div className="mt-0.5 flex flex-wrap gap-x-3 text-xs text-slate-400">
          {candidate.location && <span>{candidate.location}</span>}
          {candidate.email && <span>{candidate.email}</span>}
          {candidate.phone && <span>{candidate.phone}</span>}
          {candidate.resume_url && (
            <a
              href={fileHref(candidate.resume_url)}
              target="_blank"
              rel="noreferrer"
              onClick={(e) => e.stopPropagation()}
              className="text-indigo-600 underline"
            >
              Resume
            </a>
          )}
        </div>
        <div className="mt-2 flex flex-wrap gap-1">
          {(candidate.skills ?? []).slice(0, 6).map((s) => (
            <span key={s} className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">
              {s}
            </span>
          ))}
        </div>
      </div>

      <div className="flex shrink-0 flex-col gap-2">
        {status === "open" ? (
          <>
            <button
              onClick={(e) => openModal(e, "proceed")}
              className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-indigo-700"
            >
              Proceed
            </button>
            <button
              onClick={(e) => openModal(e, "reject")}
              className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-600 transition hover:border-rose-300 hover:text-rose-600"
            >
              Reject
            </button>
          </>
        ) : status === "rejected" ? (
          <span className="rounded-lg bg-rose-50 px-3 py-1.5 text-center text-sm font-medium text-rose-700">
            Rejected
          </span>
        ) : (
          // Approved → show the CURRENT pipeline stage (Reviewed → Outreach → …)
          <span className="rounded-lg bg-emerald-50 px-3 py-1.5 text-center text-sm font-medium text-emerald-700">
            {item.stage && item.stage !== "Sourced" ? item.stage : "Reviewed"}
          </span>
        )}
      </div>

      {modal && (
        <div onClick={(e) => e.stopPropagation()}>
          <EmailDraftModal
            applicationId={id}
            decision={modal}
            onClose={() => setModal(null)}
            onDone={(s) => {
              setStatus(s);
              onChanged?.(); // reload list so the card reflects the new stage
            }}
          />
        </div>
      )}
    </div>
  );
}
