/**
 * event_dispatch.js — Feed trajectory data to tdc.js via the appropriate mechanism.
 *
 * Routes by trajectory.kind:
 *   slider      → TDC.setData({t: "x,y"})               (preserves slider regression)
 *   click       → mousemove × N + mousedown/up/click    (icon_click)
 *   multi_click → mousemove × N + mousedown/up/click    (image_select, single region)
 *
 * Why use new win.MouseEvent(...) instead of the global MouseEvent:
 *   tdc.js often does `e instanceof MouseEvent` from the same window; events
 *   constructed in Node's global scope fail that check.
 */

"use strict";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function fireMouse(win, type, x, y, extra = {}) {
  const evt = new win.MouseEvent(type, {
    bubbles: true,
    cancelable: true,
    view: win,
    button: 0,
    buttons: type === "mousedown" ? 1 : 0,
    clientX: x,
    clientY: y,
    screenX: x,
    screenY: y,
    ...extra,
  });
  // document-level dispatch — tdc.js's mInit attaches to document (and window).
  try {
    win.document.dispatchEvent(evt);
  } catch (_) {}
  try {
    win.dispatchEvent(evt);
  } catch (_) {}
}

async function dispatchSlider(win, trajectory) {
  // Preserve current slider behavior: feed trajectory via TDC.setData.
  // Do NOT emit real mouse events here — changing this path risks slider regression.
  if (!win.TDC || typeof win.TDC.setData !== "function") {
    return;
  }
  const slideData = {};
  for (const pt of trajectory.points) {
    slideData[pt.t] = `${pt.x},${pt.y}`;
  }
  win.TDC.setData(slideData);
}

async function _playMouseTrajectory(win, points) {
  if (!points.length) return;
  let prevT = points[0].t;
  for (const pt of points) {
    const gap = Math.max(pt.t - prevT, 0);
    if (gap > 0) await sleep(Math.min(gap, 120));
    fireMouse(win, "mousemove", pt.x, pt.y);
    prevT = pt.t;
  }
}

async function _clickAtLast(win, last) {
  if (!last) return;
  await sleep(40 + Math.floor(Math.random() * 40)); // 40–80ms settle
  fireMouse(win, "mousedown", last.x, last.y);
  await sleep(40 + Math.floor(Math.random() * 40));
  fireMouse(win, "mouseup", last.x, last.y);
  fireMouse(win, "click", last.x, last.y);
}

async function dispatchClick(win, trajectory) {
  await _ambientPage(win);
  const points = trajectory.points || [];
  await _playMouseTrajectory(win, points);
  await _clickAtLast(win, points[points.length - 1]);
}

async function dispatchMultiClick(win, trajectory) {
  // image_select: trajectory shape is drift + approach + target;
  // last point is the single region to click (data_type=DynAnswerType_UC).
  await _ambientPage(win);
  const points = trajectory.points || [];
  await _playMouseTrajectory(win, points);
  await _clickAtLast(win, points[points.length - 1]);
}

// Simulate ambient page activity a real user would produce before interacting:
// focus, idle mouse moves spread over the viewport, scroll tick. tdc.js cd
// modules sample these continuously; a trajectory with only 3 points looks
// suspiciously sparse.
async function _ambientPage(win) {
  try {
    const doc = win.document;
    // focus / visibility signal
    try { doc.dispatchEvent(new win.Event("visibilitychange", { bubbles: true })); } catch (_) {}
    try { win.dispatchEvent(new win.Event("focus")); } catch (_) {}
    try { win.dispatchEvent(new win.Event("pageshow")); } catch (_) {}
  } catch (_) {}
  // 20 random mousemoves over ~400ms — reading the captcha before clicking.
  const N = 20;
  const baseX = 400, baseY = 250, spread = 250;
  for (let i = 0; i < N; i++) {
    const x = baseX + Math.floor((Math.random() - 0.5) * spread * 2);
    const y = baseY + Math.floor((Math.random() - 0.5) * spread);
    fireMouse(win, "mousemove", x, y);
    await sleep(15 + Math.floor(Math.random() * 20));
  }
}

async function dispatch(win, trajectory) {
  switch (trajectory.kind) {
    case "slider":
      return dispatchSlider(win, trajectory);
    case "click":
      return dispatchClick(win, trajectory);
    case "multi_click":
      return dispatchMultiClick(win, trajectory);
    default:
      // Unknown kind → fall back to slider behavior (safer default).
      return dispatchSlider(win, trajectory);
  }
}

module.exports = { dispatch };
