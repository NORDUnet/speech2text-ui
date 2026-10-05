/* Karaoke follows media time; animation frames only schedule repainting. */
(() => {
    window.__disposeKaraoke?.();
    let video, root, cache = null, last = null, frame = null;
    let disposed = false;
    const events = ['play', 'pause', 'seeking', 'seeked', 'timeupdate',
                    'loadedmetadata', 'ended', 'emptied'];
    const changes = new MutationObserver(() => { cache = null; schedule(); });
    const mounting = new MutationObserver(attach);

    function schedule() {
        if (!disposed && frame === null) frame = requestAnimationFrame(paint);
    }

    function paint() {
        frame = null;
        if (disposed || !video?.isConnected || !root?.isConnected) return;
        if (cache === null) {
            cache = [...root.querySelectorAll('.w-seek[data-s][data-e]')]
                .map(el => ({el, s: Number(el.dataset.s), e: Number(el.dataset.e)}))
                .filter(w => Number.isFinite(w.s) && Number.isFinite(w.e) && w.e > w.s)
                .sort((a, b) => a.s - b.s);
        }
        const t = video.currentTime;
        let lo = 0, hi = cache.length - 1, index = -1;
        while (lo <= hi) {
            const mid = (lo + hi) >> 1;
            if (cache[mid].s <= t) { index = mid; lo = mid + 1; }
            else hi = mid - 1;
        }
        // Preserve the existing short grace period between words.
        const current = video.readyState > 0 && index >= 0 && t < cache[index].e + 0.3
            ? cache[index].el : null;
        if (current !== last) {
            last?.classList.remove('w-current');
            current?.classList.add('w-current');
            last = current;
        }
        if (!video.paused && !video.ended) schedule();
    }

    function attach() {
        if (disposed || video) return;
        const candidate = document.getElementById('subtitle-editor-video');
        const captions = document.getElementById('subtitle-editor-captions');
        if (!candidate || !captions) return;
        video = candidate;
        root = captions;
        mounting.disconnect();
        // Class changes from highlighting do not invalidate our own cache.
        changes.observe(root, {subtree: true, childList: true, attributes: true,
            attributeFilter: ['data-s', 'data-e']});
        events.forEach(name => video.addEventListener(name, schedule));
        schedule();
    }

    function dispose() {
        disposed = true;
        mounting.disconnect();
        changes.disconnect();
        if (frame !== null) cancelAnimationFrame(frame);
        events.forEach(name => video?.removeEventListener(name, schedule));
        document.removeEventListener('visibilitychange', schedule);
        window.removeEventListener('pagehide', dispose);
        last?.classList.remove('w-current');
    }
    window.__disposeKaraoke = dispose;
    window.addEventListener('pagehide', dispose);
    document.addEventListener('visibilitychange', schedule);
    // Observe the page only until the editor and video have mounted.
    mounting.observe(document.documentElement, {subtree: true, childList: true});
    attach();
})();
