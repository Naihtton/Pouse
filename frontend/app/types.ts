export type Status = "idle" | "working" | "completed" | "error";
export const agents = ["Manager", "Market Scout", "PC Builder", "Market Analyst"] as const;
export type Agent = typeof agents[number];
export type AgentStates = Record<Agent, Status>;
export const initialStates: AgentStates = { Manager: "idle", "Market Scout": "idle", "PC Builder": "idle", "Market Analyst": "idle" };
export type Result = {
  components: { listing: { sku: string; name: string; category: string; retailer: string; price: number; shipping: number }; delivered_price: number; alternatives: { retailer: string; delivered_price: number }[] }[];
  total_cost: number; suggested_selling_price: number; estimated_profit: number; actual_margin: number;
  compatibility_checks: string[]; rationale: string;
};
export type Job = {
  id: string; status: "queued" | "working" | "completed" | "error"; revision: number;
  agents: AgentStates; events: { id: number; agent: string; message: string; at: string }[];
  result: Result | null; error: string | null;
};
