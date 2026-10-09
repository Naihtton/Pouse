"use client";
import dynamic from "next/dynamic";
import { FormEvent, useEffect, useRef, useState } from "react";
import { agents, initialStates, Job } from "./types";

const Office = dynamic(() => import("./Office"), { ssr: false, loading: () => <div className="office-canvas" /> });
const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const money = (n: number) => new Intl.NumberFormat("th-TH", { style: "currency", currency: "THB", maximumFractionDigits: 0 }).format(n);
const roles = ["Coordinates the workflow", "Collects component offers", "Checks fit and builds PCs", "Compares delivered prices"];

export default function Dashboard() {
  const [job, setJob] = useState<Job | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [connection, setConnection] = useState("Ready for a request");
  const [budget, setBudget] = useState(25000);
  const [margin, setMargin] = useState(20);
  const [useCase, setUseCase] = useState("1080p gaming");
  const socket = useRef<WebSocket | null>(null);
  const running = submitting || job?.status === "queued" || job?.status === "working";

  useEffect(() => {
    const saved = localStorage.getItem("office-job");
    if (saved) void fetch(`${API}/api/builds/${saved}`).then(async res => {
      if (res.ok) setJob(await res.json());
      else localStorage.removeItem("office-job");
    }).catch(() => setError("Could not restore the last request. Check that the API is running."));
  }, []);

  useEffect(() => {
    if (!job || job.status === "completed" || job.status === "error") return;
    let stopped = false;
    let retry: ReturnType<typeof setTimeout>;
    const id = job.id;
    function connect() {
      if (stopped) return;
      setConnection("Connecting to office…");
      const ws = new WebSocket(`${API.replace(/^http/, "ws")}/ws/builds/${id}`);
      socket.current = ws;
      let terminal = false;
      ws.onopen = () => setConnection("Live updates connected");
      ws.onmessage = e => {
        const next: Job = JSON.parse(e.data);
        terminal = next.status === "completed" || next.status === "error";
        setJob(next);
        if (terminal) setConnection("Workflow finished");
      };
      ws.onclose = () => {
        if (!stopped && !terminal) {
          setConnection("Connection lost · retrying…");
          retry = setTimeout(connect, 2000);
        }
      };
      ws.onerror = () => ws.close();
    }
    connect();
    return () => { stopped = true; clearTimeout(retry); socket.current?.close(); };
  }, [job?.id, job?.status === "completed" || job?.status === "error"]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true); setError("");
    try {
      const response = await fetch(`${API}/api/builds`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ max_cost: budget, target_margin: margin, use_case: useCase }) });
      if (!response.ok) throw new Error("Could not start the request. Check your inputs and API connection.");
      const next: Job = await response.json();
      localStorage.setItem("office-job", next.id);
      setJob(next);
    } catch (err) { setError(err instanceof Error ? err.message : "Request failed."); }
    finally { setSubmitting(false); }
  }

  const states = job?.agents ?? initialStates;
  const result = job?.result;
  return <main>
    <header><a className="brand" href="/">pouse<span> / virtual office</span></a><span className="demo">● MOCK DATA · THB</span></header>
    <div className="heading"><div><p className="eyebrow">YOUR COMPONENT SOURCING TEAM</p><h1>A better build starts here.</h1><p className="muted">Four specialist agents. One compatible PC. Clear costs and margins.</p></div><span className="connection">{connection}</span></div>
    <div className="workspace">
      <section className="panel request"><p className="eyebrow">01 / THE BRIEF</p><h2>Plan a PC build</h2>
        <form onSubmit={submit}>
          <label htmlFor="budget">Maximum component cost <span>THB</span></label><input id="budget" type="number" min="10000" max="500000" step="1" required value={budget} onChange={e => setBudget(Number(e.target.value))} disabled={running} />
          <label htmlFor="margin">Target profit margin <span>% of selling price</span></label><input id="margin" type="number" min="0" max="80" step="0.1" required value={margin} onChange={e => setMargin(Number(e.target.value))} disabled={running} />
          <label htmlFor="use">Primary use case</label><select id="use" value={useCase} onChange={e => setUseCase(e.target.value)} disabled={running}><option>1080p gaming</option><option>1440p gaming</option><option>Office &amp; productivity</option></select>
          <button disabled={running} type="submit">{running ? "Team is working…" : "Start sourcing →"}</button>
        </form><p className="small muted">Selling price = cost ÷ (1 − margin). Estimates include component shipping; exclude tax, assembly, software, marketplace fees and warranty costs.</p>
        {(error || job?.error) && <p className="error" role="alert">{error || job?.error}</p>}
      </section>
      <section className="panel team"><div className="section-title"><div><p className="eyebrow">02 / THE OFFICE</p><h2>Your team at work</h2></div><span className="tag">4 agents</span></div><Office states={states} />
        <div className="agents">{agents.map((agent, i) => <div className="agent" key={agent}><div className="agent-head"><strong>{agent}</strong><span className={`status ${states[agent]}`}>{states[agent]}</span></div><p>{roles[i]}</p></div>)}</div>
      </section>
    </div>
    <div className="results-grid">
      <section className="panel recommendation"><p className="eyebrow">03 / THE RECOMMENDATION</p><h2>{result ? "Your recommended build" : "The next build is yours."}</h2>
        {result ? <><div className="metrics"><div><span>Total cost</span><strong>{money(result.total_cost)}</strong></div><div><span>Suggested selling price</span><strong>{money(result.suggested_selling_price)}</strong></div><div className="profit"><span>Estimated profit</span><strong>{money(result.estimated_profit)}</strong><small>{result.actual_margin}% margin</small></div></div>
          <div className="table-scroll"><table><thead><tr><th>Component</th><th>Retailer</th><th>Delivered cost</th></tr></thead><tbody>{result.components.map(c => <tr key={c.listing.sku}><td><span className="category">{c.listing.category}</span><strong>{c.listing.name}</strong><details><summary>Compare 4 mock offers</summary>{c.alternatives.map(a => <p key={a.retailer}>{a.retailer}: {money(a.delivered_price)}</p>)}</details></td><td>{c.listing.retailer}</td><td>{money(c.delivered_price)}</td></tr>)}</tbody></table></div>
          <p className="muted small">{result.rationale}</p><div className="checks">{result.compatibility_checks.map(check => <span key={check}>✓ {check}</span>)}</div>
        </> : <div className="empty"><span>▧</span><p>{running ? "Your agents are comparing components and checking compatibility." : "Set a budget and let the team put the pieces together."}</p><p className="small muted">Seven components · Four retailers · Compatibility checked</p></div>}
      </section>
      <section className="panel feed"><p className="eyebrow">LIVE / ACTIVITY</p><h2>Office updates</h2><div className="events" aria-live="polite">{job?.events.length ? [...job.events].reverse().map(event => <article key={event.id}><div><strong>{event.agent}</strong><time>{new Date(event.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</time></div><p>{event.message}</p></article>) : <p className="muted small">The office is ready. Submit a brief to start the workflow.</p>}</div></section>
    </div><footer>V1 · Deterministic sourcing agents · Synthetic retailer listings, no live scraping</footer>
  </main>;
}
