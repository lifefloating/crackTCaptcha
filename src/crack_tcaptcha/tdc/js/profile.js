/**
 * profile.js — Fingerprint profile for jsdom.
 *
 * Captured from a real Chrome 147 on macOS (Apple M4) via CDP on
 * https://t.captcha.qq.com/template/drag_ele.html, 2026-04-19.
 *
 * Why this profile specifically: using the same OS + chip combo as the
 * developer's actual browser minimizes the surface area where a server-side
 * consistency check (UA vs platform vs WebGL renderer) could flag us.
 */

"use strict";

const fs = require("fs");
const path = require("path");

function loadCanvasFp() {
  try {
    return fs.readFileSync(path.join(__dirname, "data", "canvas_fp.txt"), "utf8").trim();
  } catch (_) {
    return "";
  }
}

module.exports = {
  ua:
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " +
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
  platform: "MacIntel",
  vendor: "Google Inc.",
  language: "zh-CN",
  languages: ["zh-CN", "zh"],
  hardwareConcurrency: 10,
  deviceMemory: 16,
  maxTouchPoints: 0,
  timezoneOffset: -480, // UTC+8
  timezoneName: "Asia/Shanghai",
  chromeVersion: 147,

  screen: {
    width: 1920,
    height: 1080,
    availWidth: 1920,
    availHeight: 981,
    availLeft: 0,
    availTop: 25,
    colorDepth: 24,
    pixelDepth: 24,
    orientation: { type: "landscape-primary", angle: 0 },
  },

  viewport: {
    innerWidth: 1920,
    innerHeight: 842,
    outerWidth: 1920,
    outerHeight: 981,
    devicePixelRatio: 1,
  },

  // Real values from the dev box. Note UA claims Intel but ANGLE unmasks to Apple M4
  // — this matches exactly what real Chrome on an M-series Mac reports, and diverging
  // here is precisely the kind of inconsistency bot detectors watch for.
  webgl: {
    vendor: "WebKit",
    renderer: "WebKit WebGL",
    version: "WebGL 1.0 (OpenGL ES 2.0 Chromium)",
    shadingLanguageVersion: "WebGL GLSL ES 1.0 (OpenGL ES GLSL ES 1.0 Chromium)",
    unmaskedVendor: "Google Inc. (Apple)",
    unmaskedRenderer:
      "ANGLE (Apple, ANGLE Metal Renderer: Apple M4, Unspecified Version)",
    extensions: [
      "ANGLE_instanced_arrays",
      "EXT_blend_minmax",
      "EXT_clip_control",
      "EXT_color_buffer_half_float",
      "EXT_depth_clamp",
      "EXT_disjoint_timer_query",
      "EXT_float_blend",
      "EXT_frag_depth",
      "EXT_polygon_offset_clamp",
      "EXT_shader_texture_lod",
      "EXT_texture_compression_bptc",
      "EXT_texture_compression_rgtc",
      "EXT_texture_filter_anisotropic",
      "EXT_texture_mirror_clamp_to_edge",
      "EXT_sRGB",
      "KHR_parallel_shader_compile",
      "OES_element_index_uint",
      "OES_fbo_render_mipmap",
      "OES_standard_derivatives",
      "OES_texture_float",
      "OES_texture_float_linear",
      "OES_texture_half_float",
      "OES_texture_half_float_linear",
      "OES_vertex_array_object",
      "WEBGL_blend_func_extended",
      "WEBGL_color_buffer_float",
      "WEBGL_compressed_texture_astc",
      "WEBGL_compressed_texture_etc",
      "WEBGL_compressed_texture_etc1",
      "WEBGL_compressed_texture_pvrtc",
      "WEBGL_compressed_texture_s3tc",
      "WEBGL_compressed_texture_s3tc_srgb",
      "WEBGL_debug_renderer_info",
      "WEBGL_debug_shaders",
      "WEBGL_depth_texture",
      "WEBGL_draw_buffers",
      "WEBGL_lose_context",
      "WEBGL_multi_draw",
      "WEBGL_polygon_mode",
    ],
  },

  // Real Chrome 147: 5 plugins, each with 2 pdf mimeTypes; top-level
  // navigator.mimeTypes has just 2 entries (deduped across plugins).
  plugins: [
    {
      name: "PDF Viewer",
      filename: "internal-pdf-viewer",
      description: "Portable Document Format",
      mimes: [
        { type: "application/pdf", suffixes: "pdf" },
        { type: "text/pdf", suffixes: "pdf" },
      ],
    },
    {
      name: "Chrome PDF Viewer",
      filename: "internal-pdf-viewer",
      description: "Portable Document Format",
      mimes: [
        { type: "application/pdf", suffixes: "pdf" },
        { type: "text/pdf", suffixes: "pdf" },
      ],
    },
    {
      name: "Chromium PDF Viewer",
      filename: "internal-pdf-viewer",
      description: "Portable Document Format",
      mimes: [
        { type: "application/pdf", suffixes: "pdf" },
        { type: "text/pdf", suffixes: "pdf" },
      ],
    },
    {
      name: "Microsoft Edge PDF Viewer",
      filename: "internal-pdf-viewer",
      description: "Portable Document Format",
      mimes: [
        { type: "application/pdf", suffixes: "pdf" },
        { type: "text/pdf", suffixes: "pdf" },
      ],
    },
    {
      name: "WebKit built-in PDF",
      filename: "internal-pdf-viewer",
      description: "Portable Document Format",
      mimes: [
        { type: "application/pdf", suffixes: "pdf" },
        { type: "text/pdf", suffixes: "pdf" },
      ],
    },
  ],

  mimeTypes: [
    { type: "application/pdf", suffixes: "pdf" },
    { type: "text/pdf", suffixes: "pdf" },
  ],

  connection: {
    effectiveType: "4g",
    rtt: 0,
    downlink: 9.5,
    saveData: false,
  },

  // Real Chrome leaves window.chrome.runtime unavailable (or as `undefined`)
  // on top-level pages unless an extension is active. Faking {} is what got
  // our previous profile caught.
  chromeGlobal: {
    hasRuntime: false,
    hasLoadTimes: true,
    hasCsi: true,
    hasApp: true,
    appIsInstalled: false,
  },

  // Canvas fingerprint captured from the same real browser with the exact
  // same painting sequence we reproduce in env_patch.js patchCanvas().
  canvasFingerprint: loadCanvasFp(),

  // Audio fingerprint probe: Chrome 147 on this box returns
  // hash≈257.83, sampleAt5000≈-0.3777. We store a slight range so Math.random
  // style jitter in tdc.js comparisons doesn't misfire.
  audio: {
    hashSample: 257.83077973115724,
    sampleAt5000: -0.3776991367340088,
  },
};
