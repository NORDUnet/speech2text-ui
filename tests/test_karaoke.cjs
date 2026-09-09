const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const script = fs.readFileSync(require('node:path').join(__dirname, '../static/karaoke.js'), 'utf8');
const frames = new Map();
let nextFrame = 0;
const observers = [];
function target(data = {}) {
    return Object.assign({listeners: new Map(),
        addEventListener(k, f) { this.listeners.set(k, f); },
        removeEventListener(k) { this.listeners.delete(k); },
        fire(k) { this.listeners.get(k)?.(); }}, data);
}
function word(s, e) {
    const classes = new Set();
    return {dataset: {s: String(s), e: String(e)}, classes,
        classList: {add: c => classes.add(c), remove: c => classes.delete(c)}};
}
let words = [word(0, .1), word(.1, .2), word(.2, .3)];
const root = {isConnected: true, querySelectorAll: () => words};
const video = target({isConnected: true, currentTime: 0, paused: true, ended: false, readyState: 4});
const document = target({documentElement: {}, getElementById: id => id.endsWith('video') ? video : root});
const window = target();
const context = {window, document, Number,
    MutationObserver: class { constructor(f) { this.f = f; observers.push(this); }
        observe(root) { this.root = root; } disconnect() { this.root = null; } },
    requestAnimationFrame(f) { frames.set(++nextFrame, f); return nextFrame; },
    cancelAnimationFrame(id) { frames.delete(id); }};
function tick() { const pending = [...frames.values()]; frames.clear(); pending.forEach(f => f()); }
vm.runInNewContext(script, context);
tick();
assert(words[0].classes.has('w-current'));
assert.equal(frames.size, 0, 'paused does not spin');
video.paused = false; video.fire('play');
video.currentTime = .15; tick();
assert(words[1].classes.has('w-current'), 'short word updates without timeupdate');
video.currentTime = .25; tick();
assert(words[2].classes.has('w-current'));
assert(!words[1].classes.has('w-current'));
video.paused = true; video.fire('pause'); tick();
assert.equal(frames.size, 0);
video.currentTime = .05; video.fire('seeked'); tick();
assert(words[0].classes.has('w-current'), 'paused seek updates');
const old = words[0]; words = [word(0, .1)];
observers[0].f(); tick();
assert(words[0].classes.has('w-current'), 'paused rerender updates');
assert(!old.classes.has('w-current'));
assert.equal(observers[0].root, root, 'observer scoped to captions');
assert.equal(observers[1].root, null, 'mount observer disconnected');
video.currentTime = 5; video.fire('seeked'); tick();
assert(!words[0].classes.has('w-current'), 'clear during silence');
video.paused = false; video.fire('play'); tick();
window.__disposeKaraoke();
assert.equal(frames.size, 0);
assert.equal(video.listeners.size, 0);
console.log('PASS: short words, pause, seek, rerender, scope, silence and cleanup');
