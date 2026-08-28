import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { createHash } from "node:crypto";
import { chromium } from "playwright";
import axe from "axe-core";
import pixelmatch from "pixelmatch";
import { PNG } from "pngjs";

const PROTOCOL_VERSION = 1;
const FROZEN_TIME = "2020-01-02T03:04:05.000Z";

function fail(message) {
  process.stderr.write(`VAL browser: ${message}\n`);
  process.exitCode = 3;
}

async function readRequest() {
  let source = "";
  for await (const chunk of process.stdin) source += chunk;
  let request;
  try {
    request = JSON.parse(source);
  } catch {
    throw new Error("stdin must contain one JSON object");
  }
  if (request?.protocolVersion !== PROTOCOL_VERSION) throw new Error("unsupported protocolVersion");
  if (typeof request.url !== "string" || !request.url) throw new Error("url is required");
  if (typeof request.outDir !== "string" || !path.isAbsolute(request.outDir)) throw new Error("outDir must be absolute");
  if (request.fixtureFile != null && (typeof request.fixtureFile !== "string" || !path.isAbsolute(request.fixtureFile))) throw new Error("fixtureFile must be an absolute path");
  const operation = request.operation || "capture";
  if (!["capture", "auth-check", "auth-login", "auth-manual"].includes(operation)) throw new Error("unsupported operation");
  if (operation === "capture" && (!Array.isArray(request.shots) || request.shots.length === 0)) throw new Error("shots must be a non-empty array");
  if (operation !== "capture" && (typeof request.successCheck !== "string" || !request.successCheck)) throw new Error("successCheck is required for authentication");
  if (operation === "auth-login" && (!Array.isArray(request.steps) || request.steps.length === 0)) throw new Error("authentication steps are required");
  if (["auth-login", "auth-manual"].includes(operation) && (typeof request.authState !== "string" || !path.isAbsolute(request.authState))) throw new Error("authState must be an absolute path");
  return request;
}

function shotName(shot) {
  const route = shot.route === "/" ? "root" : shot.route.replace(/^\/+|\/+$/g, "").replace(/[^A-Za-z0-9._-]+/g, "-") || "root";
  const [width, height] = shot.viewport;
  const identity = createHash("sha256").update(shot.route).digest("hex").slice(0, 10);
  return `${route}--${identity}__${width}x${height}__${shot.theme}.png`;
}

function validateShot(shot) {
  if (typeof shot?.route !== "string" || !shot.route.startsWith("/")) throw new Error("shot route must start with /");
  if (!Array.isArray(shot.viewport) || shot.viewport.length !== 2 || !shot.viewport.every((value) => Number.isInteger(value) && value > 0)) {
    throw new Error("shot viewport must contain two positive integers");
  }
  if (!["light", "dark"].includes(shot.theme)) throw new Error("shot theme must be light or dark");
}

async function installDeterminism(context) {
  await context.addInitScript(({ frozenTime }) => {
    const NativeDate = Date;
    const fixed = new NativeDate(frozenTime).valueOf();
    class FrozenDate extends NativeDate {
      constructor(...args) {
        super(...(args.length === 0 ? [fixed] : args));
      }
      static now() { return fixed; }
    }
    Object.defineProperty(globalThis, "Date", { value: FrozenDate });
    let seed = 0x12345678;
    Math.random = () => {
      seed = (1664525 * seed + 1013904223) >>> 0;
      return seed / 0x100000000;
    };
  }, { frozenTime: FROZEN_TIME });
}

async function installNetworkBoundary(context, request) {
  const allowedOrigins = new Set([new URL(request.url).origin, ...(request.allowedOrigins || [])]);
  const fixture = request.fixtureFile ? JSON.parse(await fs.readFile(request.fixtureFile, "utf8")) : { routes: [] };
  await context.route("**/*", async (route) => {
    const currentRequest = route.request();
    const url = currentRequest.url();
    const match = (fixture.routes || []).find((entry) => entry.url === url && (!entry.method || entry.method === currentRequest.method()));
    if (match) {
      await route.fulfill({ status: match.status || 200, contentType: match.contentType || "application/json", body: typeof match.body === "string" ? match.body : JSON.stringify(match.body ?? null) });
      return;
    }
    if (/^(data|blob):/.test(url) || allowedOrigins.has(new URL(url).origin)) await route.continue();
    else await route.abort("blockedbyclient");
  });
}

async function settle(page, enabled) {
  if (enabled) {
    await page.waitForLoadState("networkidle", { timeout: 3000 }).catch(() => {});
    await page.waitForTimeout(300);
  }
  await page.evaluate(async () => {
    await document.fonts?.ready;
    const images = [...document.images].filter((image) => !image.complete);
    await Promise.all(images.map((image) => image.decode?.().catch(() => {})));
    window.scrollTo(0, 0);
    await new Promise((resolve) => requestAnimationFrame(() => resolve()));
  });
}

function requested(request, id) {
  return !Array.isArray(request.checks) || request.checks.includes(id);
}

async function domChecks(page, request) {
  const checks = [];
  if (requested(request, "overflow")) {
    const overflow = await page.evaluate(() => ({ width: document.documentElement.clientWidth, scrollWidth: document.documentElement.scrollWidth }));
    checks.push({ id: "overflow", status: overflow.scrollWidth > overflow.width ? "fail" : "pass", detail: overflow.scrollWidth > overflow.width ? `scrollWidth ${overflow.scrollWidth} > ${overflow.width}` : "no horizontal overflow" });
  }
  if (requested(request, "clipped")) {
    const clipped = await page.evaluate(() => [...document.querySelectorAll('[data-val-check-visibility],main,header,nav,section,article,aside,form,fieldset,button,a[href],input,select,textarea,img,video,canvas,svg,table')]
      .filter((element) => {
        const style = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return !element.closest('[aria-hidden="true"],[inert]')
          && style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0
          && (rect.left < -1 || rect.right > document.documentElement.clientWidth + 1);
      })
      .slice(0, 10)
      .map((element) => element.id ? `#${element.id}` : element.tagName.toLowerCase()));
    checks.push({ id: "clipped", status: clipped.length ? "fail" : "pass", detail: clipped.length ? `offscreen: ${clipped.join(", ")}` : "visible semantic and interactive elements are within the viewport" });
  }
  if (requested(request, "tap-target")) {
    const undersized = await page.evaluate(() => [...document.querySelectorAll("button,a,input,select,textarea")]
      .filter((element) => {
        const style = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return !element.hasAttribute("disabled") && style.display !== "none" && style.visibility !== "hidden"
          && rect.width > 0 && rect.height > 0 && (rect.width < 44 || rect.height < 44);
      })
      .slice(0, 10)
      .map((element) => `${element.tagName.toLowerCase()} ${Math.round(element.getBoundingClientRect().width)}x${Math.round(element.getBoundingClientRect().height)}`));
    checks.push({ id: "tap-target", status: undersized.length ? "fail" : "pass", detail: undersized.length ? undersized.join(", ") : "interactive targets are at least 44x44" });
  }
  if (requested(request, "viewport")) {
    const viewport = await page.evaluate(() => document.querySelector('meta[name="viewport"]')?.getAttribute("content") || "");
    const invalid = !viewport || /user-scalable\s*=\s*no|maximum-scale\s*=\s*1(?:\.0+)?(?:\s|,|$)/i.test(viewport);
    checks.push({ id: "viewport", status: invalid ? "fail" : "pass", detail: !viewport ? "viewport meta missing" : invalid ? `restrictive viewport meta: ${viewport}` : "viewport meta allows zoom" });
  }
  if (requested(request, "axe")) {
    await page.addScriptTag({ content: axe.source });
    const result = await page.evaluate(async () => globalThis.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] } }));
    if (result.violations.length === 0) checks.push({ id: "axe", status: "pass", detail: "no WCAG A/AA violations" });
    else for (const violation of result.violations) checks.push({ id: `axe-${violation.id}`, status: "fail", detail: `${violation.help}: ${violation.nodes.length} node(s)` });
  }
  return checks;
}

async function focusCheck(page, request, name) {
  if (!requested(request, "focus")) return null;
  const candidates = await page.evaluate(() => {
    const selector = 'button:not([disabled]),a[href],input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';
    return [...document.querySelectorAll(selector)]
      .filter((element) => {
        const style = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
      })
      .slice(0, 20)
      .map((element, index) => {
        element.dataset.valFocusIndex = String(index);
        return { index, label: element.id ? `#${element.id}` : element.tagName.toLowerCase() };
      });
  });
  if (candidates.length === 0) return { id: "focus", status: "pass", detail: "no keyboard-focusable elements" };
  const failures = [];
  for (const candidate of candidates) {
    await page.evaluate(() => {
      document.body.tabIndex = -1;
      document.body.focus();
      window.scrollTo(0, 0);
    });
    const locator = page.locator(`[data-val-focus-index="${candidate.index}"]`);
    await locator.scrollIntoViewIfNeeded();
    const box = await locator.boundingBox();
    if (!box) continue;
    const clip = { x: Math.max(0, box.x - 4), y: Math.max(0, box.y - 4), width: Math.max(1, box.width + 8), height: Math.max(1, box.height + 8) };
    const before = await page.screenshot({ clip, animations: "disabled", caret: "hide" });
    let reached = false;
    for (let step = 0; step < candidates.length + 2; step += 1) {
      await page.keyboard.press("Tab");
      reached = await locator.evaluate((element) => document.activeElement === element);
      if (reached) break;
    }
    const after = await page.screenshot({ clip, animations: "disabled", caret: "hide" });
    const beforePng = PNG.sync.read(before);
    const afterPng = PNG.sync.read(after);
    const changed = beforePng.width === afterPng.width && beforePng.height === afterPng.height
      ? pixelmatch(beforePng.data, afterPng.data, null, beforePng.width, beforePng.height, { threshold: 0.1, includeAA: false })
      : 0;
    if (!reached || changed === 0) {
      const evidenceDir = path.join(request.outDir, "focus");
      await fs.mkdir(evidenceDir, { recursive: true });
      const stem = `${name.replace(/\.png$/, "")}__${candidate.index}`;
      await Promise.all([fs.writeFile(path.join(evidenceDir, `${stem}__before.png`), before), fs.writeFile(path.join(evidenceDir, `${stem}__after.png`), after)]);
      failures.push(`${candidate.label} ${reached ? "has no visible pixel change" : "was not reached by Tab"}`);
    }
  }
  return { id: "focus", status: failures.length ? "fail" : "pass", detail: failures.length ? failures.join(", ") : `${candidates.length} focus target(s) visibly changed` };
}

async function pixelCheck(request, name, outputPath) {
  if (!requested(request, "pixel")) return null;
  if (!request.baselineDir) return { id: "pixel", status: "manual", detail: "baselineDir not configured" };
  const baselinePath = path.join(request.baselineDir, name);
  try {
    await fs.access(baselinePath);
  } catch {
    return { id: "pixel", status: "manual", detail: `baseline missing: ${baselinePath}` };
  }
  const [actual, baseline] = await Promise.all([fs.readFile(outputPath), fs.readFile(baselinePath)]);
  const actualPng = PNG.sync.read(actual);
  const baselinePng = PNG.sync.read(baseline);
  if (actualPng.width !== baselinePng.width || actualPng.height !== baselinePng.height) {
    return { id: "pixel", status: "fail", detail: `dimensions ${actualPng.width}x${actualPng.height} != ${baselinePng.width}x${baselinePng.height}` };
  }
  const diff = new PNG({ width: actualPng.width, height: actualPng.height });
  const changed = pixelmatch(actualPng.data, baselinePng.data, diff.data, actualPng.width, actualPng.height, { threshold: 0.1, includeAA: false });
  const total = actualPng.width * actualPng.height;
  const ratio = changed / total;
  if (changed > 0) {
    const diffDir = path.join(request.outDir, "diffs");
    await fs.mkdir(diffDir, { recursive: true });
    await fs.writeFile(path.join(diffDir, name), PNG.sync.write(diff));
  }
  return { id: "pixel", status: ratio > 0.001 ? "fail" : "pass", detail: `${changed}/${total} pixels changed (${(ratio * 100).toFixed(4)}%; limit 0.1%)` };
}

async function capture(browser, request, shot) {
  validateShot(shot);
  const [width, height] = shot.viewport;
  const context = await browser.newContext({
    viewport: { width, height },
    deviceScaleFactor: 2,
    colorScheme: shot.theme,
    reducedMotion: "reduce",
    locale: "en-US",
    timezoneId: "UTC",
    serviceWorkers: "block",
    storageState: request.authState || undefined,
  });
  await installDeterminism(context);
  await installNetworkBoundary(context, request);
  const page = await context.newPage();
  await page.emulateMedia({ colorScheme: shot.theme, reducedMotion: "reduce" });
  const targetUrl = new URL(shot.route, request.url);
  await page.goto(targetUrl.href, { waitUntil: "domcontentloaded", timeout: request.navigationTimeoutMs || 30000 });
  await page.addStyleTag({ content: "*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}html{scroll-behavior:auto!important}::selection{background:transparent!important}" });
  await settle(page, request.settle !== false);
  const name = shotName(shot);
  const outputPath = path.join(request.outDir, name);
  await fs.mkdir(request.outDir, { recursive: true });
  const landedPath = new URL(page.url()).pathname;
  const authRedirect = landedPath !== targetUrl.pathname && /\/(?:login|sign-?in|auth)(?:\/|$)/i.test(landedPath);
  if (!request.authState && (authRedirect || await page.locator('input[type="password"]').count() > 0)) {
    await page.screenshot({ path: outputPath, fullPage: true, animations: "disabled", caret: "hide", mask: [page.locator('input[type="password"]')] });
    await context.close();
    return { path: outputPath, route: shot.route, viewport: shot.viewport, theme: shot.theme, blocked: "auth-required", checks: [{ id: "auth-required", status: "blocked", detail: `route requires authentication (landed on ${landedPath})` }] };
  }
  const checks = await domChecks(page, request);
  const focus = await focusCheck(page, request, name);
  if (focus) checks.push(focus);
  await page.screenshot({ path: outputPath, fullPage: true, animations: "disabled", caret: "hide", scale: "device" });
  const pixel = await pixelCheck(request, name, outputPath);
  if (pixel) checks.push(pixel);
  const result = { path: outputPath, route: shot.route, viewport: shot.viewport, theme: shot.theme, checks };
  await context.close();
  return result;
}

async function authCheck(browser, request) {
  const context = await browser.newContext({ storageState: request.authState || undefined, serviceWorkers: "block" });
  await installNetworkBoundary(context, request);
  const page = await context.newPage();
  try {
    await page.goto(request.url, { waitUntil: "domcontentloaded", timeout: request.navigationTimeoutMs || 30000 });
    await page.locator(request.successCheck).first().waitFor({ state: "visible", timeout: request.authTimeoutMs || 5000 });
    return { protocolVersion: PROTOCOL_VERSION, status: "valid" };
  } catch (error) {
    return { protocolVersion: PROTOCOL_VERSION, status: "expired", detail: error instanceof Error ? error.message : String(error) };
  } finally {
    await context.close();
  }
}

async function authStepScreenshot(page, request, index) {
  await fs.mkdir(request.outDir, { recursive: true });
  const outputPath = path.join(request.outDir, `step-${String(index + 1).padStart(2, "0")}.png`);
  await page.screenshot({ path: outputPath, fullPage: true, animations: "disabled", caret: "hide", mask: [page.locator('input[type="password"]')] });
  return outputPath;
}

async function runAuthStep(page, step, request) {
  const actions = ["fill", "click", "waitFor"].filter((key) => Object.hasOwn(step, key));
  if (actions.length !== 1) throw new Error("each auth step must contain exactly one action");
  const action = actions[0];
  if (action === "fill") {
    if (typeof step.fill !== "string" || typeof step.value !== "string") throw new Error("fill step requires selector and value");
    await page.locator(step.fill).fill(step.value, { timeout: request.authTimeoutMs || 5000 });
  } else if (action === "click") {
    if (typeof step.click !== "string") throw new Error("click step requires a selector");
    await page.locator(step.click).click({ timeout: request.authTimeoutMs || 5000 });
  } else if (step.waitFor.startsWith("/")) {
    await page.waitForURL((url) => url.pathname === step.waitFor, { timeout: request.authTimeoutMs || 5000 });
  } else {
    await page.locator(step.waitFor).first().waitFor({ state: "visible", timeout: request.authTimeoutMs || 5000 });
  }
  return action;
}

async function authLogin(browser, request) {
  const context = await browser.newContext({ serviceWorkers: "block" });
  await installNetworkBoundary(context, request);
  const page = await context.newPage();
  const evidence = [];
  try {
    await page.goto(request.url, { waitUntil: "domcontentloaded", timeout: request.navigationTimeoutMs || 30000 });
    for (let index = 0; index < request.steps.length; index += 1) {
      const step = request.steps[index];
      const action = ["fill", "click", "waitFor"].find((key) => Object.hasOwn(step, key)) || "invalid";
      try {
        await runAuthStep(page, step, request);
        evidence.push({ index: index + 1, action, status: "pass", path: await authStepScreenshot(page, request, index) });
      } catch (error) {
        evidence.push({ index: index + 1, action, status: "fail", path: await authStepScreenshot(page, request, index) });
        throw new Error(`authentication step ${index + 1} (${action}) failed: ${error instanceof Error ? error.message : String(error)}`);
      }
    }
    await page.locator(request.successCheck).first().waitFor({ state: "visible", timeout: request.authTimeoutMs || 5000 });
    await fs.mkdir(path.dirname(request.authState), { recursive: true });
    await context.storageState({ path: request.authState });
    await fs.chmod(request.authState, 0o600);
    return { protocolVersion: PROTOCOL_VERSION, status: "authenticated", statePath: request.authState, steps: evidence };
  } catch (error) {
    return { protocolVersion: PROTOCOL_VERSION, status: "failed", detail: error instanceof Error ? error.message : String(error), steps: evidence };
  } finally {
    await context.close();
  }
}

async function authManual(browser, request) {
  const context = await browser.newContext({ serviceWorkers: "block" });
  await installNetworkBoundary(context, request);
  const page = await context.newPage();
  try {
    await page.goto(request.url, { waitUntil: "domcontentloaded", timeout: request.navigationTimeoutMs || 30000 });
    process.stderr.write("VAL: complete authentication in the opened browser window.\n");
    await page.locator(request.successCheck).first().waitFor({ state: "visible", timeout: request.authTimeoutMs || 600000 });
    await fs.mkdir(request.outDir, { recursive: true });
    const evidence = path.join(request.outDir, "manual-success.png");
    await page.screenshot({ path: evidence, fullPage: true, mask: [page.locator('input[type="password"]')] });
    await fs.mkdir(path.dirname(request.authState), { recursive: true });
    await context.storageState({ path: request.authState });
    await fs.chmod(request.authState, 0o600);
    return { protocolVersion: PROTOCOL_VERSION, status: "authenticated", statePath: request.authState, steps: [{ action: "manual", status: "pass", path: evidence }] };
  } catch (error) {
    return { protocolVersion: PROTOCOL_VERSION, status: "failed", detail: error instanceof Error ? error.message : String(error), steps: [] };
  } finally {
    await context.close();
  }
}

async function main() {
  const request = await readRequest();
  const browser = await chromium.launch({ headless: request.operation !== "auth-manual" });
  try {
    if (request.operation === "auth-check") {
      process.stdout.write(`${JSON.stringify(await authCheck(browser, request))}\n`);
      return;
    }
    if (request.operation === "auth-login") {
      const result = await authLogin(browser, request);
      process.stdout.write(`${JSON.stringify(result)}\n`);
      if (result.status !== "authenticated") process.exitCode = 3;
      return;
    }
    if (request.operation === "auth-manual") {
      const result = await authManual(browser, request);
      process.stdout.write(`${JSON.stringify(result)}\n`);
      if (result.status !== "authenticated") process.exitCode = 3;
      return;
    }
    const shots = [];
    for (const shot of request.shots) shots.push(await capture(browser, request, shot));
    process.stdout.write(`${JSON.stringify({ protocolVersion: PROTOCOL_VERSION, shots })}\n`);
  } finally {
    await browser.close();
  }
}

main().catch((error) => fail(error instanceof Error ? error.message : String(error)));
