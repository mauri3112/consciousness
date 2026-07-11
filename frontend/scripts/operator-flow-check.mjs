import { chromium } from "playwright";

const url = process.env.CONSCIOUSNESS_STUDIO_URL ?? "http://localhost:5173";
const now = new Date().toISOString();
let decisionCount = 0;
let rollbackCount = 0;
let approvalStatus = "pending";

const state = {
  id: "gather", name: "Gather", kind: "agent", domain: "memory", goal_template: "Gather evidence.",
  prompt_contract: "Use sources.", output_contract: "Structured output.", tools: ["memory.search"], skills: ["research"],
  context_minimum: 4096, output_reserve: 1024, model_policy: "local-default", max_attempts: 2,
  max_run_budget: 0.1, x: 20, y: 30, is_current: true
};
const model = {
  id: "local", provider: "ollama", model: "test", context_window: 8192, relative_cost: 0, max_run_budget: 0.1,
  quality_tier: 1, strengths: ["local"], capabilities: ["structured-output"], input_cost_per_million: 0,
  output_cost_per_million: 0, open_weights: true, enabled: true
};
const guardrails = {
  capability_policies: [{ state_id: "gather", allowed_tool_patterns: ["memory.*"], mutation_level: "bounded", requires_approval: true, rationale: "Protect writes." }],
  loop_control: { manual_pause_enabled: true, sleep_window: "", base_backoff_seconds: 1, max_backoff_seconds: 30, max_consecutive_failures: 3, daily_budget_cap: 1, degraded_mode: "pause_writes" },
  evidence_policy: { require_sources: true }
};
const definition = { name: "Operator safety fixture", states: [state], transitions: [], models: [model], guardrails };

function snapshot() {
  return {
    version: { id: "version-2", version: 2, status: "active", digest: "fixture", parent_id: "version-1", revision: 1, definition, created_by_run_id: null, created_at: now, activated_at: now },
    runtime: { active_version_id: "version-2", current_state_id: "gather", status: "degraded", interval_seconds: 30, worker_id: null, lease_expires_at: null, heartbeat_at: null, failure_count: 2, backoff_until: null, daily_budget_cap: 1, execution_mode: "operator", updated_at: now },
    states: [state], transitions: [], models: [model], runs: [], recaps: [], guardrails,
    integrations: [{ name: "only-memories", status: "unreachable", endpoint: "http://localhost:8765", last_checked_at: now, details: { error: "connection refused" } }],
    approvals: [{ id: "approval-1", run_id: null, kind: "procedure_mutation", status: approvalStatus, risk: "Changes the active procedure graph.", proposed_action: { operation: "activate", version: 3 }, evidence: [{ label: "diff", kind: "artifact", uri: "artifact://mutation/diff" }], requested_at: now, decided_at: approvalStatus === "pending" ? null : now, decision_note: approvalStatus === "pending" ? null : "Reviewed evidence." }],
    mutations: [{ id: "mutation-1", base_version_id: "version-1", proposed_version_id: "version-2", proposer_run_id: null, status: "activated", diff: "+ add validation state", rationale: "Improve output quality", budget_impact: { daily_usd: 0.05 }, rollback_version_id: "version-1", created_at: now, decided_at: now }]
  };
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });

await page.route("**/api/v1/procedure", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(snapshot()) }));
await page.route("**/api/v1/approvals/approval-1/decision", async (route) => {
  decisionCount += 1;
  const body = route.request().postDataJSON();
  if (body.approved !== true || body.note !== "Reviewed evidence.") {
    return route.fulfill({ status: 422, contentType: "application/json", body: JSON.stringify({ detail: "Unexpected decision payload" }) });
  }
  approvalStatus = "approved";
  return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(snapshot().approvals[0]) });
});
await page.route("**/api/v1/procedure/versions/version-1/rollback", (route) => {
  rollbackCount += 1;
  return route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ ...snapshot().version, id: "version-rollback", version: 3, parent_id: "version-2" }),
  });
});
await page.route("**/api/v1/events**", (route) => route.fulfill({ status: 200, headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" }, body: "" }));

await page.goto(url, { waitUntil: "domcontentloaded" });
await page.locator(".app-shell").waitFor();
await page.getByText("Runtime degraded.", { exact: false }).waitFor();
const accessibility = await page.evaluate(() => {
  const controls = [...document.querySelectorAll("button, a, input, select, textarea")];
  const missingNames = controls.filter((element) => {
    if (element instanceof HTMLInputElement && element.type === "hidden") return false;
    const labelledBy = element.getAttribute("aria-labelledby");
    const labelledText = labelledBy
      ? labelledBy.split(/\s+/).map((id) => document.getElementById(id)?.textContent ?? "").join(" ")
      : "";
    const ownLabel = element.id ? document.querySelector(`label[for="${CSS.escape(element.id)}"]`)?.textContent ?? "" : "";
    return !(element.getAttribute("aria-label") || labelledText || ownLabel || element.getAttribute("title") || element.textContent || element.getAttribute("alt"));
  });
  const ids = [...document.querySelectorAll("[id]")].map((element) => element.id);
  const duplicateIds = ids.filter((id, index) => ids.indexOf(id) !== index);
  return {
    hasMain: Boolean(document.querySelector("main")),
    hasNavigation: Boolean(document.querySelector("nav[aria-label]")),
    hasSkipLink: Boolean(document.querySelector('a[href="#studio-content"]')),
    missingNames: missingNames.map((element) => element.outerHTML.slice(0, 160)),
    duplicateIds: [...new Set(duplicateIds)],
  };
});
if (!accessibility.hasMain || !accessibility.hasNavigation || !accessibility.hasSkipLink) throw new Error("Required accessibility landmarks are missing");
if (accessibility.missingNames.length) throw new Error(`Unnamed controls: ${accessibility.missingNames.join(" | ")}`);
if (accessibility.duplicateIds.length) throw new Error(`Duplicate ids: ${accessibility.duplicateIds.join(", ")}`);
await page.getByRole("button", { name: "Approvals, 1 pending" }).click();
await page.getByRole("button", { name: "Review approval" }).click();
const note = page.getByLabel("Decision note (recommended)");
if (!(await note.evaluate((element) => element === document.activeElement))) throw new Error("Decision note did not receive focus");
await note.fill("Reviewed evidence.");
await page.getByRole("button", { name: "Confirm approve" }).click();
await page.getByText("Decision note", { exact: true }).waitFor();
if (decisionCount !== 1) throw new Error(`Approval submitted ${decisionCount} times`);

await page.getByRole("button", { name: "Mutations" }).click();
await page.getByRole("button", { name: "Review rollback" }).click();
await page.getByRole("group", { name: "Rollback confirmation" }).waitFor();
await page.getByRole("button", { name: "Cancel" }).click();
if (await page.getByRole("group", { name: "Rollback confirmation" }).count()) throw new Error("Rollback confirmation did not close");
await page.getByRole("button", { name: "Review rollback" }).click();
await page.getByRole("button", { name: "Confirm rollback" }).click();
if (rollbackCount !== 1) throw new Error(`Rollback submitted ${rollbackCount} times`);

const allViewsReachable = await Promise.all(["Overview", "Editor", "Mutations", "Runs", "Approvals", "Models", "only-memories"].map((name) => page.getByRole("button", { name: new RegExp(`^${name}`) }).isVisible()));
if (allViewsReachable.some((visible) => !visible)) throw new Error("A mobile navigation destination is hidden");
if (errors.length) throw new Error(`Browser errors: ${errors.join(" | ")}`);

await page.close();

const disconnected = await browser.newPage({ viewport: { width: 1280, height: 800 } });
await disconnected.route("**/api/v1/procedure", (route) => route.abort("connectionrefused"));
await disconnected.route("**/api/v1/events**", (route) => route.abort("connectionrefused"));
await disconnected.goto(url, { waitUntil: "domcontentloaded" });
await disconnected.getByRole("heading", { name: "Studio cannot reach the API" }).waitFor();
await disconnected.getByRole("button", { name: "Retry connection" }).waitFor();
await disconnected.close();
await browser.close();

console.log(JSON.stringify({ approvalSubmittedExactlyOnce: decisionCount === 1, mobileViewsReachable: true, rollbackRequiresConfirmation: true, rollbackSubmittedExactlyOnce: rollbackCount === 1, disconnectedStateRendered: true, accessibilitySmoke: accessibility }, null, 2));
