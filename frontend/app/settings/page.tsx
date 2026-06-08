export default function SettingsPage() {
  return (
    <div>
      <h1 className="text-2xl font-bold text-slate-900">Settings</h1>
      <p className="mt-2 text-sm text-slate-500">
        Configure integrations (Apollo, Resend, Gmail, Google Calendar, LinkedIn) via environment
        variables in <code className="rounded bg-slate-100 px-1 text-slate-700">.env</code>.
      </p>
    </div>
  );
}
