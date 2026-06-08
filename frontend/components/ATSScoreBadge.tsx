import type { ATSBreakdown } from "@/lib/api";

function color(score: number): string {
  if (score >= 80) return "bg-emerald-50 text-emerald-700 border-emerald-200";
  if (score >= 60) return "bg-amber-50 text-amber-700 border-amber-200";
  return "bg-rose-50 text-rose-700 border-rose-200";
}

export function ATSScoreBadge({
  score,
  breakdown,
}: {
  score: number | null;
  breakdown?: ATSBreakdown | null;
}) {
  if (score === null || score === undefined || Number.isNaN(score)) {
    return (
      <span className="inline-flex h-12 w-12 items-center justify-center rounded-full border border-slate-200 bg-slate-50 text-[10px] font-semibold uppercase text-slate-400">
        New
      </span>
    );
  }

  const rows: [string, number][] = breakdown
    ? [
        ["Skill match", breakdown.skill_match],
        ["Experience fit", breakdown.experience_fit],
        ["Location match", breakdown.location_match],
        ["Tech stack overlap", breakdown.tech_stack_overlap],
      ]
    : [];

  return (
    <div className="group relative inline-block">
      <span
        className={`inline-flex h-12 w-12 items-center justify-center rounded-full border text-sm font-bold ${color(
          score
        )}`}
      >
        {Math.round(score)}
      </span>

      {breakdown && (
        <div className="invisible absolute left-0 z-30 mt-2 w-64 rounded-lg border border-slate-200 bg-white p-3 text-xs shadow-xl group-hover:visible">
          <div className="mb-2 font-semibold text-slate-700">Score breakdown</div>
          {rows.map(([label, val]) => (
            <div key={label} className="mb-1 flex items-center justify-between gap-2">
              <span className="text-slate-500">{label}</span>
              <div className="flex items-center gap-2">
                <div className="h-1.5 w-20 rounded-full bg-slate-100">
                  <div
                    className="h-1.5 rounded-full bg-indigo-500"
                    style={{ width: `${Math.round(val * 100)}%` }}
                  />
                </div>
                <span className="w-8 text-right text-slate-600">{Math.round(val * 100)}%</span>
              </div>
            </div>
          ))}
          <p className="mt-2 border-t border-slate-100 pt-2 italic text-slate-500">
            {breakdown.reasoning}
          </p>
        </div>
      )}
    </div>
  );
}
