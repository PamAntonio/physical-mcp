"use client";

// The results page: five big tiles to film, then every metric underneath. All the numbers come
// from public/metrics.json, written by scripts/dashboard_metrics.py. Nothing gets typed in here,
// and a metric that isn't REAL shows its status in grey instead of a number.

import { useEffect, useState } from "react";
import Link from "next/link";

interface Metric {
  value: unknown;
  sample: string | null;
  source: string | null;
  status: string;
  group?: string;
  note?: string;
  need?: string;
  headline?: string | null;
  label?: string | null;
}

type Metrics = Record<string, Metric>;

const GROUPS = ["Reliability", "Speed", "Vision", "Safety", "Cost", "Scale"];

function isReal(metric: Metric | undefined): metric is Metric {
  return metric !== undefined && metric.status === "REAL";
}

function money(amount: number): string {
  return "$" + amount.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// a {low, high} price range, one price when both ends match
function moneyRange(range: { low: number; high: number }): string {
  if (range.low === range.high) return money(range.low);
  return `${money(range.low)} to ${money(range.high)}`;
}

function isRange(value: object): value is { low: number; high: number } {
  return "low" in value && "high" in value;
}

// whatever shape the metric's value is, as one readable line
function showValue(value: unknown): string {
  if (value === null || value === undefined) return "none";
  if (typeof value === "string" || typeof value === "number") return String(value);
  if (Array.isArray(value)) {
    const items: string[] = [];
    for (const item of value) {
      items.push(typeof item === "object" && item !== null && "cost" in item ? String(item.cost) : showValue(item));
    }
    return items.join("; ");
  }
  if (typeof value === "object") {
    if (isRange(value)) return moneyRange(value);
    const parts: string[] = [];
    for (const [name, inner] of Object.entries(value)) {
      if (typeof inner === "object" && inner !== null && "total" in inner) {
        parts.push(`${name}: ${String(inner.total)} tokens`);
      } else {
        parts.push(`${name}: ${showValue(inner)}`);
      }
    }
    return parts.join(", ");
  }
  return String(value);
}

function StatusText({ metric }: { metric: Metric | undefined }) {
  return <span className="results-grey">{metric ? metric.status : "NEEDS DATA"}</span>;
}

function SourceLines({ metrics }: { metrics: (Metric | undefined)[] }) {
  const lines = [];
  for (let index = 0; index < metrics.length; index++) {
    const metric = metrics[index];
    if (metric) {
      lines.push(
        <p className="results-tile-foot" key={index}>
          <span className="results-sample">{metric.sample ?? "no sample yet"}</span>
          <span className="results-source">{metric.source ?? "no source yet"}</span>
        </p>,
      );
    }
  }
  return <>{lines}</>;
}

function Tile({ label, metric, shown, long }: { label: string; metric: Metric | undefined; shown: string; long?: boolean }) {
  return (
    <article className="results-tile">
      <p className={isReal(metric) ? `results-big${long ? " results-big-long" : ""}` : "results-big results-big-long"}>
        {isReal(metric) ? shown : <StatusText metric={metric} />}
      </p>
      <p className="results-label">{label}</p>
      <SourceLines metrics={[metric]} />
    </article>
  );
}

function PartsTile({ metrics }: { metrics: Metrics }) {
  const cheaper = metrics["cost.parts_vs_replace.research_labs"];
  if (isReal(cheaper)) {
    return <Tile label="parts vs replacing a lab incubator" metric={cheaper} shown={showValue(cheaper.value)} />;
  }
  const replace = metrics["cost.replace_machine.research_labs"];
  const parts = metrics["cost.hardware_parts_total"];
  return (
    <article className="results-tile">
      <p className="results-big results-big-long">
        {isReal(replace) ? showValue(replace.value) : <StatusText metric={replace} />}
      </p>
      <p className="results-label">to replace a lab incubator</p>
      <p className="results-big results-big-long results-big-second">
        {isReal(parts) ? showValue(parts.value) : <StatusText metric={parts} />}
      </p>
      <p className="results-label">our rig parts</p>
      <SourceLines metrics={[replace, parts]} />
    </article>
  );
}

function CapsTile({ metrics }: { metrics: Metrics }) {
  const budget = metrics["config.press_budget_per_job"];
  const hold = metrics["config.hold_ms_timer"];
  const holdValue = hold?.value as { max_hold_ms?: number } | undefined;
  return (
    <article className="results-tile">
      <p className="results-big results-big-long">
        {isReal(budget) ? `${showValue(budget.value)} presses` : <StatusText metric={budget} />}
      </p>
      <p className="results-big results-big-long results-big-tight">
        {isReal(hold) ? `${showValue(holdValue?.max_hold_ms)} ms hold` : <StatusText metric={hold} />}
      </p>
      <p className="results-label">safety caps in code: most presses per job, longest timer hold</p>
      <SourceLines metrics={[budget, hold]} />
    </article>
  );
}

function SameButtonTile({ metric }: { metric: Metric | undefined }) {
  return (
    <Tile
      label={metric?.label ?? "same START button, scored by Muse Spark Open"}
      metric={metric}
      shown={metric?.headline ?? showValue(metric?.value)}
      long
    />
  );
}

function MetricRow({ name, metric }: { name: string; metric: Metric }) {
  const mock = metric.status === "MOCK";
  const showNumber = metric.status === "REAL" || mock;
  return (
    <li className="results-row">
      <div className="results-row-head">
        <span className="results-row-name">{name}</span>
        {mock ? (
          <span className="badge-mock">FakeHardware</span>
        ) : (
          <span className={metric.status === "REAL" ? "results-badge results-badge-real" : "results-badge"}>
            {metric.status}
          </span>
        )}
      </div>
      <p className={showNumber ? "results-row-value" : "results-row-value results-grey"}>
        {showNumber ? showValue(metric.value) : metric.need ?? metric.note ?? metric.status}
      </p>
      {showNumber && metric.note && <p className="results-row-small">{metric.note}</p>}
      <p className="results-row-small">Sample: {metric.sample ?? "none yet"}</p>
      <p className="results-row-small results-source">Source: {metric.source ?? "none yet"}</p>
    </li>
  );
}

export default function ResultsPage() {
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch("/metrics.json", { cache: "no-store" })
      .then((response) => {
        if (!response.ok) throw new Error(`metrics.json answered ${response.status}`);
        return response.json();
      })
      .then((body) => {
        if (!cancelled) setMetrics(body.metrics);
      })
      .catch((err: Error) => {
        if (!cancelled) setProblem(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const found = metrics?.["runs.found"]?.value as { real?: number } | undefined;
  const hasRealRuns = Boolean(found?.real);

  return (
    <main className="results">
      <section className="results-hero">
        <header className="results-head">
          <div>
            <div className="brand">
              <span className="brand-mark" aria-hidden />
              <span>Physical MCP</span>
            </div>
            <h1 className="results-title">Results</h1>
          </div>
          <div className="results-head-right">
            <p className="results-promise">Every number comes from a file in this repo.</p>
            <Link href="/" className="btn-log">Back to the app</Link>
          </div>
        </header>

        {problem && (
          <div className="error-banner">
            Couldn't load the numbers: {problem}. Run scripts/dashboard_metrics.py first.
          </div>
        )}
        {!metrics && !problem && <p className="muted">Loading the numbers...</p>}

        {metrics && (
          <div className="results-tiles">
            <Tile
              label="presses that clicked, rebuilt rig"
              metric={metrics["bench.press_success_rebuilt_rig"]}
              shown={showValue(metrics["bench.press_success_rebuilt_rig"]?.value)}
            />
            {hasRealRuns && (
              <Tile
                label="full jobs that worked on the real rig"
                metric={metrics["real.job_success_rate"]}
                shown={showValue(metrics["real.job_success_rate"]?.value)}
              />
            )}
            <SameButtonTile metric={metrics["risk_fixtures.same_button_different_risk"]} />
            <PartsTile metrics={metrics} />
            <CapsTile metrics={metrics} />
          </div>
        )}
      </section>

      {metrics && (
        <section className="results-details" aria-label="Every metric">
          <h2>Details</h2>
          <div className="results-groups">
            {GROUPS.map((group) => {
              const rows = [];
              for (const [name, metric] of Object.entries(metrics)) {
                if (metric.group === group) rows.push(<MetricRow key={name} name={name} metric={metric} />);
              }
              return (
                <section className="results-group" key={group}>
                  <h3>{group}</h3>
                  <ul className="results-rows">{rows}</ul>
                </section>
              );
            })}
          </div>
        </section>
      )}
    </main>
  );
}
