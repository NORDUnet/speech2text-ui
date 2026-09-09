const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

(async () => {
    const listeners = new Map();
    const video = {
        currentTime: 0, duration: 100, paused: true,
        pause() { this.paused = true; this.emit('pause'); },
        play() { this.paused = false; return Promise.resolve(); },
        addEventListener(e, fn) { if (!listeners.has(e)) listeners.set(e, new Set()); listeners.get(e).add(fn); },
        removeEventListener(e, fn) { listeners.get(e)?.delete(fn); },
        emit(e) { [...(listeners.get(e) || [])].forEach(fn => fn()); },
    };
    const window = {addEventListener() {}};
    const context = {window, document: {getElementById: id => id === 'subtitle-editor-video' ? video : null},
        requestAnimationFrame: () => 1, cancelAnimationFrame() {}, setTimeout, clearTimeout};
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/confidence-review.js'), 'utf8'), context);
    await window.__reviewConfidenceWord({word_id:'a', start:10, end:11, replay:true});
    assert.equal(video.paused, false);
    window.__stopConfidenceReplay(true);
    assert.equal(video.paused, true, 'End review pauses an active replay');

    await window.__reviewConfidenceWord({word_id:'a', start:10, end:11, replay:true});
    video.currentTime = 30;
    video.emit('seeking'); // Cancels the replay boundary, but ordinary playback continues.
    assert.equal(video.paused, false);
    window.__stopConfidenceReplay(true);
    assert.equal(video.paused, true, 'End review pauses even after replay listeners were cleared');
    assert.equal(listeners.get('timeupdate').size, 0);

    video.paused = false;
    window.__stopConfidenceReplay(true);
    assert.equal(video.paused, true, 'Repeated End review also stops regular playback');
    console.log('PASS: End review pauses active, cancelled, and ordinary playback');
})().catch(error => {console.error(error); process.exitCode = 1;});
