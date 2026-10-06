const test = require('node:test');
const assert = require('node:assert/strict');
const {watch} = require('../app/static/session.js');

function session({hidden = false, data = true} = {}) {
  const doc = new EventTarget(), win = new EventTarget(), timers = new Map();
  let wall = 1000000, ticks = 0, sequence = 0, clears = 0;
  doc.hidden = hidden;
  win.Date = {now: () => wall};
  win.performance = {now: () => ticks};
  win.setTimeout = (fn, delay) => { const id = ++sequence; timers.set(id, {fn, at: ticks + delay}); return id; };
  win.clearTimeout = (id) => timers.delete(id);
  const stop = watch({window: win, document: doc, hasData: () => data, clear: () => { clears++; data = false; }});
  return {
    get clears() { return clears; },
    advance(ms, runTimers = true) {
      wall += ms; ticks += ms;
      if (runTimers) {
        for (const [id, timer] of [...timers]) {
          if (timer.at <= ticks && timers.delete(id)) timer.fn();
        }
      }
    },
    visibility(hidden) { doc.hidden = hidden; doc.dispatchEvent(new Event('visibilitychange')); },
    event(name) { win.dispatchEvent(new Event(name)); },
    setData(value) { data = value; },
    moveClock(ms) { wall += ms; },
    stop,
  };
}

test('visible tab retains files even without activity', () => {
  const s = session(); s.advance(600000); s.event('focus'); assert.equal(s.clears, 0);
});
test('one continuous minute hidden clears once at the deadline', () => {
  const s = session(); s.visibility(true); s.advance(59999); assert.equal(s.clears, 0);
  s.advance(1); assert.equal(s.clears, 1); s.advance(120000); s.event('pageshow'); assert.equal(s.clears, 1);
});
test('returning sooner cancels the deadline and the next absence starts fresh', () => {
  const s = session(); s.visibility(true); s.advance(40000); s.visibility(false); s.advance(100000);
  assert.equal(s.clears, 0); s.visibility(true); s.advance(59999); assert.equal(s.clears, 0);
  s.advance(1); assert.equal(s.clears, 1);
});
test('return checks elapsed time when the background timer was suspended', () => {
  const s = session(); s.visibility(true); s.advance(90000, false); assert.equal(s.clears, 0);
  s.visibility(false); assert.equal(s.clears, 1); s.event('focus'); s.event('pageshow'); assert.equal(s.clears, 1);
});
test('restoring a cached page after a minute clears its retained session', () => {
  const s = session(); s.event('pagehide'); s.advance(90000, false); s.event('pageshow'); assert.equal(s.clears, 1);
});
test('hidden pageshow and repeated hidden events do not extend the deadline', () => {
  const s = session(); s.visibility(true); s.advance(40000); s.event('pageshow'); s.visibility(true);
  s.advance(20000); assert.equal(s.clears, 1);
});
test('empty background sessions do not repeatedly reload', () => {
  const s = session({data: false}); s.visibility(true); s.advance(60000); s.advance(120000); assert.equal(s.clears, 0);
  s.visibility(false); s.setData(true); s.visibility(true); s.advance(60000); assert.equal(s.clears, 1);
});
test('a page opened in a hidden tab starts its deadline', () => {
  const s = session({hidden: true}); s.advance(60000); assert.equal(s.clears, 1);
});
test('moving the wall clock backwards does not delay cleanup', () => {
  const s = session(); s.visibility(true); s.moveClock(-3600000); s.advance(60000); assert.equal(s.clears, 1);
});
test('stopping the guard removes timers and lifecycle listeners', () => {
  const s = session(); s.visibility(true); s.stop(); s.advance(60000); s.visibility(false);
  s.event('pagehide'); s.advance(60000); s.event('pageshow'); assert.equal(s.clears, 0);
});
