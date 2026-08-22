export default function HomePage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col justify-center gap-6 px-6 py-16">
      <p className="text-sm font-semibold tracking-widest text-emerald-400 uppercase">
        Razorpay AI Buildathon 2026 — Track 04
      </p>
      <h1 className="text-4xl font-bold">ARGUS CONTROL</h1>
      <p className="text-lg text-slate-300">
        Financial flight recorder for merchant reconciliation. Phase 0
        foundation: domain contracts, safe configuration, and health endpoints
        are in place. Reconciliation workflows arrive in later phases.
      </p>
      <p className="text-sm text-slate-400">
        This prototype uses synthetic data only. It never moves real money.
      </p>
    </main>
  );
}
