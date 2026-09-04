import Link from "next/link";

import "./landing.css";
import "./landing-sections.css";
import { LandingNav } from "../components/landing-nav";
import { LandingVideo } from "../components/landing-video";
import { LandingArrow } from "../components/landing-arrow";
import publicBenchmark from "../../../artifacts/benchmark/public-summary.json";
import { benchmarkStats, parsePublicBenchmark } from "../lib/benchmark-view";

const CAPABILITIES = [
  {
    index: "01",
    title: "Deterministic reconciliation",
    body: "Payments, refunds, settlements, bank credits and ledger entries are matched by identifiers and arithmetic — signed integer paise, idempotent, immutable source provenance. No row is ever silently dropped.",
  },
  {
    index: "02",
    title: "Financial flight recorder",
    body: "A typed evidence graph links every recorded amount across systems and shows exactly where the chain breaks — proven, hypothesised, or rejected. Certainty is never implied beyond the stored state.",
  },
  {
    index: "03",
    title: "Bounded AI investigation",
    body: "One investigator inspects residual exceptions and tests competing hypotheses through read-only tools. It can never resolve a case, approve a correction, or touch the ledger.",
  },
  {
    index: "04",
    title: "Proof-carrying corrections",
    body: "Every proposed fix ships with equations, cited evidence and a deterministic verifier PASS — previewed as a dry-run, applied only after explicit human approval.",
  },
] as const;


const EXCEPTION_CLASSES = [
  "Duplicate ledger posting",
  "Missing refund posting",
  "Settlement timing shift",
  "Ambiguous evidence",
] as const;

const TRUST_ITEMS = [
  {
    title: "No resolution without proof",
    body: "Every accepted explanation requires a deterministic verifier PASS with cited evidence IDs and rule versions.",
  },
  {
    title: "Dry-run first",
    body: "Corrections are previewed against a sandbox ledger — variance before and after — before anything is allowed to change.",
  },
  {
    title: "Humans hold authority",
    body: "Every non-zero ledger delta requires explicit human approval. Voice is never an approval channel.",
  },
  {
    title: "Append-only audit",
    body: "Every decision is hashed into a tamper-evident trail, from first import to simulated application.",
  },
] as const;

const BENCHMARK = parsePublicBenchmark(publicBenchmark);
const BENCHMARK_STATS = benchmarkStats(BENCHMARK);

export default function LandingPage() {
  return (
    <div className="landing">
      <div className="scroll-progress" aria-hidden="true">
        <span></span>
      </div>

      <div className="page">
        <div className="bg" aria-hidden="true">
          <LandingVideo />
        </div>

        <LandingNav />

        <section className="hero">
          <div className="badge wipe" style={{ "--d": "0.18s" } as React.CSSProperties}>
            <span className="badge__mark" aria-hidden="true" />
            Financial flight recorder · Merchant reconciliation
          </div>

          <h1 className="headline">
            <span className="headline__mask">
              <span className="headline__rise" style={{ "--d": "0.26s" } as React.CSSProperties}>
                Every financial record
              </span>
            </span>
            <span className="headline__mask">
              <span
                className="headline__rise headline__line"
                style={{ "--d": "0.4s" } as React.CSSProperties}
              >
                <span className="headline__muted">deserves an&nbsp;</span>
                <span className="headline__accent" data-text="evidence trail.">
                  evidence trail.
                </span>
              </span>
            </span>
          </h1>

          <div className="actions">

            <a className="btn btn--ghost wipe" style={{ "--d": "0.66s" } as React.CSSProperties} href="#workflow">
              <span className="btn__label">See the workflow</span>
            </a>
          </div>
        </section>

        <div className="lede">
          <p className="lede__rise" style={{ "--d": "0.78s" } as React.CSSProperties}>
            ARGUS CONTROL deterministically reconciles payments, refunds, settlements, bank credits
            and ledger entries — investigates the residual exceptions with one bounded AI, verifies
            every explanation with code, and leaves ambiguous cases unresolved.
          </p>
        </div>
      </div>

      <main className="below">
        <section id="platform" className="section section--what">
          <div className="section__head">
            <p className="kicker">
              <span className="kicker__mark" aria-hidden="true" />
              What ARGUS does
            </p>
            <h2 className="section__title">A financial flight recorder for merchant reconciliation.</h2>
            <p className="section__sub">
              ARGUS reconstructs the evidence path behind recorded amounts across payments, refunds,
              settlements, bank credits and ledger entries — investigates where the chain breaks,
              and refuses to close a case it cannot prove.
            </p>
          </div>

          <div className="cap-grid">
            {CAPABILITIES.map((cap) => (
              <article className="cap" key={cap.index}>
                <span className="cap__index">{cap.index}</span>
                <h3>{cap.title}</h3>
                <p>{cap.body}</p>
              </article>
            ))}
          </div>
        </section>

        <section id="workflow" className="section section--workflow">
          <div className="section__head">
            <p className="kicker">
              <span className="kicker__mark" aria-hidden="true" />
              How it works
            </p>
            <h2 className="section__title">One finance loop, proven end to end.</h2>
            <p className="section__sub">
              Clean records reconcile by identifiers and arithmetic. Whatever remains becomes a
              typed case — investigated by a bounded AI, verified by deterministic code, and only
              ever corrected through a previewed, human-approved simulation. Ambiguity is never
              forced closed.
            </p>
          </div>

          <div className="flow-scroll">
            <svg
              className="flow"
              viewBox="0 0 1240 640"
              role="img"
              aria-label="Workflow graph: five source record types flow into normalize, then reconcile. Clean records match deterministically. Residual variance becomes an exception case, investigated by a bounded AI, checked by a deterministic verifier. A PASS leads to dry-run, approval and a simulated entry; an inconclusive result stays unresolved; a fail returns for re-investigation."
            >
              <defs>
                <filter id="packet-glow-blue" x="-50%" y="-50%" width="200%" height="200%">
                  <feDropShadow dx="0" dy="0" stdDeviation="3" floodColor="#006cd2" floodOpacity="0.85" />
                </filter>
                <filter id="packet-glow-amber" x="-50%" y="-50%" width="200%" height="200%">
                  <feDropShadow dx="0" dy="0" stdDeviation="3" floodColor="#d97706" floodOpacity="0.85" />
                </filter>
                <filter id="packet-glow-bad" x="-50%" y="-50%" width="200%" height="200%">
                  <feDropShadow dx="0" dy="0" stdDeviation="3" floodColor="#f43f5e" floodOpacity="0.85" />
                </filter>
              </defs>

              {/* ---------- edges (packets travel beneath nodes) ---------- */}
              <g className="edges">
                <path className="edge" d="M160 62 C 230 62, 215 242, 280 242" />
                <path className="edge" d="M160 152 C 225 152, 215 242, 280 242" />
                <path className="edge" d="M160 242 L 280 242" />
                <path className="edge" d="M160 332 C 225 332, 215 242, 280 242" />
                <path className="edge" d="M160 422 C 230 422, 215 242, 280 242" />
                <path className="edge edge--main" d="M460 242 L 520 242" />
                <path className="edge edge--ok" d="M710 242 C 745 242, 725 110, 760 110" />
                <path className="edge edge--warn" d="M710 242 C 745 242, 725 374, 760 374" />
                <path className="edge" d="M960 374 L 1000 374" />
                <path className="edge edge--info" d="M1100 406 L 1100 464" />
                <path className="edge edge--ok" d="M1000 486 L 980 486" />
                <path className="edge edge--bad" d="M1100 508 L 1100 568" />
                <path className="edge edge--fail" d="M1210 486 C 1236 486, 1236 374, 1210 374" />
              </g>

              {/* ---------- travelling packets (Single active packet at a time, sequential cycles) ---------- */}
              <g className="packets" aria-hidden="true">
                {/* Dot 1: Deterministic Match Cycle (Settlement -> Normalize -> Reconcile -> Matched) */}
                <circle className="packet packet--blue" r="6" filter="url(#packet-glow-blue)" opacity="0">
                  <animateMotion
                    id="flowCycle1"
                    dur="4.2s"
                    begin="0s; flowCycle3.end+0.7s"
                    fill="freeze"
                    path="M160 242 L 280 242 L 460 242 L 520 242 L 710 242 C 745 242, 725 110, 760 110 L 860 110"
                  />
                  <animate
                    attributeName="opacity"
                    values="0; 1; 1; 1; 0"
                    keyTimes="0; 0.05; 0.9; 0.98; 1"
                    dur="4.2s"
                    begin="0s; flowCycle3.end+0.7s"
                    fill="freeze"
                  />
                </circle>

                {/* Dot 2: Exception Investigation & Approval Cycle (Payment -> Normalize -> Reconcile -> Exception -> AI -> Verifier -> Pass -> Approval) */}
                <circle className="packet packet--amber" r="6" filter="url(#packet-glow-amber)" opacity="0">
                  <animateMotion
                    id="flowCycle2"
                    dur="6.2s"
                    begin="flowCycle1.end+0.7s"
                    fill="freeze"
                    path="M160 62 C 230 62, 215 242, 280 242 L 460 242 L 520 242 L 710 242 C 745 242, 725 374, 760 374 L 960 374 L 1000 374 L 1100 374 L 1100 464 L 1100 486 L 980 486 L 860 486"
                  />
                  <animate
                    attributeName="opacity"
                    values="0; 1; 1; 1; 0"
                    keyTimes="0; 0.04; 0.92; 0.98; 1"
                    dur="6.2s"
                    begin="flowCycle1.end+0.7s"
                    fill="freeze"
                  />
                </circle>

                {/* Dot 3: Ambiguity / Unresolved Case Cycle (Refund -> Normalize -> Reconcile -> Exception -> AI -> Verifier -> Inconclusive -> Unresolved) */}
                <circle className="packet packet--bad" r="6" filter="url(#packet-glow-bad)" opacity="0">
                  <animateMotion
                    id="flowCycle3"
                    dur="5.8s"
                    begin="flowCycle2.end+0.7s"
                    fill="freeze"
                    path="M160 152 C 225 152, 215 242, 280 242 L 460 242 L 520 242 L 710 242 C 745 242, 725 374, 760 374 L 960 374 L 1000 374 L 1100 374 L 1100 464 L 1100 486 L 1100 508 L 1100 568 L 1100 590"
                  />
                  <animate
                    attributeName="opacity"
                    values="0; 1; 1; 1; 0"
                    keyTimes="0; 0.05; 0.92; 0.98; 1"
                    dur="5.8s"
                    begin="flowCycle2.end+0.7s"
                    fill="freeze"
                  />
                </circle>
              </g>

              {/* ---------- edge labels ---------- */}
              <g className="edge-labels">
                <text x="618" y="96">identifier + arithmetic</text>
                <text x="600" y="322">residual variance</text>
                <text x="928" y="440">deterministic check</text>
                <text x="866" y="500">PASS</text>
                <text x="1112" y="545">INCONCLUSIVE</text>
                <text x="1128" y="436">FAIL · re-investigate</text>
              </g>

              {/* ---------- nodes ---------- */}
              <g className="nodes">
                <g className="gnode gnode--src">
                  <rect x="20" y="40" width="140" height="44" />
                  <text x="90" y="67">PAYMENT</text>
                </g>
                <g className="gnode gnode--src">
                  <rect x="20" y="130" width="140" height="44" />
                  <text x="90" y="157">REFUND</text>
                </g>
                <g className="gnode gnode--src">
                  <rect x="20" y="220" width="140" height="44" />
                  <text x="90" y="247">SETTLEMENT</text>
                </g>
                <g className="gnode gnode--src">
                  <rect x="20" y="310" width="140" height="44" />
                  <text x="90" y="337">BANK ENTRY</text>
                </g>
                <g className="gnode gnode--src">
                  <rect x="20" y="400" width="140" height="44" />
                  <text x="90" y="427">LEDGER ENTRY</text>
                </g>

                <g className="gnode gnode--stage">
                  <rect x="280" y="210" width="180" height="64" style={{ animationDelay: "0s" }} />
                  <text x="370" y="238">NORMALIZE</text>
                  <text className="sub" x="370" y="258">validate · quarantine · hash</text>
                </g>
                <g className="gnode gnode--stage">
                  <rect x="520" y="210" width="180" height="64" style={{ animationDelay: "0.8s" }} />
                  <text x="610" y="238">RECONCILE</text>
                  <text className="sub" x="610" y="258">match hierarchy · consumption</text>
                </g>
                <g className="gnode gnode--ok">
                  <rect x="760" y="78" width="200" height="64" style={{ animationDelay: "1.6s" }} />
                  <text x="860" y="106">MATCHED</text>
                  <text className="sub" x="860" y="126">control totals · evidence graph</text>
                </g>
                <g className="gnode gnode--warn">
                  <rect x="760" y="342" width="200" height="64" style={{ animationDelay: "1.6s" }} />
                  <text x="860" y="370">EXCEPTION CASE</text>
                  <text className="sub" x="860" y="390">typed · variance scoped</text>
                </g>
                <g className="gnode gnode--ai">
                  <rect x="1000" y="342" width="200" height="64" style={{ animationDelay: "2.6s" }} />
                  <text x="1100" y="370">AI INVESTIGATOR</text>
                  <text className="sub" x="1100" y="390">bounded · read-only tools</text>
                </g>
                <g className="gnode gnode--info">
                  <rect x="1000" y="464" width="200" height="44" style={{ animationDelay: "3.4s" }} />
                  <text x="1100" y="491">DETERMINISTIC VERIFIER</text>
                </g>
                <g className="gnode gnode--pass">
                  <rect x="740" y="464" width="240" height="44" style={{ animationDelay: "4.4s" }} />
                  <text x="860" y="485">DRY-RUN → APPROVAL</text>
                  <text className="sub" x="860" y="500">new simulated entry · audit</text>
                </g>
                <g className="gnode gnode--bad">
                  <rect x="1000" y="568" width="200" height="44" style={{ animationDelay: "5s" }} />
                  <text x="1100" y="589">UNRESOLVED</text>
                  <text className="sub" x="1100" y="604">missing evidence stated</text>
                </g>
              </g>
            </svg>
          </div>

          <ul className="flow-legend">
            <li><span className="dot dot--ok" /> Deterministic</li>
            <li><span className="dot dot--warn" /> Exception</li>
            <li><span className="dot dot--ai" /> AI + verification</li>
            <li><span className="dot dot--pass" /> Approval + simulation</li>
            <li><span className="dot dot--bad" /> Honest unresolved</li>
          </ul>

        </section>

        <section className="section section--principle">
          <p className="kicker kicker--dark">
            <span className="kicker__mark kicker__mark--warm" aria-hidden="true" />
            The operating principle
          </p>
          <h2 className="principle__title">
            Rules for calculation. AI for investigation. Verification for closure. Humans for
            ambiguity.
          </h2>

          <div className="classes">
            <p className="classes__label">Every residual discrepancy becomes a typed case</p>
            <ul className="classes__list">
              {EXCEPTION_CLASSES.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
            <p className="classes__note">
              Two valid explanations and no discriminator? The case stays{" "}
              <strong>UNRESOLVED</strong> — ARGUS refuses to guess and states exactly which evidence
              is missing and what a human should inspect next.
            </p>
          </div>
        </section>

        <section id="safety" className="section section--trust">
          <div className="section__head">
            <p className="kicker">
              <span className="kicker__mark" aria-hidden="true" />
              Safety by construction
            </p>
            <h2 className="section__title">Nothing changes without proof, preview and a person.</h2>
          </div>

          <div className="trust-grid">
            {TRUST_ITEMS.map((item) => (
              <div className="trust" key={item.title}>
                <h3>{item.title}</h3>
                <p>{item.body}</p>
              </div>
            ))}
          </div>
        </section>

        <section id="benchmark" className="section section--benchmark">
          <div className="section__head">
            <p className="kicker">
              <span className="kicker__mark" aria-hidden="true" />
              Measured, not claimed
            </p>
            <h2 className="section__title">The benchmark runner publishes every denominator.</h2>
          </div>

          <dl className="bench-grid">
            {BENCHMARK_STATS.map((stat) => (
              <div className="bench" key={stat.label}>
                <dd>{stat.value}</dd>
                <dt>{stat.label}</dt>
                <span>{stat.denominator}</span>
              </div>
            ))}
          </dl>

          <p className="bench__note">
            Generated from <code>{BENCHMARK.source_artifact}</code> ({BENCHMARK.mode} mode,
            deterministic {BENCHMARK.provider} investigator). Historical frozen-holdout result,
            not active-run telemetry · synthetic data only · no real money moves.
          </p>

          <div className="cta">
            <h2 className="cta__title">See every correction before it happens.</h2>
            <div className="actions">
              <Link className="btn btn--nav" href="/dashboard">
                <span className="btn__label">Open Control Room</span>
                <span className="btn__icon" aria-hidden="true">
                  <LandingArrow />
                </span>
              </Link>
              <a className="btn btn--ghost" href="#workflow">
                <span className="btn__label">Replay the workflow</span>
              </a>
            </div>
          </div>
        </section>

        {/* ================= Monumental Gradient ARGUS Typography ================= */}
        <section className="landing-signature" aria-hidden="true">
          <div className="landing-signature__word">ARGUS</div>
        </section>
      </main>
    </div>
  );
}
