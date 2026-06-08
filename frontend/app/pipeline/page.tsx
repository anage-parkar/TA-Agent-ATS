"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, type ChannelSummary } from "@/lib/api";

const META: Record<string, { icon: string; accent: string }> = {
  linkedin: { icon: "in", accent: "bg-sky-50 text-sky-700 ring-sky-100" },
  forms: { icon: "≡", accent: "bg-violet-50 text-violet-700 ring-violet-100" },
  "talent-hunt": { icon: "⌖", accent: "bg-emerald-50 text-emerald-700 ring-emerald-100" },
  website: { icon: "🌐", accent: "bg-amber-50 text-amber-700 ring-amber-100" },
};

export default function PipelinePage() {
  const [channels, setChannels] = useState<ChannelSummary[]>([]);

  useEffect(() => {
    api.dashboardSummary().then((d) => setChannels(d.channels)).catch(() => {});
  }, []);

  return (
    <div>
      <h1 className="text-2xl font-bold text-slate-900">Pipeline</h1>
      <p className="mb-8 text-sm text-slate-500">
        Open a channel to manage its board — drag candidates across Sourced → Reviewed → Outreach →
        Replied → Interview → Offer.
      </p>

      <div className="grid grid-cols-1 gap-5 sm:grid-cols-3">
        {channels.map((ch) => {
          const m = META[ch.channel] ?? { icon: "•", accent: "bg-slate-50 text-slate-700 ring-slate-100" };
          return (
            <Link
              key={ch.channel}
              href={`/pipeline/${ch.channel}`}
              className="group rounded-2xl border border-slate-200 bg-white p-6 shadow-sm transition hover:-translate-y-0.5 hover:border-indigo-300 hover:shadow-md"
            >
              <div className="flex items-center justify-between">
                <span className={`grid h-11 w-11 place-items-center rounded-xl text-lg font-semibold ring-4 ${m.accent}`}>
                  {m.icon}
                </span>
                <span className="text-3xl font-bold text-slate-900">{ch.count}</span>
              </div>
              <h2 className="mt-4 font-semibold text-slate-900 group-hover:text-indigo-700">
                {ch.label}
              </h2>
              <p className="mt-1 text-sm text-slate-500">Open Kanban board →</p>
            </Link>
          );
        })}
      </div>
    </div>
  );
}
