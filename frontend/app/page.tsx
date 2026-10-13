"use client";
import dynamic from "next/dynamic";
import { FormEvent, useEffect, useRef, useState } from "react";
import { agents, initialStates, Job, DealsResponse } from "./types";

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
  const [useCase, setUseCase] = useState("gaming");
  const [resolution, setResolution] = useState("1080p");
  const [targetSellingPrice, setTargetSellingPrice] = useState("");
  const [preferredCpu, setPreferredCpu] = useState("");
  const [preferredGpu, setPreferredGpu] = useState("");
  const [requiredRam, setRequiredRam] = useState("");
  const [requiredStorage, setRequiredStorage] = useState("");
  const [deals, setDeals] = useState<DealsResponse | null>(null);
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
    void fetch(`${API}/deals?active=true`).then(async response => {
      if (response.ok) setDeals(await response.json());
    }).catch(() => undefined);
  }, [job?.status]);

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
        body: JSON.stringify({ max_cost: budget, minimum_margin_pct: margin, use_case: useCase,
          target_resolution: useCase === "gaming" ? resolution : null,
          target_sell_price: targetSellingPrice ? Number(targetSellingPrice) : null,
          preferred_cpu_brand: preferredCpu || null, preferred_gpu_vendor: preferredGpu || null,
          required_ram_gb: requiredRam ? Number(requiredRam) : null,
          required_storage_gb: requiredStorage ? Number(requiredStorage) : null }) });
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
    <header><a className="brand" href="/">pouse<span> / virtual office</span></a><span className="demo">● LIVE JIB + ADVICE · THB</span></header>
    <div className="heading"><div><p className="eyebrow">YOUR COMPONENT SOURCING TEAM</p><h1>A better build starts here.</h1><p className="muted">Four specialist agents. One compatible PC. Clear costs and margins.</p></div><span className="connection">{connection}</span></div>
    <div className="workspace">
      <section className="panel request"><p className="eyebrow">01 / THE BRIEF</p><h2>Plan a PC build</h2>
        <form onSubmit={submit}>
          <label htmlFor="budget">Maximum component cost <span>THB</span></label><input id="budget" type="number" min="10000" max="500000" step="1" required value={budget} onChange={e => setBudget(Number(e.target.value))} disabled={running} />
          <label htmlFor="selling">Target selling price <span>THB · optional</span></label><input id="selling" type="number" min="10000" max="1000000" step="1" value={targetSellingPrice} placeholder="Calculated from margin" onChange={e => setTargetSellingPrice(e.target.value)} disabled={running} />
          <label htmlFor="margin">Minimum target margin <span>% of selling price</span></label><input id="margin" type="number" min="0" max="80" step="0.1" required value={margin} onChange={e => setMargin(Number(e.target.value))} disabled={running} />
          <label htmlFor="use">Primary use case</label><select id="use" value={useCase} onChange={e => setUseCase(e.target.value)} disabled={running}><option value="gaming">Gaming</option><option value="office">Office</option><option value="workstation">Workstation</option></select>
          {useCase === "gaming" && <><label htmlFor="resolution">Target resolution</label><select id="resolution" value={resolution} onChange={e => setResolution(e.target.value)} disabled={running}><option>1080p</option><option>1440p</option><option>4k</option></select></>}
          <label htmlFor="cpu">Preferred CPU brand <span>optional</span></label><input id="cpu" value={preferredCpu} placeholder="AMD or Intel" onChange={e => setPreferredCpu(e.target.value)} disabled={running} />
          <label htmlFor="gpu">Preferred GPU vendor <span>optional</span></label><input id="gpu" value={preferredGpu} placeholder="NVIDIA, AMD, ASUS…" onChange={e => setPreferredGpu(e.target.value)} disabled={running} />
          <label htmlFor="ram">Required RAM <span>GB · optional</span></label><input id="ram" type="number" min="4" max="512" value={requiredRam} placeholder="Workload default" onChange={e => setRequiredRam(e.target.value)} disabled={running} />
          <label htmlFor="storage">Required storage <span>GB · optional</span></label><input id="storage" type="number" min="128" max="16384" value={requiredStorage} placeholder="Workload default" onChange={e => setRequiredStorage(e.target.value)} disabled={running} />
          <button disabled={running} type="submit">{running ? "Team is working…" : "Start sourcing →"}</button>
        </form><p className="small muted">Landed cost includes parts, shipping, assembly, packaging and warranty reserve. Software, tax and marketplace fees are excluded.</p>
        {(error || job?.error) && <p className="error" role="alert">{error || job?.error}</p>}
      </section>
      <section className="panel team"><div className="section-title"><div><p className="eyebrow">02 / THE OFFICE</p><h2>Your team at work</h2></div><span className="tag">4 agents</span></div><Office states={states} />
        <div className="agents">{agents.map((agent, i) => <div className="agent" key={agent}><div className="agent-head"><strong>{agent}</strong><span className={`status ${states[agent]}`}>{states[agent]}</span></div><p>{roles[i]}</p></div>)}</div>
      </section>
    </div>
    <div className="results-grid">
      <section className="panel recommendation"><p className="eyebrow">03 / THE RECOMMENDATION</p><h2>{result ? "Your recommended build" : "The next build is yours."}</h2>
        {result ? <><div className="metrics"><div><span>Landed cost</span><strong>{money(result.total_cost)}</strong></div><div><span>Suggested selling price</span><strong>{money(result.suggested_selling_price)}</strong></div><div className="profit"><span>Estimated profit</span><strong>{money(result.estimated_profit)}</strong><small>{result.actual_margin}% margin</small></div></div>
          {(result.candidates?.length ?? 0) > 1 && <div className="candidate-strip">{result.candidates.map(candidate => <div key={candidate.rank}><strong>#{candidate.rank} · {candidate.name || "Ranked build"}</strong><span>{money(candidate.landed_cost ?? candidate.total_cost)} · score {candidate.ranking_score} · {candidate.actual_margin}% margin</span></div>)}</div>}
          <div className="table-scroll"><table><thead><tr><th>Component</th><th>Retailer</th><th>Delivered cost</th></tr></thead><tbody>{result.components.map(c => <tr key={c.listing.sku}><td><span className="category">{c.listing.category}</span><strong>{c.listing.url ? <a className="product-link" href={c.listing.url} target="_blank" rel="noreferrer">{c.listing.name}</a> : c.listing.name}</strong>{c.suspicious ? <p className="error">Needs review</p> : c.alternatives.length === 1 ? <p className="small muted">1 retailer offer</p> : <details><summary>Compare {c.alternatives.length} offers</summary>{c.alternatives.map(a => <p key={`${a.retailer}-${a.delivered_price}`}>{a.url ? <a className="product-link" href={a.url} target="_blank" rel="noreferrer">{a.retailer}</a> : a.retailer}: {money(a.delivered_price)}</p>)}</details>}</td><td>{c.listing.retailer}</td><td>{money(c.delivered_price)}</td></tr>)}</tbody></table></div>
          <p className="muted small">{result.rationale}</p><div className="checks">{result.compatibility_checks.map(check => <span key={check}>✓ {check}</span>)}</div>
        </> : <div className="empty"><span>▧</span><p>{running ? "Your agents are comparing components and checking compatibility." : "Set a budget and let the team put the pieces together."}</p><p className="small muted">Seven components · JIB + Advice · Compatibility checked</p></div>}
      </section>
      <section className="panel feed"><p className="eyebrow">LIVE / ACTIVITY</p><h2>Office updates</h2><div className="events" aria-live="polite">{job?.events.length ? [...job.events].reverse().map(event => <article key={event.id}><div><strong>{event.agent}</strong><time>{new Date(event.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</time></div><p>{event.message}</p></article>) : <p className="muted small">The office is ready. Submit a brief to start the workflow.</p>}</div></section>
    </div>
    <section className="panel deals-panel"><div className="section-title"><div><p className="eyebrow">04 / DEAL HUNTER</p><h2>Current price opportunities</h2></div><span className="tag">{deals?.scan.status === "completed" ? `${deals.count} active` : deals?.scan.status ?? "loading"}</span></div>
      <p className="small muted">Deal Hunter analyzes stored price history after refresh. It never contacts retailers.</p>
      {deals?.deals.length ? <div className="deal-grid">{deals.deals.slice(0, 8).map(deal => <article className="deal-card" key={deal.id}>
        <div><span className={`deal-grade ${deal.grade}`}>{deal.grade}</span><span className="category">{deal.category} · {deal.source}</span></div>
        <h3>{deal.title}</h3><strong className="deal-price">{money(deal.current_price)}</strong>
        <p className="small muted">{deal.median_30d ? `30-day median ${money(deal.median_30d)}` : deal.previous_price ? `Previous ${money(deal.previous_price)}` : "Reduced history confidence"}</p>
        <div className="deal-score"><span>Deal score</span><strong>{deal.deal_score}</strong></div>
        {deal.build_impact && <p className="profit small">Best use: {deal.build_impact.profile.replaceAll("_", " ")}<br />{deal.build_impact.profit_improvement > 0 ? `Potential profit improvement: +${money(deal.build_impact.profit_improvement)}` : "No margin improvement for this profile"}</p>}
        <details><summary>Why this deal</summary>{deal.reasons.map(reason => <p key={reason}>{reason}</p>)}</details>
        {deal.url && <a className="deal-link" href={deal.url} target="_blank" rel="noreferrer">View deal →</a>}
      </article>)}</div> : <div className="empty"><p>{deals?.scan.status === "never_run" ? "Run a deal scan after collecting price history." : "No current opportunities meet the configured thresholds."}</p></div>}
    </section>
    <footer>V1 · Deterministic sourcing agents · Stored live JIB and Advice listings</footer>
  </main>;
}
