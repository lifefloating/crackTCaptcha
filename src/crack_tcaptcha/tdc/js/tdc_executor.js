/**
 * tdc_executor.js — jsdom bridge for TCaptcha TDC.js
 *
 * Reads JSON from stdin:
 *   { tdc_url, ua, trajectory: [{x,y,t}...], total_ms }
 *
 * Writes JSON to stdout:
 *   { collect, eks, tlg }
 */

const { JSDOM } = require("jsdom");
const https = require("https");
const http = require("http");
const zlib = require("zlib");

function fetchScript(url) {
  return new Promise((resolve, reject) => {
    const client = url.startsWith("https") ? https : http;
    client
      .get(url, { headers: { "Accept-Encoding": "gzip, deflate" } }, (res) => {
        let stream = res;
        const encoding = res.headers["content-encoding"];
        if (encoding === "gzip") {
          stream = res.pipe(zlib.createGunzip());
        } else if (encoding === "deflate") {
          stream = res.pipe(zlib.createInflate());
        }
        let body = "";
        stream.on("data", (chunk) => (body += chunk));
        stream.on("end", () => resolve(body));
        stream.on("error", reject);
      })
      .on("error", reject);
  });
}

async function main() {
  // Read stdin
  let input = "";
  for await (const chunk of process.stdin) {
    input += chunk;
  }
  const { tdc_url, ua, trajectory, total_ms } = JSON.parse(input);

  // Fetch tdc.js source
  const fullUrl = tdc_url.startsWith("http")
    ? tdc_url
    : `https://t.captcha.qcloud.com${tdc_url}`;
  const tdcSource = await fetchScript(fullUrl);

  // Create jsdom with browser-like environment
  const dom = new JSDOM(`<!DOCTYPE html><html><body></body></html>`, {
    url: "https://t.captcha.qcloud.com/",
    userAgent: ua,
    pretendToBeVisual: true,
    runScripts: "dangerously",
  });

  const win = dom.window;

  // Patch browser APIs that tdc.js expects
  Object.defineProperty(win, "screen", {
    value: {
      width: 1920,
      height: 1080,
      availWidth: 1920,
      availHeight: 1040,
      colorDepth: 24,
      pixelDepth: 24,
    },
    writable: true,
  });
  win.innerWidth = 1920;
  win.innerHeight = 1080;
  win.outerWidth = 1920;
  win.outerHeight = 1080;
  win.devicePixelRatio = 1;

  // Remove webdriver flag
  Object.defineProperty(win.navigator, "webdriver", {
    get: () => false,
  });

  // Inject tdc.js
  const script = win.document.createElement("script");
  script.textContent = tdcSource;
  win.document.head.appendChild(script);

  // Wait for TDC initialization
  await new Promise((r) => setTimeout(r, 500));

  if (!win.TDC) {
    throw new Error("TDC global not found after script injection");
  }

  // Feed trajectory data via setData
  const slideData = {};
  for (const pt of trajectory) {
    slideData[pt.t] = `${pt.x},${pt.y}`;
  }
  win.TDC.setData(slideData);

  // Collect results
  const collect = win.TDC.getData();
  const info = win.TDC.getInfo();

  const result = {
    collect: collect || "",
    eks: typeof info === "string" ? info : JSON.stringify(info || ""),
    tlg: total_ms || 0,
  };

  process.stdout.write(JSON.stringify(result));
  dom.window.close();
}

main().catch((err) => {
  process.stderr.write(String(err));
  process.exit(1);
});
