import fs from "node:fs/promises";
import { spawn } from "node:child_process";
import { chromium } from "file:///C:/Users/wxx/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/.pnpm/playwright@1.61.1/node_modules/playwright/index.mjs";

const chrome = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const output = new URL("../docs/assets/", import.meta.url);
await fs.mkdir(output, { recursive: true });

const projectRoot = new URL("../", import.meta.url).pathname.slice(1);
const port = Number(process.env.EMBODISCOPE_PORT || 8765);
const baseUrl = `http://127.0.0.1:${port}`;
let server = null;
try {
  const response = await fetch(`${baseUrl}/api/health`);
  if (!response.ok) throw new Error("unhealthy");
} catch {
  const args = port === 8765
    ? ["run.py"]
    : ["-c", `from pathlib import Path; from embodiscope.server import run_server; p=Path.cwd(); run_server(p, p/'data'/'demo_pick_place.csv', port=${port})`];
  server = spawn("python", args, { cwd: projectRoot, stdio: "ignore", windowsHide: true });
}
for (let attempt = 0; attempt < 30; attempt++) {
  try {
    const response = await fetch(`${baseUrl}/api/health`);
    if (response.ok) break;
  } catch {}
  await new Promise(resolve => setTimeout(resolve, 200));
}

const browser = await chromium.launch({ headless: true, executablePath: chrome });

async function capture(name, viewport, action) {
  const page = await browser.newPage({ viewport, deviceScaleFactor: 1 });
  const consoleErrors = [];
  page.on("console", message => { if (message.type() === "error") consoleErrors.push(message.text()); });
  await page.goto(baseUrl, { waitUntil: "networkidle" });
  await page.waitForSelector("#dashboard:not([hidden])");
  if (action) await action(page);
  await page.evaluate(() => scrollTo(0, 0));
  await page.waitForTimeout(100);
  await page.screenshot({ path: new URL(`${name}.png`, output).pathname.slice(1), fullPage: true });
  const diagnostics = await page.evaluate(() => ({
    viewport: [innerWidth, innerHeight],
    document: [document.documentElement.scrollWidth, document.documentElement.scrollHeight],
    horizontalOverflow: document.documentElement.scrollWidth > innerWidth + 1,
    dashboardVisible: !document.querySelector("#dashboard").hidden,
    metricWidths: [...document.querySelectorAll(".metric")].map(node => Math.round(node.getBoundingClientRect().width)),
    spatialCanvas: document.querySelector("#panel-spatial.active")
      ? [document.querySelector("#spatialCanvas").width, document.querySelector("#spatialCanvas").height]
      : null,
  }));
  await page.close();
  return { name, consoleErrors, ...diagnostics };
}

const results = [];
results.push(await capture("dashboard-overview", { width: 1440, height: 1000 }, async page => {
  await page.click('[data-episode="EP-003"]');
  await page.waitForFunction(() => document.querySelector("#episodeTitle")?.textContent === "EP-003");
}));
results.push(await capture("dashboard-signals", { width: 1440, height: 1000 }, async page => {
  await page.click('[data-episode="EP-002"]');
  await page.waitForFunction(() => document.querySelector("#episodeTitle")?.textContent === "EP-002");
  await page.click('[data-tab="signals"]');
  await page.click('[data-signal="ee_speed"]');
  await page.click('[data-signal="ee_x"]');
  await page.click('[data-signal="ee_y"]');
  await page.click('[data-signal="ee_z"]');
  await page.click('[data-signal="camera_motion"]');
  await page.waitForTimeout(250);
}));
results.push(await capture("dashboard-spatial", { width: 1440, height: 1000 }, async page => {
  await page.click('[data-episode="EP-003"]');
  await page.waitForFunction(() => document.querySelector("#episodeTitle")?.textContent === "EP-003");
  await page.click('[data-tab="spatial"]');
  await page.waitForSelector("#panel-spatial.active");
  await page.click("#spatialPlay");
  await page.waitForTimeout(500);
  await page.locator("#spatialCanvas").screenshot({ path: new URL("spatial-canvas.png", output).pathname.slice(1) });
}));
results.push(await capture("dashboard-mobile", { width: 390, height: 844 }, async page => {
  await page.click('[data-episode="EP-006"]');
  await page.waitForFunction(() => document.querySelector("#episodeTitle")?.textContent === "EP-006");
}));
results.push(await capture("dashboard-spatial-mobile", { width: 390, height: 844 }, async page => {
  await page.click('[data-episode="EP-003"]');
  await page.waitForFunction(() => document.querySelector("#episodeTitle")?.textContent === "EP-003");
  await page.click('[data-tab="spatial"]');
  await page.waitForSelector("#panel-spatial.active");
  await page.waitForTimeout(300);
}));
results.push(await capture("adapter-ecosystem", { width: 1440, height: 1000 }, async page => {
  await page.setInputFiles("#fileInput", `${projectRoot}/data/demo_lerobot.parquet`);
  await page.waitForFunction(() => document.querySelector("#sourceFormat")?.textContent === "LeRobot Parquet");
  await page.click("#adapterButton");
  await page.waitForSelector("#adapterModal:not([hidden])");
}));
results.push(await capture("mcap-diagnosis", { width: 1440, height: 1000 }, async page => {
  await page.setInputFiles("#fileInput", `${projectRoot}/data/demo_ros2_collision.mcap`);
  await page.waitForFunction(() => document.querySelector("#sourceFormat")?.textContent === "ROS bag / MCAP");
  await page.waitForFunction(() => document.querySelector("#episodeTitle")?.textContent === "demo_ros2_collision");
}));
results.push(await capture("maniskill-spatial", { width: 1440, height: 1000 }, async page => {
  await page.setInputFiles("#fileInput", `${projectRoot}/data/demo_maniskill_collision.h5`);
  await page.waitForFunction(() => document.querySelector("#sourceFormat")?.textContent === "ManiSkill HDF5");
  await page.waitForFunction(() => document.querySelector("#episodeTitle")?.textContent === "0");
  await page.click('[data-tab="spatial"]');
  await page.waitForSelector("#panel-spatial.active");
  await page.waitForTimeout(300);
}));

await fs.writeFile(new URL("visual-check.json", output), JSON.stringify(results, null, 2));
await browser.close();
server?.kill();
await fs.rm(`${projectRoot}/data/uploads/demo_lerobot.parquet`, { force: true });
await fs.rm(`${projectRoot}/data/uploads/demo_ros2_collision.mcap`, { force: true });
await fs.rm(`${projectRoot}/data/uploads/demo_maniskill_collision.h5`, { force: true });

if (results.some(result => result.consoleErrors.length || result.horizontalOverflow || !result.dashboardVisible)) {
  console.error(JSON.stringify(results, null, 2));
  process.exitCode = 1;
} else {
  console.log(JSON.stringify(results, null, 2));
}
