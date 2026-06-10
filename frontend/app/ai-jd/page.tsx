"use client";

import { useEffect, useRef, useState } from "react";
import { api, type GeneratedJD, type JDMetadata } from "@/lib/api";

// ── Experience → designation helper (mirrors backend logic) ───────────────────
function getDesignations(years: number): string[] {
  if (years === 0) return ["Intern", "Graduate Trainee Engineer (GTE)"];
  if (years === 1) return ["SE1"];
  if (years === 2) return ["SE2"];
  if (years === 3) return ["SE2", "SE3"];
  if (years === 4) return ["SE3"];
  if (years >= 5 && years <= 7) return ["SE4"];
  if (years >= 8 && years <= 10) return ["Associate", "Lead"];
  return ["Lead", "Principal / Architect", "Senior Lead"];
}

// ── JD card shown in the list ──────────────────────────────────────────────────
function JDCard({ jd, downloadUrl }: { jd: GeneratedJD; downloadUrl: string }) {
  const [expanded, setExpanded] = useState(false);
  const date = new Date(jd.created_at).toLocaleDateString("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });

  return (
    <div className="rounded-2xl border border-slate-200 bg-white shadow-sm overflow-hidden">
      {/* Card header */}
      <div className="flex items-start justify-between gap-4 p-6">
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2 mb-1">
            <span className="inline-block rounded-md bg-indigo-50 px-2 py-0.5 text-xs font-medium text-indigo-700">
              {jd.business_unit}
            </span>
            <span className="inline-block rounded-md bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
              {jd.designation}
            </span>
            <span className="inline-block rounded-md bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700">
              {jd.years_of_experience}+ yr{jd.years_of_experience !== 1 ? "s" : ""}
            </span>
          </div>
          <h3 className="text-base font-semibold text-slate-900 truncate">{jd.content.title}</h3>
          <p className="mt-0.5 text-xs text-slate-500">{date}</p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {jd.pdf_url && (
            <a
              href={downloadUrl}
              target="_blank"
              rel="noreferrer"
              className="rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-1.5 text-xs font-medium text-indigo-700 hover:bg-indigo-100 transition"
            >
              ↓ PDF
            </a>
          )}
          <button
            onClick={() => setExpanded((v) => !v)}
            className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50 transition"
          >
            {expanded ? "Hide" : "Preview"}
          </button>
        </div>
      </div>

      {/* Skill tags */}
      <div className="px-6 pb-4 flex flex-wrap gap-1.5">
        {jd.skills.slice(0, 8).map((s) => (
          <span key={s} className="rounded-full bg-slate-100 px-2.5 py-0.5 text-xs text-slate-600">
            {s}
          </span>
        ))}
        {jd.skills.length > 8 && (
          <span className="rounded-full bg-slate-100 px-2.5 py-0.5 text-xs text-slate-500">
            +{jd.skills.length - 8} more
          </span>
        )}
      </div>

      {/* Expanded preview */}
      {expanded && (
        <div className="border-t border-slate-100 px-6 py-5 space-y-5 bg-slate-50">
          <Section title="About the Role" text={jd.content.summary} />
          <BulletSection title="Key Responsibilities" items={jd.content.responsibilities} />
          <BulletSection title="Required Skills" items={jd.content.required_skills} />
          {jd.content.nice_to_have.length > 0 && (
            <BulletSection title="Nice to Have" items={jd.content.nice_to_have} />
          )}
          {jd.content.qualifications.length > 0 && (
            <BulletSection title="Qualifications" items={jd.content.qualifications} />
          )}
          {jd.content.what_we_offer.length > 0 && (
            <BulletSection title="What We Offer" items={jd.content.what_we_offer} />
          )}
        </div>
      )}
    </div>
  );
}

function Section({ title, text }: { title: string; text: string }) {
  return (
    <div>
      <h4 className="text-sm font-semibold text-indigo-700 mb-1">{title}</h4>
      <p className="text-sm text-slate-700 leading-relaxed">{text}</p>
    </div>
  );
}

function BulletSection({ title, items }: { title: string; items: string[] }) {
  return (
    <div>
      <h4 className="text-sm font-semibold text-indigo-700 mb-2">{title}</h4>
      <ul className="space-y-1">
        {items.map((item, i) => (
          <li key={i} className="flex gap-2 text-sm text-slate-700">
            <span className="mt-1 shrink-0 text-indigo-400">•</span>
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ── Skill toggle pill ──────────────────────────────────────────────────────────
function SkillPill({
  skill,
  selected,
  onToggle,
}: {
  skill: string;
  selected: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className={`rounded-full px-3 py-1 text-xs font-medium transition border ${
        selected
          ? "bg-indigo-600 text-white border-indigo-600"
          : "bg-white text-slate-600 border-slate-200 hover:border-indigo-300 hover:text-indigo-600"
      }`}
    >
      {skill}
    </button>
  );
}

// ── Main page ──────────────────────────────────────────────────────────────────
export default function AIJDPage() {
  const [jds, setJds] = useState<GeneratedJD[]>([]);
  const [metadata, setMetadata] = useState<JDMetadata | null>(null);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const formRef = useRef<HTMLDivElement>(null);

  // Form state
  const [selectedBU, setSelectedBU] = useState("");
  const [selectedRole, setSelectedRole] = useState("");
  const [selectedSkills, setSelectedSkills] = useState<Set<string>>(new Set());
  const [yearsInput, setYearsInput] = useState<string>("");
  const [selectedDesignation, setSelectedDesignation] = useState("");
  const [generating, setGenerating] = useState(false);
  const [genError, setGenError] = useState("");

  useEffect(() => {
    Promise.all([api.listJDs(), api.jdMetadata()])
      .then(([jdRes, meta]) => {
        setJds(jdRes.jds);
        setMetadata(meta);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  // Scroll to form when it opens
  useEffect(() => {
    if (showForm && formRef.current) {
      formRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [showForm]);

  const rolesForBU = selectedBU && metadata ? metadata.business_units[selectedBU] ?? [] : [];
  const skillsForRole = selectedRole && metadata ? metadata.skills_by_role[selectedRole] ?? [] : [];
  const yearsNum = yearsInput === "" ? 0 : parseInt(yearsInput, 10);
  const availableDesignations = isNaN(yearsNum) ? [] : getDesignations(yearsNum);

  // Reset downstream selections when parent changes
  function handleBUChange(bu: string) {
    setSelectedBU(bu);
    setSelectedRole("");
    setSelectedSkills(new Set());
    setSelectedDesignation("");
  }

  function handleRoleChange(role: string) {
    setSelectedRole(role);
    setSelectedSkills(new Set());
    setSelectedDesignation("");
  }

  function handleYearsChange(val: string) {
    setYearsInput(val);
    setSelectedDesignation("");
  }

  function toggleSkill(skill: string) {
    setSelectedSkills((prev) => {
      const next = new Set(prev);
      if (next.has(skill)) next.delete(skill);
      else next.add(skill);
      return next;
    });
  }

  function selectAllSkills() {
    setSelectedSkills(new Set(skillsForRole));
  }

  function clearSkills() {
    setSelectedSkills(new Set());
  }

  async function handleGenerate() {
    if (!selectedBU || !selectedRole || selectedSkills.size === 0 || !selectedDesignation) return;

    setGenerating(true);
    setGenError("");
    try {
      const result = await api.generateJD({
        business_unit: selectedBU,
        role: selectedRole,
        skills: Array.from(selectedSkills),
        years_of_experience: isNaN(yearsNum) ? 0 : yearsNum,
        designation: selectedDesignation,
      });
      setJds((prev) => [result, ...prev]);
      setShowForm(false);
      // Reset form
      setSelectedBU("");
      setSelectedRole("");
      setSelectedSkills(new Set());
      setYearsInput("");
      setSelectedDesignation("");
    } catch (err: any) {
      setGenError(err?.message ?? "Generation failed. Please try again.");
    } finally {
      setGenerating(false);
    }
  }

  const canGenerate =
    selectedBU && selectedRole && selectedSkills.size > 0 &&
    !isNaN(yearsNum) && yearsInput !== "" && selectedDesignation && !generating;

  return (
    <div className="max-w-4xl mx-auto">
      {/* Page header */}
      <div className="mb-6 flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">AI Job Description Generator</h1>
          <p className="mt-1 text-sm text-slate-500">
            Generate professional, formatted job descriptions powered by AI
          </p>
        </div>
        <button
          onClick={() => setShowForm((v) => !v)}
          className="rounded-xl bg-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-700 transition"
        >
          {showForm ? "✕ Cancel" : "✦ Create New JD"}
        </button>
      </div>

      {/* ── Creation form ────────────────────────────────────────────── */}
      {showForm && (
        <div
          ref={formRef}
          className="mb-8 rounded-2xl border border-indigo-100 bg-white shadow-md overflow-hidden"
        >
          {/* Form header */}
          <div className="bg-indigo-600 px-6 py-4">
            <h2 className="text-base font-semibold text-white">Configure Job Description</h2>
            <p className="text-xs text-indigo-200 mt-0.5">
              Select business unit, role, and skills — then let AI do the rest
            </p>
          </div>

          <div className="p-6 space-y-6">
            {/* Step 1: Business Unit + Role (side-by-side cascade) */}
            <div>
              <label className="block text-xs font-semibold uppercase tracking-wider text-slate-500 mb-3">
                Step 1 — Business Unit &amp; Role
              </label>
              <div className="flex flex-wrap gap-3 items-start">
                {/* Business Unit dropdown */}
                <div className="relative">
                  <select
                    value={selectedBU}
                    onChange={(e) => handleBUChange(e.target.value)}
                    className="appearance-none rounded-xl border border-slate-200 bg-white py-2.5 pl-4 pr-10 text-sm text-slate-800 shadow-sm focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-100"
                  >
                    <option value="">Select Business Unit…</option>
                    {metadata
                      ? Object.keys(metadata.business_units).map((bu) => (
                          <option key={bu} value={bu}>
                            {bu}
                          </option>
                        ))
                      : null}
                  </select>
                  <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-slate-400">▾</span>
                </div>

                {/* Animated connector arrow */}
                {selectedBU && (
                  <span className="self-center text-indigo-400 font-bold text-lg select-none">→</span>
                )}

                {/* Role dropdown (appears when BU selected) */}
                {selectedBU && (
                  <div className="relative">
                    <select
                      value={selectedRole}
                      onChange={(e) => handleRoleChange(e.target.value)}
                      className="appearance-none rounded-xl border border-indigo-200 bg-indigo-50 py-2.5 pl-4 pr-10 text-sm text-slate-800 shadow-sm focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-100"
                    >
                      <option value="">Select Role…</option>
                      {rolesForBU.map((role) => (
                        <option key={role} value={role}>
                          {role}
                        </option>
                      ))}
                    </select>
                    <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-indigo-400">▾</span>
                  </div>
                )}
              </div>
            </div>

            {/* Step 2: Skills selection (appears when role selected) */}
            {selectedRole && (
              <div>
                <div className="flex items-center justify-between mb-3">
                  <label className="block text-xs font-semibold uppercase tracking-wider text-slate-500">
                    Step 2 — Select Skills
                    <span className="ml-2 font-normal normal-case text-indigo-600">
                      ({selectedSkills.size} selected)
                    </span>
                  </label>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={selectAllSkills}
                      className="text-xs text-indigo-600 hover:underline"
                    >
                      Select all
                    </button>
                    <span className="text-slate-300">|</span>
                    <button
                      type="button"
                      onClick={clearSkills}
                      className="text-xs text-slate-400 hover:underline"
                    >
                      Clear
                    </button>
                  </div>
                </div>

                <div className="rounded-xl border border-slate-200 bg-slate-50 p-4">
                  <div className="flex flex-wrap gap-2">
                    {skillsForRole.map((skill) => (
                      <SkillPill
                        key={skill}
                        skill={skill}
                        selected={selectedSkills.has(skill)}
                        onToggle={() => toggleSkill(skill)}
                      />
                    ))}
                  </div>
                </div>

                {/* Experience + Designation */}
                <div className="mt-5 grid grid-cols-1 sm:grid-cols-2 gap-5">
                  {/* Years of experience */}
                  <div>
                    <label className="block text-xs font-semibold uppercase tracking-wider text-slate-500 mb-2">
                      Years of Experience
                    </label>
                    <input
                      type="number"
                      min={0}
                      max={30}
                      value={yearsInput}
                      onChange={(e) => handleYearsChange(e.target.value)}
                      placeholder="e.g. 3"
                      className="w-full rounded-xl border border-slate-200 py-2.5 px-4 text-sm text-slate-800 shadow-sm focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-100"
                    />
                    {yearsInput !== "" && !isNaN(yearsNum) && (
                      <p className="mt-1.5 text-xs text-slate-500">
                        Eligible designations:{" "}
                        <span className="font-medium text-indigo-700">
                          {getDesignations(yearsNum).join(", ")}
                        </span>
                      </p>
                    )}
                  </div>

                  {/* Designation dropdown */}
                  <div>
                    <label className="block text-xs font-semibold uppercase tracking-wider text-slate-500 mb-2">
                      Designation / Required Role
                    </label>
                    <div className="relative">
                      <select
                        value={selectedDesignation}
                        onChange={(e) => setSelectedDesignation(e.target.value)}
                        disabled={availableDesignations.length === 0}
                        className="w-full appearance-none rounded-xl border border-slate-200 bg-white py-2.5 pl-4 pr-10 text-sm text-slate-800 shadow-sm focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-100 disabled:bg-slate-50 disabled:text-slate-400"
                      >
                        <option value="">
                          {yearsInput === ""
                            ? "Enter experience first…"
                            : availableDesignations.length === 0
                            ? "No designations available"
                            : "Select Designation…"}
                        </option>
                        {availableDesignations.map((d) => (
                          <option key={d} value={d}>
                            {d}
                          </option>
                        ))}
                      </select>
                      <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-slate-400">▾</span>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* Summary + Generate */}
            {selectedRole && (
              <div className="border-t border-slate-100 pt-5">
                {/* Quick summary */}
                {selectedBU && selectedRole && (
                  <div className="mb-4 rounded-xl bg-indigo-50 border border-indigo-100 px-4 py-3 text-sm text-slate-700">
                    <span className="font-medium text-indigo-700">Ready to generate:</span>{" "}
                    {selectedRole} · {selectedBU}
                    {selectedDesignation && ` · ${selectedDesignation}`}
                    {yearsInput !== "" && ` · ${yearsInput}+ yr${parseInt(yearsInput) !== 1 ? "s" : ""} exp`}
                    {selectedSkills.size > 0 && ` · ${selectedSkills.size} skills selected`}
                  </div>
                )}

                {genError && (
                  <div className="mb-4 rounded-xl bg-rose-50 border border-rose-200 px-4 py-3 text-sm text-rose-700">
                    {genError}
                  </div>
                )}

                <button
                  onClick={handleGenerate}
                  disabled={!canGenerate}
                  className="w-full rounded-xl bg-indigo-600 py-3 text-sm font-semibold text-white shadow-sm hover:bg-indigo-700 transition disabled:bg-indigo-300 disabled:cursor-not-allowed"
                >
                  {generating ? (
                    <span className="flex items-center justify-center gap-2">
                      <svg className="animate-spin h-4 w-4 text-white" viewBox="0 0 24 24" fill="none">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                      </svg>
                      Generating JD with AI…
                    </span>
                  ) : (
                    "✦ Generate Job Description"
                  )}
                </button>

                {!canGenerate && !generating && (
                  <p className="mt-2 text-center text-xs text-slate-400">
                    {!selectedSkills.size
                      ? "Select at least one skill to continue"
                      : yearsInput === ""
                      ? "Enter years of experience"
                      : !selectedDesignation
                      ? "Select a designation"
                      : "Fill in all fields above"}
                  </p>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── JD list ──────────────────────────────────────────────────── */}
      {loading ? (
        <div className="py-16 text-center text-sm text-slate-400">Loading…</div>
      ) : jds.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-slate-200 bg-white py-16 text-center">
          <p className="text-4xl mb-3">✦</p>
          <p className="text-slate-600 font-medium">No job descriptions yet</p>
          <p className="mt-1 text-sm text-slate-400">
            Click <strong>Create New JD</strong> above to get started
          </p>
        </div>
      ) : (
        <div className="space-y-4">
          <p className="text-sm text-slate-500">
            {jds.length} job description{jds.length !== 1 ? "s" : ""} generated
          </p>
          {jds.map((jd) => (
            <JDCard
              key={jd.jd_id}
              jd={jd}
              downloadUrl={api.jdDownloadUrl(jd.jd_id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
