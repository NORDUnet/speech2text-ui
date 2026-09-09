/* A bounded preview uses media time, never a wall-clock playback timer. */
(() => {
    window.__stopConfidenceReplay?.();
    let cleanup = null;
    let replayVideo = null;
    let generation = 0;
    window.__stopConfidenceReplay = (pauseVideo = false) => {
        generation++;
        // End review must also stop playback after a seek cancelled the window.
        const activeVideo = pauseVideo
            ? document.getElementById('subtitle-editor-video')
            : (cleanup ? replayVideo : null);
        if (cleanup) cleanup();
        cleanup = null;
        activeVideo?.pause();
        replayVideo = null;
    };
    window.__reviewConfidenceWord = async ({word_id, start, end, replay}) => {
        window.__stopConfidenceReplay();
        const run = generation;
        const root = document.getElementById('subtitle-editor-captions');
        const word = [...(root?.querySelectorAll('[data-word-id]') || [])]
            .find(el => el.dataset.wordId === word_id);
        word?.scrollIntoView({block: 'center', behavior: 'smooth'});
        const video = document.getElementById('subtitle-editor-video');
        if (!video || !Number.isFinite(start) || !Number.isFinite(end) || end <= start) return 'unavailable';
        video.pause();
        if (Number.isFinite(video.duration) && start >= video.duration) return 'unavailable';
        const from = replay ? Math.max(0, start - 2) : start;
        const until = Math.min(end + 2, Number.isFinite(video.duration) ? video.duration : Infinity);
        video.currentTime = from;
        if (!replay) return 'ready';
        let frame = null;
        let initialSeek = true;
        const clear = () => {
            if (frame !== null) cancelAnimationFrame(frame);
            video.removeEventListener('timeupdate', check);
            video.removeEventListener('pause', stop);
            video.removeEventListener('ended', stop);
            video.removeEventListener('seeking', seek);
            video.removeEventListener('seeked', sought);
        };
        const stop = () => { clear(); if (cleanup === stop) cleanup = null; };
        const sought = () => { initialSeek = false; };
        const seek = () => {
            if (!initialSeek || Math.abs(video.currentTime - from) > 0.1) stop();
        };
        const check = () => {
            if (video.currentTime >= until) { stop(); video.pause(); }
        };
        const tick = () => { check(); if (cleanup === stop) frame = requestAnimationFrame(tick); };
        video.addEventListener('timeupdate', check);
        video.addEventListener('pause', stop);
        video.addEventListener('ended', stop);
        video.addEventListener('seeking', seek);
        video.addEventListener('seeked', sought);
        cleanup = stop;
        replayVideo = video;
        let timer;
        try {
            await Promise.race([
                video.play(),
                new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('Playback timed out')), 4000); }),
            ]);
            if (run !== generation) return 'cancelled';
            if (cleanup === stop) frame = requestAnimationFrame(tick);
            return 'playing';
        } catch (_) {
            if (run !== generation) return 'cancelled';
            stop(); video.pause();
            return 'blocked';
        } finally { clearTimeout(timer); }
    };
    window.addEventListener('pagehide', () => window.__stopConfidenceReplay());
})();
