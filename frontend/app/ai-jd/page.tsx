"use client";

import { useEffect, useRef, useState } from "react";
import { api, type GeneratedJD, type JDContent, type JDMetadata } from "@/lib/api";

// ── Experience → designation helper ───────────────────────────────────────────
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

// ── Editable list section (each item is its own textarea row) ─────────────────
function EditableList({
  items,
  onChange,
}: {
  items: string[];
  onChange: (next: string[]) => void;
}) {
  function update(i: number, val: string) {
    const next = [...items];
    next[i] = val;
    onChange(next);
  }
  function remove(i: number) {
    onChange(items.filter((_, idx) => idx !== i));
  }
  function add() {
    onChange([...items, ""]);
  }

  return (
    <div className="space-y-2">
      {items.map((item, i) => (
        <div key={i} className="flex gap-2 items-start">
          <span className="mt-2.5 shrink-0 text-indigo-300 text-xs">•</span>
          <textarea
            value={item}
            onChange={(e) => update(i, e.target.value)}
            rows={2}
            className="flex-1 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-800 resize-y focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-100"
          />
          <button
            type="button"
            onClick={() => remove(i)}
            className="mt-1.5 shrink-0 rounded-lg p-1.5 text-slate-400 hover:bg-rose-50 hover:text-rose-500 transition"
            title="Remove item"
          >
            ✕
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={add}
        className="mt-1 text-xs font-medium text-indigo-600 hover:underline"
      >
        + Add item
      </button>
    </div>
  );
}

// ── Review / Edit panel ────────────────────────────────────────────────────────
function JDReviewPanel({
  jd,
  onUpdate,
  onClose,
}: {
  jd: GeneratedJD;
  onUpdate: (updated: GeneratedJD) => void;
  onClose: () => void;
}) {
  const [isEditing, setIsEditing] = useState(false);
  const [draft, setDraft] = useState<JDContent>({ ...jd.content });
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [pdfGenerating, setPdfGenerating] = useState(false);
  const [pdfReady, setPdfReady] = useState(!!jd.pdf_url);
  const [currentJd, setCurrentJd] = useState<GeneratedJD>(jd);

  function setField<K extends keyof JDContent>(key: K, val: JDContent[K]) {
    setDraft((prev) => ({ ...prev, [key]: val }));
  }

  async function handleSave() {
    setSaving(true);
    setSaveError("");
    try {
      const updated = await api.updateJDContent(currentJd.jd_id, draft);
      const merged = { ...currentJd, content: updated.content ?? draft };
      setCurrentJd(merged);
      setDraft({ ...merged.content });
      onUpdate(merged);
      setIsEditing(false);
    } catch (err: any) {
      setSaveError(err?.message ?? "Save failed. Please try again.");
    } finally {
      setSaving(false);
    }
  }

  function handleCancelEdit() {
    setDraft({ ...currentJd.content });
    setSaveError("");
    setIsEditing(false);
  }

  async function handleDownloadPdf() {
    setPdfGenerating(true);
    try {
      await api.generateJDPdf(currentJd.jd_id);
      const updated = { ...currentJd, pdf_url: `/uploads/jds/${currentJd.jd_id}.pdf` };
      setCurrentJd(updated);
      onUpdate(updated);
      setPdfReady(true);
      // Trigger browser download
      window.open(api.jdDownloadUrl(currentJd.jd_id), "_blank");
    } catch (err: any) {
      alert("PDF generation failed: " + (err?.message ?? "Unknown error"));
    } finally {
      setPdfGenerating(false);
    }
  }

  const content = isEditing ? draft : currentJd.content;

  return (
    <div className="mb-8 rounded-2xl border border-amber-200 bg-white shadow-lg overflow-hidden">
      {/* Panel header */}
      <div className="flex items-center justify-between bg-amber-50 border-b border-amber-200 px-6 py-4">
        <div>
          <div className="flex items-center gap-2">
            <span className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-amber-500 text-white text-xs font-bold">
              ✎
            </span>
            <h2 className="text-base font-semibold text-slate-900">
              Review Job Description
            </h2>
            {!pdfReady && (
              <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-700">
                Draft — PDF not yet generated
              </span>
            )}
            {pdfReady && (
              <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-700">
                ✓ PDF ready
              </span>
            )}
          </div>
          <p className="mt-0.5 text-xs text-slate-500">
            Review the AI-generated content, edit if needed, then download the PDF
          </p>
        </div>
        <div className="flex items-center gap-2">
          {!isEditing ? (
            <button
              onClick={() => setIsEditing(true)}
              className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 transition"
            >
              ✎ Edit
            </button>
          ) : (
            <>
              <button
                onClick={handleCancelEdit}
                disabled={saving}
                className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50 transition disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={handleSave}
                disabled={saving}
                className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-700 transition disabled:bg-indigo-300"
              >
                {saving ? "Saving…" : "Save Changes"}
              </button>
            </>
          )}
          <button
            onClick={onClose}
            className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-400 hover:text-slate-600 hover:bg-slate-50 transition"
            title="Close review panel"
          >
            ✕
          </button>
        </div>
      </div>

      {/* Error banner */}
      {saveError && (
        <div className="mx-6 mt-4 rounded-xl bg-rose-50 border border-rose-200 px-4 py-3 text-sm text-rose-700">
          {saveError}
        </div>
      )}

      {/* JD meta chips */}
      <div className="flex flex-wrap gap-2 px-6 pt-5 pb-2">
        <span className="rounded-md bg-indigo-50 px-2.5 py-1 text-xs font-medium text-indigo-700">
          {currentJd.business_unit}
        </span>
        <span className="rounded-md bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-600">
          {currentJd.role}
        </span>
        <span className="rounded-md bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-600">
          {currentJd.designation}
        </span>
        <span className="rounded-md bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-700">
          {currentJd.years_of_experience}+ yr{currentJd.years_of_experience !== 1 ? "s" : ""}
        </span>
      </div>

      {/* Content body */}
      <div className="px-6 pb-6 space-y-6">
        {/* Title */}
        <div>
          <label className="block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1.5">
            Job Title
          </label>
          {isEditing ? (
            <input
              type="text"
              value={draft.title}
              onChange={(e) => setField("title", e.target.value)}
              className="w-full rounded-xl border border-slate-200 px-4 py-2.5 text-base font-semibold text-slate-900 focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-100"
            />
          ) : (
            <h3 className="text-lg font-bold text-slate-900">{content.title}</h3>
          )}
        </div>

        {/* About the Role */}
        <ReviewSection
          label="About the Role"
          isEditing={isEditing}
          text={isEditing ? undefined : content.summary}
          editNode={
            isEditing ? (
              <textarea
                value={draft.summary}
                onChange={(e) => setField("summary", e.target.value)}
                rows={4}
                className="w-full rounded-xl border border-slate-200 px-4 py-3 text-sm text-slate-800 resize-y focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-100"
              />
            ) : undefined
          }
        />

        {/* Key Responsibilities */}
        <ReviewSection
          label="Key Responsibilities"
          isEditing={isEditing}
          items={isEditing ? undefined : content.responsibilities}
          editNode={
            isEditing ? (
              <EditableList
                items={draft.responsibilities}
                onChange={(v) => setField("responsibilities", v)}
              />
            ) : undefined
          }
        />

        {/* Required Skills */}
        <ReviewSection
          label="Required Skills"
          isEditing={isEditing}
          items={isEditing ? undefined : content.required_skills}
          editNode={
            isEditing ? (
              <EditableList
                items={draft.required_skills}
                onChange={(v) => setField("required_skills", v)}
              />
            ) : undefined
          }
        />

        {/* Nice to Have */}
        {(content.nice_to_have.length > 0 || isEditing) && (
          <ReviewSection
            label="Nice to Have"
            isEditing={isEditing}
            items={isEditing ? undefined : content.nice_to_have}
            editNode={
              isEditing ? (
                <EditableList
                  items={draft.nice_to_have}
                  onChange={(v) => setField("nice_to_have", v)}
                />
              ) : undefined
            }
          />
        )}

        {/* Qualifications */}
        {(content.qualifications.length > 0 || isEditing) && (
          <ReviewSection
            label="Qualifications"
            isEditing={isEditing}
            items={isEditing ? undefined : content.qualifications}
            editNode={
              isEditing ? (
                <EditableList
                  items={draft.qualifications}
                  onChange={(v) => setField("qualifications", v)}
                />
              ) : undefined
            }
          />
        )}

        {/* What We Offer */}
        {(content.what_we_offer.length > 0 || isEditing) && (
          <ReviewSection
            label="What We Offer"
            isEditing={isEditing}
            items={isEditing ? undefined : content.what_we_offer}
            editNode={
              isEditing ? (
                <EditableList
                  items={draft.what_we_offer}
                  onChange={(v) => setField("what_we_offer", v)}
                />
              ) : undefined
            }
          />
        )}

        {/* Footer actions */}
        {!isEditing && (
          <div className="flex items-center justify-between border-t border-slate-100 pt-5 mt-2">
            <p className="text-xs text-slate-400">
              {pdfReady
                ? "PDF generated from the reviewed content."
                : "No PDF yet — click Download to generate it from the current content."}
            </p>
            <button
              onClick={handleDownloadPdf}
              disabled={pdfGenerating}
              className="flex items-center gap-2 rounded-xl bg-indigo-600 px-5 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-indigo-700 transition disabled:bg-indigo-300 disabled:cursor-not-allowed"
            >
              {pdfGenerating ? (
                <>
                  <svg className="animate-spin h-4 w-4 text-white" viewBox="0 0 24 24" fill="none">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                  </svg>
                  Generating PDF…
                </>
              ) : pdfReady ? (
                "↓ Re-download PDF"
              ) : (
                "↓ Download PDF"
              )}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Section display helper ─────────────────────────────────────────────────────
function ReviewSection({
  label,
  isEditing,
  text,
  items,
  editNode,
}: {
  label: string;
  isEditing: boolean;
  text?: string;
  items?: string[];
  editNode?: React.ReactNode;
}) {
  return (
    <div>
      <h4
        className={`text-xs font-semibold uppercase tracking-wider mb-2 ${
          isEditing ? "text-indigo-500" : "text-indigo-700"
        }`}
      >
        {label}
        {isEditing && <span className="ml-2 normal-case font-normal text-slate-400">(editing)</span>}
      </h4>
      {isEditing ? (
        editNode
      ) : text != null ? (
        <p className="text-sm text-slate-700 leading-relaxed">{text}</p>
      ) : (
        <ul className="space-y-1.5">
          {(items ?? []).map((item, i) => (
            <li key={i} className="flex gap-2 text-sm text-slate-700">
              <span className="mt-1 shrink-0 text-indigo-400">•</span>
              <span>{item}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ── JD card in the completed list ──────────────────────────────────────────────
function JDCard({
  jd,
  onReopen,
}: {
  jd: GeneratedJD;
  onReopen: (jd: GeneratedJD) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const date = new Date(jd.created_at).toLocaleDateString("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });

  return (
    <div className="rounded-2xl border border-slate-200 bg-white shadow-sm overflow-hidden">
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
            {!jd.pdf_url && (
              <span className="inline-block rounded-md bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-600">
                Draft
              </span>
            )}
          </div>
          <h3 className="text-base font-semibold text-slate-900 truncate">{jd.content.title}</h3>
          <p className="mt-0.5 text-xs text-slate-500">{date}</p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {jd.pdf_url && (
            <a
              href={api.jdDownloadUrl(jd.jd_id)}
              target="_blank"
              rel="noreferrer"
              className="rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-1.5 text-xs font-medium text-indigo-700 hover:bg-indigo-100 transition"
            >
              ↓ PDF
            </a>
          )}
          <button
            onClick={() => onReopen(jd)}
            className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50 transition"
          >
            ✎ Edit
          </button>
          <button
            onClick={() => setExpanded((v) => !v)}
            className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50 transition"
          >
            {expanded ? "Hide" : "Preview"}
          </button>
        </div>
      </div>

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

      {expanded && (
        <div className="border-t border-slate-100 px-6 py-5 space-y-5 bg-slate-50">
          <ReviewSection label="About the Role" isEditing={false} text={jd.content.summary} />
          <ReviewSection label="Key Responsibilities" isEditing={false} items={jd.content.responsibilities} />
          <ReviewSection label="Required Skills" isEditing={false} items={jd.content.required_skills} />
          {jd.content.nice_to_have.length > 0 && (
            <ReviewSection label="Nice to Have" isEditing={false} items={jd.content.nice_to_have} />
          )}
          {jd.content.qualifications.length > 0 && (
            <ReviewSection label="Qualifications" isEditing={false} items={jd.content.qualifications} />
          )}
          {jd.content.what_we_offer.length > 0 && (
            <ReviewSection label="What We Offer" isEditing={false} items={jd.content.what_we_offer} />
          )}
          {/* Edit CTA at the bottom of preview */}
          <div className="flex items-center justify-between border-t border-slate-200 pt-4">
            <p className="text-xs text-slate-400">
              Want to make changes? Open the editor to modify any section.
            </p>
            <button
              onClick={() => onReopen(jd)}
              className="flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-700 transition"
            >
              ✎ Edit this JD
            </button>
          </div>
        </div>
      )}
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
  const [reviewingJD, setReviewingJD] = useState<GeneratedJD | null>(null);
  const formRef = useRef<HTMLDivElement>(null);
  const reviewRef = useRef<HTMLDivElement>(null);

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

  useEffect(() => {
    if (showForm && formRef.current) {
      formRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [showForm]);

  useEffect(() => {
    if (reviewingJD && reviewRef.current) {
      reviewRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [reviewingJD]);

  const rolesForBU = selectedBU && metadata ? metadata.business_units[selectedBU] ?? [] : [];
  const skillsForRole = selectedRole && metadata ? metadata.skills_by_role[selectedRole] ?? [] : [];
  const yearsNum = yearsInput === "" ? 0 : parseInt(yearsInput, 10);
  const availableDesignations = isNaN(yearsNum) ? [] : getDesignations(yearsNum);

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

  function resetForm() {
    setSelectedBU("");
    setSelectedRole("");
    setSelectedSkills(new Set());
    setYearsInput("");
    setSelectedDesignation("");
    setGenError("");
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
      // Add to list as a draft, then open the review panel
      setJds((prev) => [result, ...prev]);
      setShowForm(false);
      resetForm();
      setReviewingJD(result);
    } catch (err: any) {
      setGenError(err?.message ?? "Generation failed. Please try again.");
    } finally {
      setGenerating(false);
    }
  }

  function handleJDUpdate(updated: GeneratedJD) {
    setJds((prev) => prev.map((j) => (j.jd_id === updated.jd_id ? updated : j)));
    setReviewingJD(updated);
  }

  function handleReopenReview(jd: GeneratedJD) {
    setShowForm(false);
    setReviewingJD(jd);
  }

  const canGenerate =
    !!selectedBU && !!selectedRole && selectedSkills.size > 0 &&
    !isNaN(yearsNum) && yearsInput !== "" && !!selectedDesignation && !generating;

  return (
    <div className="max-w-4xl mx-auto">
      {/* Page header */}
      <div className="mb-6 flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">AI Job Description Generator</h1>
          <p className="mt-1 text-sm text-slate-500">
            Generate · Review &amp; Edit · Download PDF
          </p>
        </div>
        {!reviewingJD && (
          <button
            onClick={() => { setShowForm((v) => !v); if (reviewingJD) setReviewingJD(null); }}
            className="rounded-xl bg-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-700 transition"
          >
            {showForm ? "✕ Cancel" : "✦ Create New JD"}
          </button>
        )}
      </div>

      {/* ── Creation form ────────────────────────────────────────────── */}
      {showForm && !reviewingJD && (
        <div
          ref={formRef}
          className="mb-8 rounded-2xl border border-indigo-100 bg-white shadow-md overflow-hidden"
        >
          <div className="bg-indigo-600 px-6 py-4">
            <h2 className="text-base font-semibold text-white">Configure Job Description</h2>
            <p className="text-xs text-indigo-200 mt-0.5">
              Select business unit, role, and skills — AI will draft the JD for your review
            </p>
          </div>

          <div className="p-6 space-y-6">
            {/* Step 1 */}
            <div>
              <label className="block text-xs font-semibold uppercase tracking-wider text-slate-500 mb-3">
                Step 1 — Business Unit &amp; Role
              </label>
              <div className="flex flex-wrap gap-3 items-start">
                <div className="relative">
                  <select
                    value={selectedBU}
                    onChange={(e) => handleBUChange(e.target.value)}
                    className="appearance-none rounded-xl border border-slate-200 bg-white py-2.5 pl-4 pr-10 text-sm text-slate-800 shadow-sm focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-100"
                  >
                    <option value="">Select Business Unit…</option>
                    {metadata
                      ? Object.keys(metadata.business_units).map((bu) => (
                          <option key={bu} value={bu}>{bu}</option>
                        ))
                      : null}
                  </select>
                  <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-slate-400">▾</span>
                </div>

                {selectedBU && (
                  <span className="self-center text-indigo-400 font-bold text-lg select-none">→</span>
                )}

                {selectedBU && (
                  <div className="relative">
                    <select
                      value={selectedRole}
                      onChange={(e) => handleRoleChange(e.target.value)}
                      className="appearance-none rounded-xl border border-indigo-200 bg-indigo-50 py-2.5 pl-4 pr-10 text-sm text-slate-800 shadow-sm focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-100"
                    >
                      <option value="">Select Role…</option>
                      {rolesForBU.map((role) => (
                        <option key={role} value={role}>{role}</option>
                      ))}
                    </select>
                    <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-indigo-400">▾</span>
                  </div>
                )}
              </div>
            </div>

            {/* Step 2 */}
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
                    <button type="button" onClick={() => setSelectedSkills(new Set(skillsForRole))} className="text-xs text-indigo-600 hover:underline">
                      Select all
                    </button>
                    <span className="text-slate-300">|</span>
                    <button type="button" onClick={() => setSelectedSkills(new Set())} className="text-xs text-slate-400 hover:underline">
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

                <div className="mt-5 grid grid-cols-1 sm:grid-cols-2 gap-5">
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
                        Eligible:{" "}
                        <span className="font-medium text-indigo-700">
                          {getDesignations(yearsNum).join(", ")}
                        </span>
                      </p>
                    )}
                  </div>

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
                          {yearsInput === "" ? "Enter experience first…" : "Select Designation…"}
                        </option>
                        {availableDesignations.map((d) => (
                          <option key={d} value={d}>{d}</option>
                        ))}
                      </select>
                      <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-slate-400">▾</span>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* Generate button */}
            {selectedRole && (
              <div className="border-t border-slate-100 pt-5">
                {selectedBU && selectedRole && (
                  <div className="mb-4 rounded-xl bg-indigo-50 border border-indigo-100 px-4 py-3 text-sm text-slate-700">
                    <span className="font-medium text-indigo-700">Ready to generate:</span>{" "}
                    {selectedRole} · {selectedBU}
                    {selectedDesignation && ` · ${selectedDesignation}`}
                    {yearsInput !== "" && ` · ${yearsInput}+ yr${parseInt(yearsInput) !== 1 ? "s" : ""}`}
                    {selectedSkills.size > 0 && ` · ${selectedSkills.size} skills`}
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
                      ? "Select at least one skill"
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

      {/* ── Review panel (human in the loop) ────────────────────────── */}
      {reviewingJD && (
        <div ref={reviewRef}>
          <JDReviewPanel
            jd={reviewingJD}
            onUpdate={handleJDUpdate}
            onClose={() => setReviewingJD(null)}
          />
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
            {jds.length} job description{jds.length !== 1 ? "s" : ""}
          </p>
          {jds.map((jd) => (
            <JDCard
              key={jd.jd_id}
              jd={jd}
              onReopen={handleReopenReview}
            />
          ))}
        </div>
      )}
    </div>
  );
}
