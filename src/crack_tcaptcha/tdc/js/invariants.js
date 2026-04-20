/**
 * invariants.js — Overwrite cd[] fields that the server rejects if drifted.
 *
 * P4 will port the concrete mapping from flashtcaptcha/tdc/invariants.py.
 * For P1 we keep this as a no-op so the skeleton compiles and the slider path
 * is not disturbed.
 */

"use strict";

function apply(win) {
  // Intentionally empty at P1. The real mapping lives in P4.
  //
  // Shape will be along the lines of:
  //   const tdc = win.TDC;
  //   if (tdc && tdc._data_) {
  //     tdc._data_[INDEX].field = EXPECTED_VALUE;
  //     ...
  //   }
  void win;
}

module.exports = { apply };
