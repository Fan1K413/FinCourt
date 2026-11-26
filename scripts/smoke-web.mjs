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

  const clickText = text => evaluate(
    "[...document.querySelectorAll('button')].find(b => b.offsetWidth && b.textContent.includes("
    + JSON.stringify(text) + ")).click()",
  );
  await until(() => evaluate("document.querySelector('.verdict')?.textContent.includes('有证据支持')"),
    "Initial rolling review was not rendered");
  if (process.argv[3]) {
    const image = await call("Page.captureScreenshot", { format: "png" });
    writeFileSync(process.argv[3], Buffer.from(image.data, "base64"));
  }
  await clickText("支持 · 星桥样例公司 2024 年度业绩快报 · 第 1 页");
  assert.match(await until(() => evaluate("document.querySelector('mark')?.textContent"),
    "Citation did not open a highlighted page"), /12,000.00/);
  await clickText("观点审查");
  await clickText("变化影响");
  await clickText("导入模拟更正");
  await until(() => evaluate("document.querySelector('main').innerText.includes('0.92 亿元')"),
    "Correction impact was not rendered");
  await clickText("跟踪星桥样例公司");
  await until(() => evaluate("document.querySelector('.verdict')?.textContent.includes('被适用证据反驳')"),
    "Corrected rolling review was not rendered");
  await clickText("2025-03-01 · 有证据支持");
  assert.equal(await evaluate("document.querySelector('.verdict strong').textContent"), "有证据支持");
  console.log("Browser workflow passed: cited page, correction import, impacted review, saved history");
  const admin = process.env.FINCOURT_SMOKE_ADMIN;
  if (admin) {
    const ready = text => until(() => evaluate(
      "[...document.querySelectorAll('button')].some(b => b.offsetWidth && !b.disabled && b.textContent === "
      + JSON.stringify(text) + ")",
    ), "Button not ready: " + text);
    await clickText("知识版本");
    await ready("验证补丁");
    await clickText("验证补丁");
    await until(() => evaluate("document.querySelector('main').innerText.includes('候选版本可保存条件协议')"),
      "Agreement preview not rendered");
    // Use the native setter so React sees the test input event.
    await evaluate("(() => { const input = document.querySelector('input[type=password]'); "
      + "Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, "
      + JSON.stringify(admin) + "); input.dispatchEvent(new Event('input', {bubbles:true})); })()");
    await ready("批准补丁");
    await clickText("批准补丁");
    await ready("发布知识版本");
    await clickText("发布知识版本");
    await until(() => evaluate("document.querySelector('main').innerText.includes('当前协议投影')"),
      "Published agreement projection not rendered");
    await evaluate("(() => { const select = document.querySelector('select[aria-label=回滚版本]'); "
      + "select.value = 'fincourt-disclosures-v1'; select.dispatchEvent(new Event('change', {bubbles:true})); })()");
    await ready("回滚");
    await clickText("回滚");
    await until(() => evaluate("document.querySelector('main').innerText.includes('待处理协议 1 份')"),
      "Rollback did not restore pending agreement status");
    console.log("Browser workflow passed: validated, approved, published and rolled back knowledge bundle");
  }
} finally {
  socket?.close();
  browser.kill();
}
