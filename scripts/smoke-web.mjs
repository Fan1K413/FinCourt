import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const baseUrl = process.argv[2] ?? "http://127.0.0.1:8000";
const browserPath = process.env.FINCOURT_BROWSER ??
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
assert(existsSync(browserPath), "Set FINCOURT_BROWSER to a Chromium browser executable");
const profile = mkdtempSync(fileURLToPath(new URL("../var/browser-", import.meta.url)));
const browser = spawn(browserPath, [
  "--headless=new", "--no-first-run", "--disable-background-networking", "--window-size=1440,900",
  "--remote-debugging-port=0", "--user-data-dir=" + profile, baseUrl,
], { stdio: "ignore", windowsHide: true });

const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(check, message) {
  for (let attempt = 0; attempt < 100; attempt++) {
    const value = await check();
    if (value) return value;
    await delay(100);
  }
  throw new Error(message);
}

let socket;
try {
  const portPath = join(profile, "DevToolsActivePort");
  await until(() => existsSync(portPath), "Browser DevTools did not start");
  const [port] = readFileSync(portPath, "utf8").split("\n");
  const page = await until(async () => {
    const urls = await fetch("http://127.0.0.1:" + port + "/json/list").then(response => response.json());
    return urls.find(item => item.type === "page" && item.url.startsWith(baseUrl));
  }, "FinCourt page not found");
  socket = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });

  let id = 0;
  async function call(method, params = {}) {
    const requestId = ++id;
    const payload = await new Promise(resolve => {
      const listener = event => {
        const packet = JSON.parse(event.data);
        if (packet.id === requestId) {
          socket.removeEventListener("message", listener);
          resolve(packet);
        }
      };
      socket.addEventListener("message", listener);
      socket.send(JSON.stringify({ id: requestId, method, params }));
    });
    assert(!payload.error && !payload.result.exceptionDetails, method);
    return payload.result;
  }
  async function evaluate(expression) {
    return (await call("Runtime.evaluate", { expression, returnByValue: true })).result.value;
  }

  const width = Number(process.env.FINCOURT_SMOKE_WIDTH ?? 0);
  if (width) {
    await call("Emulation.setDeviceMetricsOverride", {
      width, height: 844, deviceScaleFactor: 1, mobile: false,
    });
  }

  const click = selector => until(() => evaluate(
    "(() => { const button = document.querySelector(" + JSON.stringify(selector)
    + "); if (!button?.offsetWidth || button.disabled) return false; button.click(); return true; })()",
  ), "Button not ready: " + selector);
  const clickText = text => until(() => evaluate(
    "(() => { const button = [...document.querySelectorAll('button')].find(b => b.offsetWidth && !b.disabled && b.textContent.includes("
    + JSON.stringify(text) + ")); if (!button) return false; button.click(); return true; })()",
  ), "Button not ready: " + text);
  const tab = name => click('nav button[aria-controls="' + name + '"]');
  const decision = value => until(() => evaluate(
    "document.querySelector('#reviews .verdict strong')?.dataset.decision === " + JSON.stringify(value),
  ), "Decision was not rendered: " + value);
  const fitsViewport = async () => {
    assert(await evaluate("document.documentElement.scrollWidth <= innerWidth"), "Page overflows horizontally");
  };
  const capture = async target => {
    if (!target) return;
    await until(() => evaluate("document.getAnimations().every(a => a.playState !== 'running')"),
      "Page motion did not settle");
    const screenshot = await call("Page.captureScreenshot", { format: "png" });
    writeFileSync(target, Buffer.from(screenshot.data, "base64"));
  };
  await click('[data-review-id="sample-c5-initial"]');
  await decision("SUPPORTED");
  await fitsViewport();
  if (width && width <= 760) {
    await until(() => evaluate(
      "(() => { const row = document.querySelector('#reviews .rail [aria-pressed=true]');"
      + "const rail = row.closest('ul').getBoundingClientRect(); const card = row.getBoundingClientRect();"
      + "return card.left >= rail.left - 1 && card.right <= rail.right + 1; })()",
    ), "The selected mobile card is clipped");
  }
  await capture(process.argv[3]);
  await click('[data-citation-id="sample-revenue-original"]');
  assert.match(await until(() => evaluate("document.querySelector('#library mark')?.textContent"),
    "Citation did not open a highlighted page"), /12,000.00/);
  await fitsViewport();

  const pdf = process.env.FINCOURT_SMOKE_PDF;
  if (pdf) {
    await clickText("导入 PDF");
    const root = (await call("DOM.getDocument")).root.nodeId;
    const { nodeId } = await call("DOM.querySelector", { nodeId: root, selector: '#library input[type="file"]' });
    await call("DOM.setFileInputFiles", { nodeId, files: [pdf] });
    await evaluate(
      "(() => { const fields = document.querySelector('#library form').elements;"
      + "fields.title.value = '浏览器上传样例'; fields.id.value = 'browser-smoke-' + Date.now();"
      + "fields.subject.value = 'TEST'; fields.date.value = '2025-02-28';"
      + "fields.namedItem('source').value = 'SIMULATED'; })()",
    );
    await click("#library form button[type=submit]");
    await until(() => evaluate("document.querySelector('#library form [role=status]')?.textContent.includes('已导入')"),
      "The PDF form did not import the document");
  }

  await tab("impact");
  await clickText("导入模拟更正");
  await until(() => evaluate("document.querySelector('#impact .amount-slip.changed dd')?.textContent.includes('0.92 亿元')"),
    "Correction impact was not rendered");
  await fitsViewport();
  await capture(process.env.FINCOURT_SMOKE_IMPACT_SCREENSHOT);
  await click('[data-impact-claim="sample-c5"]');
  await decision("REFUTED");
  await click('[data-review-id="sample-c5-initial"]');
  await decision("SUPPORTED");
  await call("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }] });
  assert.equal(await evaluate("getComputedStyle(document.querySelector('#reviews')).animationName"), "none");
  console.log(
    "Browser workflow passed: cited page, correction, saved history, " + (width || "desktop") + " layout and reduced motion",
  );
  const admin = process.env.FINCOURT_SMOKE_ADMIN;
  if (admin) {
    await tab("knowledge");
    await clickText("验证补丁");
    await until(() => evaluate("document.querySelector('#knowledge [data-patch-status=READY_FOR_REVIEW] .agreement-facts')?.offsetWidth"),
      "Agreement preview not rendered");
    // Use the native setter so React sees the test input event.
    await evaluate("(() => { const input = document.querySelector('input[type=password]'); "
      + "Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, "
      + JSON.stringify(admin) + "); input.dispatchEvent(new Event('input', {bubbles:true})); })()");
    await clickText("批准补丁");
    await clickText("发布知识版本");
    await until(() => evaluate("document.querySelector('#knowledge [data-projection-document]')?.offsetWidth"),
      "Published agreement projection not rendered");
    await evaluate("(() => { const select = document.querySelector('select[aria-label=回滚版本]'); "
      + "select.value = 'fincourt-disclosures-v1'; select.dispatchEvent(new Event('change', {bubbles:true})); })()");
    await click("#knowledge .version-actions button");
    await until(() => evaluate("document.querySelector('#knowledge [data-pending-documents]')?.dataset.pendingDocuments === '1'"),
      "Rollback did not restore pending agreement status");
    console.log("Browser workflow passed: validated, approved, published and rolled back knowledge bundle");
  }
} finally {
  socket?.close();
  browser.kill();
}
