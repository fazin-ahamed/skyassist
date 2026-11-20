// Real-browser click-through of the kiosk and coordinator pages against a real service.
import { spawn, spawnSync } from "node:child_process";
import { existsSync, readdirSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { chromium } from "playwright-core";

const ROOT = resolve(import.meta.dirname, "..", "..");
const PORT = Number(process.env.E2E_PORT ?? 8021);
const W = Number(process.env.E2E_W ?? 1440);
const H = Number(process.env.E2E_H ?? 900);
const TOUCH = W <= 1024;
const TAG = process.env.E2E_TAG ?? "";
const THEME = process.env.E2E_THEME ?? "";
const BASE = `http://127.0.0.1:${PORT}`;
const SHOTS = process.env.E2E_SHOTS ?? join(ROOT, ".impeccable", "review", "boardui");
mkdirSync(SHOTS, { recursive: true });

function chromePath() {
  if (process.env.E2E_BROWSER) return process.env.E2E_BROWSER;
  if (process.platform === "win32") {
    return join(process.env.ProgramFiles ?? "C:/Program Files", "Google", "Chrome", "Application", "chrome.exe");
  }
  const base = join(homedir(), ".cache", "ms-playwright");
  const dir = readdirSync(base).filter((d) => d.startsWith("chromium_headless_shell")).sort().pop();
  const sub = readdirSync(join(base, dir)).find((d) => d.startsWith("chrome-headless-shell"));
  const bin = join(base, dir, sub, "chrome-headless-shell");
  if (!existsSync(bin)) throw new Error("no chromium headless shell found");
  return bin;
}

const localPython = process.platform === "win32" ? join(ROOT, ".venv", "Scripts", "python.exe") : join(ROOT, ".venv", "bin", "python");
const python = process.env.E2E_PYTHON ?? (existsSync(localPython) ? localPython : null);
const server = spawn(python ?? "uv", [...(python ? [] : ["run", "python"]), "web/e2e/serve.py", String(PORT)], { cwd: ROOT, stdio: "ignore" });
server.on("error", (error) => console.error("Could not start the fixture API:", error.message));
const results = [];
const step = async (name, fn) => {
  try { await fn(); results.push(["PASS", name]); } catch (e) {
    await globalThis.__page?.screenshot({ path: join(SHOTS, `e2e-fail${TAG}.png`) }).catch(() => {}); results.push(["FAIL", `${name}: ${String(e.message).split("\n")[0]}`]); throw e; }
};

async function waitForServer() {
  for (let i = 0; i < 80; i++) {
    try { if ((await fetch(`${BASE}/clock`)).ok) return; } catch {}
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error("service did not start");
}

let browser;
try {
  await waitForServer();
  for (const id of ["ev-c01-lift-closed", "ev-c01-lift-open", "ev-c01-ready", "ev-c01-gate-b14"]) {
    const r = await fetch(`${BASE}/demo/events/${id}/apply`, { method: "POST", headers: { Authorization: "Bearer demo-coordinator" } });
    if (!r.ok) throw new Error(`apply ${id} ${r.status}`);
  }
  browser = await chromium.launch({ executablePath: chromePath(), args: ["--no-sandbox"] });
  const ctx = await browser.newContext({ viewport: { width: W, height: H }, hasTouch: TOUCH });
  const page = await ctx.newPage();
  globalThis.__page = page;
  const errors = [];
  const watchErrors = (tab) => {
    tab.on("pageerror", (error) => errors.push(String(error)));
    tab.on("console", (message) => message.type() === "error" && errors.push(message.text()));
  };
  watchErrors(page);
  ctx.on("page", watchErrors);
  const T = { timeout: 20000 };

  await step("the canvas opens on the question and the Dubai plan", async () => {
    await page.goto(THEME ? `${BASE}/?theme=${THEME}` : `${BASE}/`);
    await page.getByRole("heading", { name: "Where are you connecting?" }).waitFor(T);
    await page.getByRole("img", { name: /Plan of Concourse/ }).waitFor(T);
    await page.screenshot({ path: join(SHOTS, `e2e-idle${TAG}.png`) });
  });
  await step("help dialog opens and closes", async () => {
    await page.getByRole("button", { name: "Help", exact: true }).click();
    await page.getByRole("dialog", { name: "Help" }).waitFor(T);
    await page.getByRole("button", { name: "Close help" }).click();
    await page.getByRole("dialog", { name: "Help" }).waitFor({ state: "hidden", ...T });
  });
  await step("the plan switches between concourses and the terminal overview", async () => {
    await page.getByRole("tab", { name: "Concourse B" }).click();
    await page.getByRole("img", { name: "Plan of Concourse B" }).waitFor(T);
    await page.getByRole("tab", { name: "Terminal 3" }).click();
    await page.getByText("Train to Concourse A").waitFor(T);
    await page.getByRole("tab", { name: "Concourse C" }).click();
  });
  await step("the island opens into a chat and a sentence starts the journey", async () => {
    await page.getByRole("button", { name: "Open the conversation" }).click();
    await page.getByText("Hi, I'm SkyAssist.").waitFor(T);
    await page.screenshot({ path: join(SHOTS, `e2e-chat${TAG}.png`), animations: "disabled" });
    const input = page.getByRole("textbox", { name: "Type a message or tap the microphone" });
    await input.fill("We land on DXB-402 and fly on ZX 515, my mum uses a wheelchair");
    await input.press("Enter");
    await page.getByRole("button", { name: /Service on .*19 Nov/ }).waitFor(T);
  });
  await step("the agent asks only what it cannot know, then shows the gate", async () => {
    await page.getByRole("button", { name: /Service on .*19 Nov/ }).click();
    await page.getByRole("button", { name: /I am at C22/ }).click();
    await page.getByLabel("B14").first().waitFor(T);
    await page.getByText("Tight", { exact: true }).first().waitFor(T);
    await page.getByText("min to spare").first().waitFor(T);
    await page.screenshot({ path: join(SHOTS, `e2e-gate${TAG}.png`), animations: "disabled" });
  });
  await step("the agent explains itself in grounded words and the ledger comes forward", async () => {
    await page.getByText("Written from verified facts").first().waitFor(T);
    await page.getByRole("button", { name: "Why this status?" }).click();
    await page.getByText(/Verified: Gate B14/).first().waitFor(T);
    await page.getByRole("heading", { name: "What I can stand behind" }).waitFor(T);
    await page.getByRole("heading", { name: "Your route" }).waitFor(T);
  });
  await step("the judge view shows the split and refuses a lie", async () => {
    await page.getByRole("button", { name: "How it decides" }).click();
    const sheet = page.getByRole("dialog", { name: "How it decides" });
    await sheet.getByText("Unverified promises shown").waitFor(T);
    await sheet.getByText("32/32").waitFor(T);
    await sheet.getByRole("button", { name: "Promise" }).click();
    await sheet.getByText("Refused", { exact: true }).first().waitFor(T);
    await page.screenshot({ path: join(SHOTS, `e2e-judge${TAG}.png`), animations: "disabled" });
    await sheet.getByRole("button", { name: "Close" }).click();
    await sheet.waitFor({ state: "hidden", ...T });
  });
  await step("page has no horizontal overflow", async () => {
    const { over, who } = await page.evaluate(() => {
      const width = window.innerWidth;
      const wide = [...document.querySelectorAll("body *")].find((el) => el.getBoundingClientRect().right > width + 1 && !el.closest("svg") && getComputedStyle(el).position !== "fixed");
      return { over: document.documentElement.scrollWidth - width, who: wide ? `${wide.tagName}.${String(wide.className).slice(0, 60)}` : "" };
    });
    if (over > 1) throw new Error(`overflow ${over}px at ${who}`);
  });
  await step("assistance request reaches pending approval", async () => {
    await page.getByRole("button", { name: "Minimise the conversation" }).click();
    await page.getByRole("button", { name: "Ask for staff help" }).first().click();
    await page.getByRole("dialog", { name: "Request physical assistance" }).waitFor(T);
    await page.getByText("I can stay at this pickup point until staff arrive").click();
    await page.getByText("I agree to share this pickup point and request with airport staff").click();
    await page.getByRole("button", { name: "Submit request" }).click();
    await page.getByText("Assistance requested. A coordinator has not approved it yet").first().waitFor(T);
    await page.screenshot({ path: join(SHOTS, `e2e-pending${TAG}.png`) });
  });
  const staffPage = await ctx.newPage();
  await step("coordinator approves from the dashboard", async () => {
    await staffPage.goto(`${BASE}/staff/#token=demo-coordinator`);
    await staffPage.getByRole("heading", { name: "Case queue" }).waitFor(T);
    const alert = staffPage.getByRole("alert").filter({ hasText: "Wheelchair" });
    await alert.waitFor(T);
    await staffPage.screenshot({ path: join(SHOTS, `e2e-staff-alert${TAG}.png`) });
    await alert.getByRole("button", { name: "Approve" }).click();
    await staffPage.getByText("Approved. Waiting for dispatch.").first().waitFor(T);
  });
  await step("the staff help card stamps the approval on its own", async () => {
    await page.getByText("Approved", { exact: true }).first().waitFor(T);
    await page.getByText("Reviewed", { exact: true }).first().waitFor(T);
    await page.screenshot({ path: join(SHOTS, `e2e-approved${TAG}.png`) });
    await staffPage.getByRole("button", { name: /Mark dispatched/ }).click();
    await page.getByText("A staff member is on the way").first().waitFor(T);
  });
  await step("scenario control applies an event once", async () => {
    await staffPage.getByText("Scenario controls").click();
    await staffPage.getByRole("button", { name: "Advance 1 minute" }).click();
    await staffPage.getByText(/Clock .*Dubai/).waitFor(T);
  });
  await step("Arabic switches to right-to-left and keeps gate codes", async () => {
    await page.getByRole("radio", { name: "عربي" }).click();
    await page.waitForFunction(() => document.documentElement.dir === "rtl", null, T);
    await page.getByLabel("B14").first().waitFor(T);
    await page.waitForTimeout(900);
    await page.screenshot({ path: join(SHOTS, `e2e-arabic${TAG}.png`) });
    await page.getByRole("radio", { name: "EN" }).click();
  });
  await step("ending the session revokes it and returns to the first screen", async () => {
    const revoked = page.waitForResponse((response) => response.request().method() === "DELETE" && response.url().includes("/journeys/"));
    await page.getByRole("button", { name: "End session" }).click();
    if (!(await revoked).ok()) throw new Error("remote session was not revoked");
    await page.getByRole("heading", { name: "Where are you connecting?" }).waitFor(T);
  });
  await step("no browser errors", async () => {
    const real = errors.filter((e) => !/favicon|Failed to load resource.*404/i.test(e));
    if (real.length) throw new Error(real[0]);
  });
} catch (e) {
  console.error(e.message);
} finally {
  await browser?.close();
  if (process.platform === "win32" && server.pid) spawnSync("taskkill", ["/pid", String(server.pid), "/t", "/f"], { stdio: "ignore" });
  else server.kill();
  for (const [s, n] of results) console.log(`${s} ${n}`);
  process.exit(results.some(([s]) => s === "FAIL") || results.length < 13 ? 1 : 0);
}
