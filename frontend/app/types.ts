export type Status = "idle" | "working" | "completed" | "error";
export const agents = ["Manager", "Market Scout", "PC Builder", "Market Analyst"] as const;
export type Agent = typeof agents[number];
export type AgentStates = Record<Agent, Status>;
export const initialStates: AgentStates = { Manager: "idle", "Market Scout": "idle", "PC Builder": "idle", "Market Analyst": "idle" };
export type Component = { listing: { sku: string; name: string; category: string; retailer: string; price: number; shipping: number | null; url?: string | null }; delivered_price: number; alternatives: { retailer: string; delivered_price: number; url?: string | null }[]; suspicious: boolean; suspicious_reasons: string[] };
export type Candidate = {
  rank: number; name: string; badges: string[]; components: Component[];
  total_parts_cost: number; shipping_cost: number; assembly_cost: number; packaging_cost: number;
  warranty_reserve: number; landed_cost: number; target_sell_price: number | null; required_sell_price: number;
  total_cost: number; suggested_selling_price: number; estimated_profit: number; actual_margin: number;
  performance_score: number; value_score: number; ranking_score: number;
  scores: Record<string, number>;
  compatibility: { compatible: boolean; checks: { rule: string; status: "pass" | "fail" | "unknown"; details: string }[] };
  compatibility_checks: string[]; rationale: string;
};
export type Result = Pick<Candidate, "components" | "total_cost" | "suggested_selling_price" | "estimated_profit" | "actual_margin" | "compatibility_checks" | "rationale"> & { candidates: Candidate[] };
export type Job = {
  id: string; status: "queued" | "working" | "completed" | "error"; revision: number;
  agents: AgentStates; events: { id: number; agent: string; message: string; at: string }[];
  result: Result | null; error: string | null;
};
export type Deal = {
  id: string; title: string; category: string; source: string; url: string | null;
  current_price: number; previous_price: number | null; median_7d: number | null;
  median_30d: number | null; discount_vs_7d_pct: number | null;
  discount_vs_30d_pct: number | null; deal_score: number;
  grade: "weak" | "good" | "strong" | "exceptional";
  reasons: string[]; opportunity_types: string[];
  build_impact: { profile: string; profit_improvement: number; cost_improvement: number } | null;
};
export type DealsResponse = {
  count: number; deals: Deal[];
  scan: { status: string; completed_at?: string | null; summary: Record<string, number> };
};
