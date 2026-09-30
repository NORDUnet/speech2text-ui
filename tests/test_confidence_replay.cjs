const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

(async () => {
    const listeners = new Map();
    const video = {
        currentTime: 0, duration: 100, paused: true,
        pause() {
            if (this.paused) return;
            this.paused = true;
            // Media events are queued, not dispatched inside pause().
            setTimeout(() => this.emit('pause'), 0);
        },
        play() { this.paused = false; return Promise.resolve(); },
        addEventListener(e, fn) { if (!listeners.has(e)) listeners.set(e, new Set()); listeners.get(e).add(fn); },
        removeEventListener(e, fn) { listeners.get(e)?.delete(fn); },
        emit(e) { [...(listeners.get(e) || [])].forEach(fn => fn()); },
    };
    const window = {addEventListener() {}};
    const context = {window, document: {getElementById: id => id === 'subtitle-editor-video' ? video : null},
        requestAnimationFrame: () => 1, cancelAnimationFrame() {}, setTimeout, clearTimeout};
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/confidence-review.js'), 'utf8'), context);
    // Listen while already playing: the pause used to restart playback queues
    // an event that arrives after the new replay's listeners are installed.
    video.paused = false;
    await window.__reviewConfidenceWord({word_id:'a', start:10, end:11, replay:true});
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.equal(video.paused, false, 'Listen starts playback');
    video.currentTime = 12.9;
    video.emit('timeupdate');
    assert.equal(video.paused, false, 'Listen plays through the context');
    video.currentTime = 13;
    video.emit('timeupdate');
    assert.equal(video.paused, true, 'Listen stops at the boundary when started during playback');
    await new Promise(resolve => setTimeout(resolve, 0));
    console.log('PASS: Listen during playback survives queued pause events and stops at its boundary');

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

    // Moving the confidence threshold slider only filters the flagged words.
    // It abandons the replay window so its end boundary cannot pause the
    // video later, and leaves a video the viewer is watching alone.
    video.paused = true;
    video.currentTime = 0;
    await window.__reviewConfidenceWord({word_id: 'a', start: 10, end: 11, replay: true});
    assert.equal(video.paused, false);
    const playhead = video.currentTime;
    window.__releaseConfidenceReplay();
    assert.equal(video.paused, false, 'Threshold change leaves a running video running');
    assert.equal(video.currentTime, playhead, 'Threshold change does not seek');
    assert.equal(listeners.get('timeupdate').size, 0, 'Replay boundary is detached');

    // Play past the boundary the abandoned replay would have stopped at.
    video.currentTime = 13;
    video.emit('timeupdate');
    assert.equal(video.paused, false, 'An abandoned replay cannot pause the video later');

    window.__stopConfidenceReplay(true);
    assert.equal(video.paused, true, 'End review still stops playback after a release');
    console.log('PASS: Threshold filtering releases the replay without pausing or seeking');

    await window.__reviewConfidenceWord({word_id:'a', start:10, end:11, replay:true});
    video.pause();
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.equal(listeners.get('timeupdate').size, 0, 'A real pause still cancels the replay boundary');

})().catch(error => {console.error(error); process.exitCode = 1;});
