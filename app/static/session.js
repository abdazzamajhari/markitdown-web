/* Clear retained documents after one continuous minute away from the tab. */
((root) => {
  'use strict';
  const hiddenLimit = 60000;

  function watch({window: win, document: doc, hasData, clear}) {
    let hiddenAt = null, hiddenTick = null, timer = null, cleared = false;
    const cancelTimer = () => { if (timer !== null) win.clearTimeout(timer); timer = null; };
    // The wall clock includes browser/OS suspension; the monotonic clock also
    // protects the deadline when the system clock moves backwards.
    const elapsed = () => Math.max(win.Date.now() - hiddenAt, win.performance.now() - hiddenTick);
    function expire() {
      cancelTimer();
      if (hiddenAt === null || cleared) return false;
      if (elapsed() < hiddenLimit) return false;
      cleared = true;
      if (hasData()) clear();
      return true;
    }
    function schedule() {
      cancelTimer();
      if (hiddenAt === null || cleared) return;
      timer = win.setTimeout(() => {
        if (!expire() && doc.hidden) schedule();
      }, Math.max(0, hiddenLimit - elapsed()));
    }
    function leave() {
      if (hiddenAt === null) { hiddenAt = win.Date.now(); hiddenTick = win.performance.now(); cleared = false; }
      schedule();
    }
    function checkVisibility() {
      if (doc.hidden) { leave(); return; }
      // Check before resetting: frozen background timers may never have run.
      expire();
      cancelTimer(); hiddenAt = hiddenTick = null; cleared = false;
    }
    doc.addEventListener('visibilitychange', checkVisibility);
    win.addEventListener('pagehide', leave);
    win.addEventListener('pageshow', checkVisibility);
    win.addEventListener('focus', checkVisibility);
    checkVisibility();
    return () => {
      cancelTimer();
      doc.removeEventListener('visibilitychange', checkVisibility);
      win.removeEventListener('pagehide', leave);
      win.removeEventListener('pageshow', checkVisibility);
      win.removeEventListener('focus', checkVisibility);
    };
  }

  if (typeof module === 'object' && module.exports) module.exports = {watch};
  else root.PrivasiGuardSession = {watch};
})(typeof window === 'undefined' ? null : window);
