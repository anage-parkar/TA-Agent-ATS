import Link from "next/link";

export default function Home() {
  return (
    <div className="mx-auto max-w-2xl py-16 text-center">
      <h1 className="text-3xl font-bold text-slate-900">Agentic Talent Acquisition</h1>
      <p className="mt-3 text-slate-500">
        Sync a job, collect applicants across LinkedIn, Google Forms, and Talent Hunt, and let the
        ATS agent score and rank them — with a human in the loop at every gate.
      </p>
      <div className="mt-8 flex justify-center gap-3">
        <Link href="/dashboard" className="btn-primary px-5 py-2.5">Open dashboard</Link>
        <Link
          href="/jobs"
          className="rounded-lg border border-slate-300 px-5 py-2.5 font-medium text-slate-700 transition hover:bg-slate-50"
        >
          Manage jobs
        </Link>
      </div>
    </div>
  );
}
