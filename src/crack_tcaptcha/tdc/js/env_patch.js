/**
 * env_patch.js — Patch a jsdom window to look like a real Chrome 147 macOS browser.
 *
 * All values sourced from profile.js (which was captured from a real browser via CDP).
 *
 * Deliberately NOT patched (must remain natural):
 *   Date / Math.random  — tdc.js mixes timestamps; fixing them looks fake.
 */

"use strict";

const profile = require("./profile");

function defineGetter(obj, prop, getter, configurable = true) {
  try {
    Object.defineProperty(obj, prop, {
      get: getter,
      configurable,
      enumerable: true,
    });
  } catch (_) {
    // some props are non-configurable in jsdom; swallow and move on
  }
}

function defineValue(obj, prop, value) {
  try {
    Object.defineProperty(obj, prop, {
      value,
      writable: false,
      configurable: true,
      enumerable: true,
    });
  } catch (_) {}
}

function buildMimeType(def) {
  return {
    type: def.type,
    suffixes: def.suffixes,
    description: def.description || "",
    enabledPlugin: null,
  };
}

function patchNavigator(win) {
  const nav = win.navigator;

  defineGetter(nav, "userAgent", () => profile.ua);
  defineGetter(nav, "appVersion", () => profile.ua.replace(/^Mozilla\//, ""));
  defineGetter(nav, "appName", () => "Netscape");
  defineGetter(nav, "appCodeName", () => "Mozilla");
  defineGetter(nav, "product", () => "Gecko");
  defineGetter(nav, "productSub", () => "20030107");
  defineGetter(nav, "platform", () => profile.platform);
  defineGetter(nav, "language", () => profile.language);
  defineGetter(nav, "languages", () => profile.languages);
  defineGetter(nav, "hardwareConcurrency", () => profile.hardwareConcurrency);
  defineGetter(nav, "deviceMemory", () => profile.deviceMemory);
  defineGetter(nav, "maxTouchPoints", () => profile.maxTouchPoints);
  defineGetter(nav, "vendor", () => profile.vendor);
  defineGetter(nav, "vendorSub", () => "");
  defineGetter(nav, "cookieEnabled", () => true);
  defineGetter(nav, "onLine", () => true);
  defineGetter(nav, "doNotTrack", () => null);
  defineGetter(nav, "webdriver", () => false);
  defineGetter(nav, "pdfViewerEnabled", () => true);

  // Build MimeTypeArray-like: top-level navigator.mimeTypes has deduped mimes.
  const topLevelMimes = profile.mimeTypes.map(buildMimeType);
  const mimeArray = [...topLevelMimes];
  mimeArray.item = (i) => mimeArray[i] || null;
  mimeArray.namedItem = (n) => mimeArray.find((m) => m.type === n) || null;
  defineGetter(nav, "mimeTypes", () => mimeArray);

  // Build PluginArray-like: each plugin is an iterable of its own MimeTypes.
  const plugins = profile.plugins.map((p) => {
    const plugMimes = p.mimes.map((m) => buildMimeType({ ...m, description: p.description }));
    const pluginObj = {
      name: p.name,
      filename: p.filename,
      description: p.description,
      length: plugMimes.length,
    };
    // Index-access: plugin[0], plugin[1]
    plugMimes.forEach((m, idx) => {
      pluginObj[idx] = m;
      m.enabledPlugin = pluginObj;
    });
    pluginObj.item = (i) => plugMimes[i] || null;
    pluginObj.namedItem = (n) => plugMimes.find((m) => m.type === n) || null;
    // Make it iterable like real PluginArray element does
    pluginObj[Symbol.iterator] = function* () {
      for (const m of plugMimes) yield m;
    };
    return pluginObj;
  });
  plugins.forEach((p, idx) => { /* PluginArray is also index-accessible */ });
  const pluginArray = [...plugins];
  pluginArray.item = (i) => pluginArray[i] || null;
  pluginArray.namedItem = (n) => pluginArray.find((p) => p.name === n) || null;
  pluginArray.refresh = () => {};
  defineGetter(nav, "plugins", () => pluginArray);

  const conn = { ...profile.connection };
  defineGetter(nav, "connection", () => conn);

  // userActivation stub (Chrome-only, real presence matters)
  defineGetter(nav, "userActivation", () => ({ isActive: true, hasBeenActive: true }));
}

function patchScreenAndWindow(win) {
  const s = profile.screen;
  const v = profile.viewport;

  try {
    Object.defineProperty(win, "screen", {
      value: {
        width: s.width,
        height: s.height,
        availWidth: s.availWidth,
        availHeight: s.availHeight,
        availLeft: s.availLeft,
        availTop: s.availTop,
        colorDepth: s.colorDepth,
        pixelDepth: s.pixelDepth,
        orientation: s.orientation,
      },
      configurable: true,
    });
  } catch (_) {}

  try { win.innerWidth = v.innerWidth; } catch (_) {}
  try { win.innerHeight = v.innerHeight; } catch (_) {}
  try { win.outerWidth = v.outerWidth; } catch (_) {}
  try { win.outerHeight = v.outerHeight; } catch (_) {}
  try { win.devicePixelRatio = v.devicePixelRatio; } catch (_) {}
}

function patchChromeGlobal(win) {
  const cfg = profile.chromeGlobal;
  // window.chrome exists on Chrome; jsdom has no such global.
  const chromeObj = {};
  if (cfg.hasLoadTimes) {
    chromeObj.loadTimes = function () {
      const now = Date.now() / 1000;
      return {
        commitLoadTime: now - 0.5,
        connectionInfo: "h2",
        finishDocumentLoadTime: now - 0.3,
        finishLoadTime: now - 0.1,
        firstPaintAfterLoadTime: 0,
        firstPaintTime: now - 0.4,
        navigationType: "Other",
        npnNegotiatedProtocol: "h2",
        requestTime: now - 0.6,
        startLoadTime: now - 0.5,
        wasAlternateProtocolAvailable: false,
        wasFetchedViaSpdy: true,
        wasNpnNegotiated: true,
      };
    };
  }
  if (cfg.hasCsi) {
    chromeObj.csi = function () {
      return {
        onloadT: Date.now(),
        pageT: 1000,
        startE: Date.now() - 1000,
        tran: 15,
      };
    };
  }
  if (cfg.hasApp) {
    chromeObj.app = {
      isInstalled: cfg.appIsInstalled,
      InstallState: { DISABLED: "disabled", INSTALLED: "installed", NOT_INSTALLED: "not_installed" },
      RunningState: { CANNOT_RUN: "cannot_run", READY_TO_RUN: "ready_to_run", RUNNING: "running" },
    };
  }
  // Key: real Chrome sans extensions has NO chrome.runtime on t.captcha origin.
  // We deliberately omit it.
  if (cfg.hasRuntime) {
    chromeObj.runtime = {};
  }
  win.chrome = chromeObj;
}

function patchCanvas(win) {
  const proto = win.HTMLCanvasElement && win.HTMLCanvasElement.prototype;
  if (!proto) return;

  const fixed = profile.canvasFingerprint;

  proto.toDataURL = function () {
    // Return the real-browser captured base64. jsdom has no 2d painting, so
    // the alternative (default blank-canvas dataURL) is an obvious bot signal.
    return fixed || "data:image/png;base64,";
  };

  const origGetContext = proto.getContext;
  proto.getContext = function (type, ...rest) {
    if (type === "2d") return make2dContext(this);
    if (type === "webgl" || type === "experimental-webgl" || type === "webgl2") {
      return makeWebGLContext();
    }
    return origGetContext ? origGetContext.apply(this, [type, ...rest]) : null;
  };
}

function make2dContext(canvas) {
  const noop = () => {};
  return {
    canvas,
    fillStyle: "#000",
    strokeStyle: "#000",
    lineWidth: 1,
    globalAlpha: 1,
    globalCompositeOperation: "source-over",
    textBaseline: "alphabetic",
    textAlign: "start",
    font: "10px sans-serif",
    fillRect: noop,
    strokeRect: noop,
    clearRect: noop,
    fillText: noop,
    strokeText: noop,
    measureText: (t) => ({ width: (t || "").length * 6, actualBoundingBoxLeft: 0, actualBoundingBoxRight: 0, actualBoundingBoxAscent: 10, actualBoundingBoxDescent: 2 }),
    beginPath: noop,
    closePath: noop,
    moveTo: noop,
    lineTo: noop,
    bezierCurveTo: noop,
    quadraticCurveTo: noop,
    stroke: noop,
    fill: noop,
    arc: noop,
    arcTo: noop,
    rect: noop,
    save: noop,
    restore: noop,
    translate: noop,
    rotate: noop,
    scale: noop,
    setTransform: noop,
    transform: noop,
    resetTransform: noop,
    getImageData: (x, y, w, h) => ({
      width: w,
      height: h,
      data: new Uint8ClampedArray(w * h * 4),
    }),
    putImageData: noop,
    drawImage: noop,
    createLinearGradient: () => ({ addColorStop: noop }),
    createRadialGradient: () => ({ addColorStop: noop }),
    createPattern: () => ({}),
    isPointInPath: () => false,
    isPointInStroke: () => false,
  };
}

function makeWebGLContext() {
  const p = profile.webgl;
  const UNMASKED_VENDOR_WEBGL = 0x9245;
  const UNMASKED_RENDERER_WEBGL = 0x9246;
  const VENDOR = 0x1f00;
  const RENDERER = 0x1f01;
  const VERSION = 0x1f02;
  const SHADING_LANGUAGE_VERSION = 0x8b8c;
  const MAX_TEXTURE_SIZE = 0x0d33;
  const MAX_VIEWPORT_DIMS = 0x0d3a;
  const ALIASED_LINE_WIDTH_RANGE = 0x846e;
  const ALIASED_POINT_SIZE_RANGE = 0x846d;

  return {
    getParameter: (param) => {
      switch (param) {
        case VENDOR: return p.vendor;
        case RENDERER: return p.renderer;
        case VERSION: return p.version;
        case SHADING_LANGUAGE_VERSION: return p.shadingLanguageVersion;
        case UNMASKED_VENDOR_WEBGL: return p.unmaskedVendor;
        case UNMASKED_RENDERER_WEBGL: return p.unmaskedRenderer;
        case MAX_TEXTURE_SIZE: return 16384;
        case MAX_VIEWPORT_DIMS: return new Int32Array([16384, 16384]);
        case ALIASED_LINE_WIDTH_RANGE: return new Float32Array([1, 1]);
        case ALIASED_POINT_SIZE_RANGE: return new Float32Array([1, 511]);
        default: return 0;
      }
    },
    getExtension: (name) => {
      if (name === "WEBGL_debug_renderer_info") {
        return { UNMASKED_VENDOR_WEBGL, UNMASKED_RENDERER_WEBGL };
      }
      return p.extensions.includes(name) ? {} : null;
    },
    getSupportedExtensions: () => [...p.extensions],
    getShaderPrecisionFormat: () => ({ rangeMin: 127, rangeMax: 127, precision: 23 }),
    createShader: () => ({}),
    createProgram: () => ({}),
    createBuffer: () => ({}),
    createTexture: () => ({}),
    VENDOR, RENDERER, VERSION, SHADING_LANGUAGE_VERSION,
  };
}

function patchAudio(win) {
  if (win.OfflineAudioContext) return;
  win.OfflineAudioContext = function (channels, length, sampleRate) {
    const buf = new Float32Array(length || 44100);
    // Populate with a deterministic waveform similar to real Chrome output.
    // tdc.js audio FP is a windowed sum over middle samples.
    for (let i = 0; i < buf.length; i++) {
      buf[i] = Math.sin(i * 0.01) * 0.5;
    }
    return {
      sampleRate: sampleRate || 44100,
      length: length || 44100,
      currentTime: 0,
      destination: { channelCount: channels || 1 },
      createOscillator: () => ({
        type: "sine",
        frequency: { value: 10000, setValueAtTime: () => {} },
        connect: () => {},
        start: () => {},
        stop: () => {},
      }),
      createDynamicsCompressor: () => {
        const param = (v) => ({ value: v, setValueAtTime: () => {} });
        return {
          threshold: param(-50),
          knee: param(40),
          ratio: param(12),
          attack: param(0),
          release: param(0.25),
          reduction: { value: 0 },
          connect: (n) => n,
        };
      },
      startRendering: () =>
        Promise.resolve({
          length: buf.length,
          sampleRate: sampleRate || 44100,
          numberOfChannels: 1,
          getChannelData: () => buf,
          copyFromChannel: (dst) => dst.set(buf.subarray(0, dst.length)),
        }),
      oncomplete: null,
    };
  };
  if (!win.AudioContext) win.AudioContext = win.OfflineAudioContext;
}

function patchFonts(win) {
  if (!win.document.fonts) {
    const set = [];
    win.document.fonts = {
      check: () => true,
      load: () => Promise.resolve([]),
      ready: Promise.resolve({}),
      values: () => set[Symbol.iterator](),
      entries: () => set[Symbol.iterator](),
      forEach: (cb) => set.forEach(cb),
      [Symbol.iterator]: () => set[Symbol.iterator](),
      size: 0,
    };
  }
}

function patchPerformance(win) {
  if (!win.performance) return;
  if (!win.performance.timing) {
    const nav = Date.now() - 1500;
    win.performance.timing = {
      navigationStart: nav,
      fetchStart: nav + 10,
      domainLookupStart: nav + 15,
      domainLookupEnd: nav + 25,
      connectStart: nav + 25,
      secureConnectionStart: nav + 35,
      connectEnd: nav + 60,
      requestStart: nav + 65,
      responseStart: nav + 120,
      responseEnd: nav + 180,
      domLoading: nav + 190,
      domInteractive: nav + 800,
      domContentLoadedEventStart: nav + 810,
      domContentLoadedEventEnd: nav + 820,
      domComplete: nav + 1400,
      loadEventStart: nav + 1410,
      loadEventEnd: nav + 1420,
      unloadEventStart: 0,
      unloadEventEnd: 0,
      redirectStart: 0,
      redirectEnd: 0,
    };
  }
  if (!win.performance.timeOrigin) {
    try {
      Object.defineProperty(win.performance, "timeOrigin", {
        value: Date.now() - 2000,
        configurable: true,
      });
    } catch (_) {}
  }
}

function patchLayout(win) {
  // jsdom's Element.getBoundingClientRect returns {0,0,0,0}. tdc.js may
  // validate click coordinates against the captcha iframe body's rect.
  // Make document.body/documentElement report a plausible rect.
  const Element = win.Element;
  if (!Element || !Element.prototype) return;
  const origGBCR = Element.prototype.getBoundingClientRect;
  Element.prototype.getBoundingClientRect = function () {
    // body: cover the full viewport so events at (x,y) fall inside
    if (this === win.document.body || this === win.document.documentElement) {
      return {
        x: 0, y: 0,
        top: 0, left: 0,
        right: profile.viewport.innerWidth,
        bottom: profile.viewport.innerHeight,
        width: profile.viewport.innerWidth,
        height: profile.viewport.innerHeight,
        toJSON() { return this; },
      };
    }
    const r = origGBCR ? origGBCR.call(this) : null;
    if (r && (r.width || r.height)) return r;
    return { x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, toJSON() { return this; } };
  };
}

function apply(win) {
  patchNavigator(win);
  patchScreenAndWindow(win);
  patchChromeGlobal(win);
  patchCanvas(win);
  patchAudio(win);
  patchFonts(win);
  patchPerformance(win);
  patchLayout(win);
}

module.exports = { apply };
