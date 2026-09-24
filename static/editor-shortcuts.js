/* One handler owns both browser-default suppression and editor actions. */
(() => {
    function actionFor(e, context = {}) {
        if (e.isComposing || context.modal || (e.ctrlKey && e.metaKey)) return null;
        const key = e.key.toLowerCase();
        const primary = (e.ctrlKey || e.metaKey) && !(e.ctrlKey && e.metaKey);
        if (e.altKey) {
            if (!e.ctrlKey && !e.metaKey && !e.shiftKey) {
                if (key === 'arrowdown') return 'next';
                if (key === 'arrowup') return 'previous';
            }
            return null;
        }
        if (!primary) return key === 'escape' && !e.shiftKey ? 'close' : null;
        if (context.text && ['z', 'y'].includes(key)) return null; // native text history
        if (key === 'z') return e.shiftKey ? 'redo' : 'undo';
        if (key === 'y' && !e.shiftKey) return 'redo';
        if (key === 'enter' && context.card) {
            if (e.shiftKey) return context.add ? 'add' : null;
            return context.split ? 'split' : null;
        }
        if (!e.shiftKey && ['s', 'f', 'e'].includes(key)) {
            return {s: 'save', f: 'find', e: 'export'}[key];
        }
        // Ctrl-only bindings avoid macOS Cmd+M (minimise window).
        if (e.ctrlKey && !e.metaKey) {
            if (key === 'm' && context.card) return e.shiftKey ? 'merge_previous' : 'merge_next';
            if (key === 'd' && !e.shiftKey && context.card) return 'delete';
            if (key === ' ' && !e.shiftKey) return 'play';
        }
        return null;
    }

    function handleKey(e, doc, emit) {
        const field = doc.querySelector('.caption-text-input textarea');
        const target = doc.activeElement;
        const context = {
            modal: !!doc.querySelector('.q-dialog, .q-menu'),
            text: !!target && (target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)),
            card: !!field,
            split: !!doc.querySelector('.caption-split-cursor'),
            add: !!doc.querySelector('.caption-add'),
        };
        const action = actionFor(e, context);
        if (!action || (action === 'close' && !context.card)) return;
        e.preventDefault();
        e.stopPropagation();
        if (e.repeat) return;
        if (action === 'play') {
            const video = doc.querySelector('video');
            if (video) {
                if (video.paused) video.play().catch(() => {});
                else video.pause();
            }
            return;
        }
        const payload = {action};
        if (field) {
            payload.caption_index = Number(field.closest('[data-caption-index]').getAttribute('data-caption-index'));
            payload.text = field.value;
            payload.cursor = [...field.value.slice(0, field.selectionStart)].length;
        }
        // Timing fields commit on blur; queue their update before the action.
        if (target?.closest?.('.caption-time-input')) target.blur();
        emit('editor-shortcut', payload);
    }
    if (typeof module !== 'undefined') module.exports = {actionFor, handleKey};
    if (typeof window !== 'undefined') {
        if (window.__editorShortcutHandler) window.removeEventListener('keydown', window.__editorShortcutHandler, true);
        window.__editorShortcutHandler = e => handleKey(e, document, window.emitEvent);
        window.addEventListener('keydown', window.__editorShortcutHandler, true);
    }
})();
