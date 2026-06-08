"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { api, type ApplicationRow } from "@/lib/api";

const STAGES = ["Sourced", "Reviewed", "Outreach", "Replied", "Interview", "Offer"] as const;
type Stage = (typeof STAGES)[number];

function deriveStage(status: string): Stage | null {
  switch (status) {
    case "applied":
    case "sourced":
    case "scored":
      return "Sourced";
    case "approved":
      return "Reviewed";
    case "outreach_sent":
      return "Outreach";
    case "replied":
      return "Replied";
    case "interview_scheduled":
      return "Interview";
    case "offer":
      return "Offer";
    default:
      return null;
  }
}

function effectiveStage(r: ApplicationRow): Stage | null {
  return (r.stage as Stage) || deriveStage(r.status);
}

export default function ChannelPipelinePage() {
  const { channel } = useParams<{ channel: string }>();
  const router = useRouter();
  const [label, setLabel] = useState("");
  const [rows, setRows] = useState<ApplicationRow[]>([]);
  const [dragId, setDragId] = useState<string | null>(null);
  const [over, setOver] = useState<Stage | null>(null);

  const load = useCallback(async () => {
    const res = await api.channel(channel);
    setLabel(res.label);
    setRows(res.candidates.filter((r) => r.status !== "rejected"));
  }, [channel]);

  useEffect(() => {
    load();
  }, [load]);

  const [checking, setChecking] = useState(false);
  const [pollMsg, setPollMsg] = useState<string | null>(null);

  async function checkReplies() {
    setChecking(true);
    setPollMsg(null);
    try {
      const res = await api.pollReplies();
      if (!res.ok) setPollMsg(res.detail || "Email not configured.");
      else setPollMsg(`${res.replies ?? 0} new reply(ies), ${res.advanced_to_replied ?? 0} advanced to Replied.`);
      await load();
    } catch (e) {
      setPollMsg((e as Error).message);
    } finally {
      setChecking(false);
    }
  }

  async function move(id: string, stage: Stage) {
    const prev = rows;
    // optimistic
    setRows((rs) => rs.map((r) => (r.id === id ? { ...r, stage } : r)));
    try {
      await api.setStage(id, stage);
    } catch {
      setRows(prev); // revert on failure
    }
  }

  return (
    <div>
      <Link href="/pipeline" className="text-sm text-indigo-600">← Pipeline</Link>
      <div className="mb-5 mt-2 flex items-center justify-between">
        <h1 className="text-2xl font-bold text-slate-900">{label || "Board"}</h1>
        <div className="flex items-center gap-3">
          {pollMsg && <span className="text-xs text-slate-500">{pollMsg}</span>}
          <span className="text-sm text-slate-400">
            {rows.length} candidate{rows.length === 1 ? "" : "s"}
          </span>
          <button onClick={checkReplies} disabled={checking} className="btn-ghost">
            {checking ? "Checking…" : "Check replies"}
          </button>
        </div>
      </div>

      <div className="-mx-6 overflow-x-auto px-6 pb-4">
        <div className="flex gap-3" style={{ minWidth: "1100px" }}>
          {STAGES.map((stage) => {
            const cards = rows.filter((r) => effectiveStage(r) === stage);
            return (
              <div
                key={stage}
                onDragOver={(e) => {
                  e.preventDefault();
                  setOver(stage);
                }}
                onDragLeave={() => setOver((o) => (o === stage ? null : o))}
                onDrop={(e) => {
                  e.preventDefault();
                  setOver(null);
                  if (dragId) move(dragId, stage);
                  setDragId(null);
                }}
                className={`flex-1 rounded-xl border p-2 transition ${
                  over === stage ? "border-indigo-400 bg-indigo-50/50" : "border-slate-200 bg-slate-50/60"
                }`}
              >
                <div className="mb-2 flex items-center justify-between px-1">
                  <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                    {stage}
                  </span>
                  <span className="rounded-full bg-white px-1.5 text-xs text-slate-500 ring-1 ring-slate-200">
                    {cards.length}
                  </span>
                </div>
                <div className="space-y-2">
                  {cards.map((r) => (
                    <div
                      key={r.id}
                      draggable
                      onDragStart={() => setDragId(r.id)}
                      onDragEnd={() => setDragId(null)}
                      onClick={() => router.push(`/profile/${r.id}`)}
                      className={`cursor-grab rounded-lg border border-slate-200 bg-white p-2 shadow-sm transition hover:border-indigo-300 active:cursor-grabbing ${
                        dragId === r.id ? "opacity-50" : ""
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate text-sm font-medium text-slate-800">
                          {r.candidate.full_name}
                        </span>
                        {r.ats_score != null && (
                          <span className="shrink-0 text-xs font-semibold text-indigo-600">
                            {Math.round(r.ats_score)}
                          </span>
                        )}
                      </div>
                      {r.job_title && (
                        <p className="truncate text-[11px] text-slate-400">{r.job_title}</p>
                      )}
                    </div>
                  ))}
                  {cards.length === 0 && (
                    <p className="px-1 py-2 text-[11px] text-slate-300">Drop here</p>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <p className="mt-2 text-xs text-slate-400">
        Drag a card between columns to move its stage · click a card for details.
      </p>

    </div>
  );
}
