/**
 * tdc_executor.js — Thin orchestrator for TCaptcha TDC.js inside jsdom.
 *
 * Responsibilities:
 *   1. Build a jsdom window at the correct origin.
 *   2. Apply env_patch (navigator/screen/chrome/canvas/webgl/audio/fonts).
 *   3. Fetch tdc.js source with realistic headers and inject it.
 *   4. Wait for window.TDC to initialize.
 *   5. Apply cd[] invariants.
 *   6. Dispatch trajectory (slider via setData; click/multi_click via real events).
 *   7. Read TDC.getData / getInfo and emit JSON to stdout.
 *
 * Input (stdin JSON):
 *   { tdc_url, ua, trajectory: {kind, points:[{x,y,t}], total_ms}, debug }
 *
 * Output (stdout JSON):
 *   { collect, eks, tokenid, tlg }
 */

"use strict";

const { JSDOM } = require("jsdom");
const https = require("https");
const http = require("http");
const zlib = require("zlib");

const envPatch = require("./env_patch");
const invariants = require("./invariants");
const eventDispatch = require("./event_dispatch");

const ORIGIN = "https://t.captcha.qq.com";
const LANDING = "https://t.captcha.qq.com/template/drag_ele.html";
const REFERER = "https://captcha.gtimg.com/";

function log(msg) {
  // Only visible when TCAPTCHA_TDC_DEBUG=1 (Python side logs stderr to debug).
  process.stderr.write(`[tdc_executor] ${msg}\n`);
}

function fetchScript(url, ua) {
  return new Promise((resolve, reject) => {
    const client = url.startsWith("https") ? https : http;
    const req = client.get(
      url,
      {
        headers: {
          "User-Agent": ua,
          Accept: "*/*",
          "Accept-Encoding": "gzip, deflate",
          "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
          Referer: REFERER,
          Origin: ORIGIN,
        },
      },
      (res) => {
        let stream = res;
        const encoding = res.headers["content-encoding"];
        if (encoding === "gzip") stream = res.pipe(zlib.createGunzip());
        else if (encoding === "deflate") stream = res.pipe(zlib.createInflate());

        let body = "";
        stream.on("data", (chunk) => (body += chunk));
        stream.on("end", () => resolve(body));
        stream.on("error", reject);
      }
    );
    req.on("error", reject);
    req.setTimeout(30000, () => {
      req.destroy(new Error("tdc.js fetch timed out"));
    });
  });
}

function resolveTdcUrl(tdc_url) {
  if (!tdc_url) return null;
  if (tdc_url.startsWith("http")) return tdc_url;
  return ORIGIN + (tdc_url.startsWith("/") ? tdc_url : "/" + tdc_url);
}

async function waitForTDC(win, timeoutMs = 5000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (win.TDC) return true;
    await new Promise((r) => setTimeout(r, 50));
  }
  return false;
}

async function main() {
  // Read stdin
  let input = "";
  for await (const chunk of process.stdin) input += chunk;
  const { tdc_url, ua, trajectory, debug } = JSON.parse(input);

  const fullUrl = resolveTdcUrl(tdc_url);
  if (!fullUrl) throw new Error("tdc_url is empty");

  if (debug) log(`fetching tdc.js: ${fullUrl}`);
  const tdcSource = await fetchScript(fullUrl, ua);
  if (debug) log(`tdc.js fetched: ${tdcSource.length} bytes`);

  const dom = new JSDOM(`<!DOCTYPE html><html><head></head><body></body></html>`, {
    url: LANDING,
    referrer: REFERER,
    userAgent: ua,
    pretendToBeVisual: true,
    runScripts: "dangerously",
  });
  const win = dom.window;

  envPatch.apply(win);
  if (debug) log("env_patch applied");

  // Inject tdc.js
  const script = win.document.createElement("script");
  script.textContent = tdcSource;
  win.document.head.appendChild(script);

  const ready = await waitForTDC(win, 5000);
  if (!ready) {
    const keys = Object.keys(win).filter((k) => /tdc|tencent|chaos/i.test(k));
    throw new Error("TDC global not found. Candidate keys: " + JSON.stringify(keys));
  }
  if (debug) log("TDC global ready");

  // cd[] overrides (P4 will fill this with real mappings).
  invariants.apply(win);

  // Feed trajectory
  await eventDispatch.dispatch(win, trajectory);
  if (debug) log(`dispatched trajectory kind=${trajectory.kind} points=${trajectory.points.length}`);

  // Let tdc.js flush any queued handlers before we read state.
  await new Promise((r) => setTimeout(r, 250));

  let collect = "";
  let info = "";
  let tokenid = "";
  try {
    collect =
      typeof win.TDC.getData === "function" ? win.TDC.getData(true) : "";
  } catch (e) {
    log(`TDC.getData error: ${e.message || e}`);
  }
  try {
    const raw = typeof win.TDC.getInfo === "function" ? win.TDC.getInfo() : "";
    if (raw && typeof raw === "object") {
      info = raw.info || raw.eks || raw.data || JSON.stringify(raw);
      tokenid = raw.tokenid || "";
    } else {
      info = String(raw || "");
    }
  } catch (e) {
    log(`TDC.getInfo error: ${e.message || e}`);
  }

  if (debug) {
    log(`collect_len=${String(collect).length} eks_len=${String(info).length} tokenid=${tokenid}`);
    // Dump TDC internals that we can inspect (collect is already URL-encoded
    // base64 by TDC; raw TDC state is more useful for comparison with real Chrome).
    try {
      const keys = Object.keys(win.TDC || {});
      log(`TDC keys: ${JSON.stringify(keys)}`);
      const internals = {};
      for (const k of keys) {
        try {
          const v = win.TDC[k];
          if (typeof v === "function") internals[k] = "<fn>";
          else if (v === null || v === undefined) internals[k] = String(v);
          else if (typeof v === "object") internals[k] = `<object len=${Array.isArray(v) ? v.length : Object.keys(v).length}>`;
          else internals[k] = String(v).slice(0, 120);
        } catch (e) { internals[k] = `<err:${e.message}>`; }
      }
      log(`TDC internals: ${JSON.stringify(internals)}`);
      // First 120 chars of collect (URL-decoded)
      try {
        const decoded = decodeURIComponent(String(collect));
        log(`collect_decoded_head: ${decoded.slice(0, 120)}`);
      } catch (_) {}
    } catch (e) { log(`TDC internals dump failed: ${e.message}`); }
  }

  const result = {
    collect: String(collect || ""),
    eks: String(info || ""),
    tokenid: String(tokenid || ""),
    tlg: String(collect || "").length,
  };

  process.stdout.write(JSON.stringify(result));
  dom.window.close();
}

main().catch((err) => {
  process.stderr.write(String(err && err.stack ? err.stack : err));
  process.exit(1);
});
