/* Run from tests/static: npm install && npm run test:modals.
 * Deliberately suppress CSS transition events; jsdom does not animate CSS.
 */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const root = process.env.MODAL_SOURCE_ROOT || path.resolve(__dirname, '../../..');

function fixture(t, transition = true) {
    const dom = new JSDOM('<!doctype html><html><body><main id="home"></main></body></html>', {
        runScripts: 'outside-only', pretendToBeVisual: true
    });
    t.after(() => dom.window.close());
    const w = dom.window;
    let now = 0, id = 0;
    const timers = new Map();
    w.setTimeout = (fn, delay = 0) => { timers.set(++id, { fn, at: now + delay }); return id; };
    w.clearTimeout = id => timers.delete(id);
    w.setInterval = (fn, delay) => { timers.set(++id, { fn, at: now + delay, repeat: delay }); return id; };
    w.clearInterval = w.clearTimeout;
    function tick(ms) {
        const until = now + ms;
        for (;;) {
            const next = [...timers].filter(([, x]) => x.at <= until).sort((a, b) => a[1].at - b[1].at)[0];
            if (!next) break;
            const [key, timer] = next;
            now = timer.at;
            if (timer.repeat) timer.at += timer.repeat;
            else timers.delete(key);
            timer.fn();
        }
        now = until;
    }
    for (const f of ['jquery/jquery.js', 'bootstrap/bootstrap-modal.js', 'bootstrap/bootstrap-modalmanager.js']) {
        w.eval(fs.readFileSync(path.join(root, 'src/octoprint/static/js/lib', f), 'utf8'));
    }
    const $ = w.jQuery;
    $.support.transition = transition ? {end: 'transitionend'} : false;
    function dialog(id = 'dialog') {
        return $(`<div id="${id}" class="modal fade" style="display:none"><button>OK</button></div>`).appendTo('#home');
    }
    const manager = () => $('body').data('modalmanager');
    function clean() {
        assert.equal($('.modal-scrollable').length, 0, 'no wrappers');
        assert.equal($('.modal-backdrop').length, 0, 'no backdrops');
        assert.equal(manager().backdropCount, 0);
        assert.equal(manager().stack.length, 0);
        assert.equal($('body').hasClass('modal-open'), false);
    }
    return {$, w, tick, dialog, manager, clean};
}

test('missing transition events cannot retain a closed modal', t => {
    const f = fixture(t), d = f.dialog();
    let shown = 0;
    d.on('shown', () => shown++).modal('show');
    assert.notEqual(d.css('display'), 'none', 'dialog appears without backdrop transition');
    f.tick(500);
    assert.equal(shown, 1);
    d.modal('hide'); f.tick(500); f.clean();
    assert.equal(d.parent().attr('id'), 'home');
});

test('rapid hide/show serializes teardown, ignores stale events and keeps latest dialog', t => {
    const f = fixture(t), d = f.dialog();
    d.modal('show');
    const old = d.data('modal'), backdrop = old.$backdrop;
    d.modal('hide'); d.modal('show'); f.tick(500);
    assert.equal(f.$('.modal-scrollable').length, 1);
    assert.equal(f.$('.modal-backdrop').length, 1);
    assert.equal(d.data('modal').isShown, true);
    backdrop.trigger('transitionend'); old.hideModal(); old.destroy();
    assert.equal(d.data('modal').isShown, true);
    d.modal('hide'); f.tick(500); f.clean();
});

test('hide/show/hide honours the final requested closed state', t => {
    const f = fixture(t), d = f.dialog();
    d.modal('show'); d.modal('hide'); d.modal('show'); d.modal('hide');
    f.tick(1000); f.clean();
});

test('200 cooling-like cycles and duplicate notifications do not accumulate layers', t => {
    const f = fixture(t), d = f.dialog();
    for (let i = 0; i < 200; i++) {
        d.modal({backdrop: 'static', keyboard: false}); d.modal('show');
        assert.equal(f.$('.modal-scrollable').length, 1);
        d.modal('hide'); d.modal('hide'); f.tick(500); f.clean();
    }
});

test('bubbled child transition cannot complete parent hide; handler fires once', t => {
    const f = fixture(t), d = f.dialog(); let hidden = 0;
    d.on('hidden', () => hidden++).modal('show'); d.modal('hide');
    d.find('button').trigger('transitionend');
    assert.equal(hidden, 0);
    d.trigger('transitionend'); f.tick(1000);
    assert.equal(hidden, 1); f.clean();
});

test('teardown never unbinds unrelated transition listeners', t => {
    const f = fixture(t), d = f.dialog(); let events = 0;
    d.on('transitionend.application', () => events++).modal('show'); d.modal('hide'); f.tick(500);
    d.trigger('transitionend'); assert.equal(events, 1); f.clean();
});

test('stacked active static dialog survives repeated destruction of another', t => {
    const f = fixture(t), a = f.dialog('a'), b = f.dialog('b');
    a.modal('show'); const old = a.data('modal');
    b.modal({backdrop: 'static', keyboard: false});
    a.modal('hide'); f.tick(500); old.destroy(); f.manager().destroyModal(old);
    assert.equal(f.manager().stack.length, 1);
    assert.equal(b.data('modal').isShown, true);
    assert.equal(f.$('.modal-backdrop').length, 1);
    assert.equal(f.$('body').hasClass('modal-open'), true);
    b.modal('hide'); f.tick(500); f.clean();
});

test('reconciler removes old orphan pairs and preserves hidden dialog bindings', t => {
    const f = fixture(t); f.$('body').modalmanager();
    const d = f.dialog(); let clicks = 0; d.find('button').on('click', () => clicks++);
    for (let i = 0; i < 3; i++) {
        const c = f.$('<div class="modal-scrollable"></div>').appendTo('body');
        f.$('<div class="modal-backdrop fade"></div>').appendTo('body');
        if (i === 2) d.appendTo(c);
    }
    f.tick(1000); f.clean();
    assert.equal(f.$('#dialog').length, 1); d.find('button').trigger('click'); assert.equal(clicks, 1);
});

test('reconciler preserves active dialogs and loading overlays while removing orphans', t => {
    const f = fixture(t), d = f.dialog(); d.modal({backdrop:'static'});
    f.$('<div class="modal-scrollable"></div><div class="modal-backdrop fade"></div>').appendTo('body');
    f.tick(1000);
    assert.equal(d.data('modal').isShown, true);
    assert.equal(f.$('.modal-backdrop').length, 1);
    f.manager().loading(); f.tick(1000);
    assert.equal(f.$('.modal-backdrop').length, 2);
    assert.ok(f.manager().$spinner[0].isConnected);
    f.manager().loading(); d.modal('hide'); f.tick(500); f.clean();
});

test('reconciler preserves an unknown visible dialog', t => {
    const f = fixture(t); f.$('body').modalmanager();
    f.$('<div class="modal-scrollable"><div class="modal in">External dialog</div></div><div class="modal-backdrop"></div>').appendTo('body');
    f.tick(1000);
    assert.equal(f.$('.modal-scrollable').length, 1);
    assert.equal(f.$('.modal-backdrop').length, 1);
});

test('externally detached active modal is cleaned up', t => {
    const f = fixture(t), d = f.dialog(); d.modal('show'); d.detach(); f.tick(1000); f.clean();
});

test('replacement retains exactly one backdrop then removes it on close', t => {
    const f = fixture(t), a = f.dialog('a'), b = f.dialog('b');
    a.modal('show'); b.modal({replace:true}); f.tick(1000);
    assert.equal(b.data('modal').isShown, true);
    assert.equal(f.$('.modal-backdrop').length, 1);
    assert.equal(f.$('.modal-scrollable').length, 1);
    b.modal('hide'); f.tick(500); f.clean();
});

test('no-transition browsers clean up synchronously', t => {
    const f = fixture(t, false), d = f.dialog(); d.modal('show'); d.modal('hide'); f.clean();
});

test('throwing hidden handler cannot strand layers', t => {
    const f = fixture(t), d = f.dialog();
    d.on('hidden', () => {throw new Error('application bug');}).modal('show'); d.modal('hide');
    assert.throws(() => f.tick(500), /application bug/); f.clean();
});

test('global loading overlay closes without transition events', t => {
    const f = fixture(t); f.$('body').modalmanager();
    f.manager().loading(); f.manager().loading(); f.clean();
});

test('cancelled hide preserves the active dialog', t => {
    const f = fixture(t), d = f.dialog(); d.modal('show');
    d.on('hide.veto', e => e.preventDefault()); d.modal('hide'); f.tick(1000);
    assert.equal(d.data('modal').isShown, true); assert.equal(f.$('.modal-backdrop').length, 1);
    d.off('.veto').modal('hide'); f.tick(500); f.clean();
});

test('a show cancelled after initialization must not allocate blocking layers', t => {
    const f = fixture(t), d = f.dialog(); d.modal({show:false});
    d.on('show.veto', e => e.preventDefault()); d.modal('show'); f.tick(1000);
    assert.equal(f.$('.modal-backdrop').length, 0);
    assert.equal(f.$('.modal-scrollable').length, 0);
});

test('hidden handler may reopen without duplicate instances or wrappers', t => {
    const f = fixture(t), d = f.dialog();
    d.on('hidden.reopen', () => d.modal('show')).modal('show');
    d.modal('hide'); f.tick(1000);
    assert.equal(d.data('modal').isShown, true); assert.equal(f.$('.modal-scrollable').length, 1);
    d.off('.reopen').modal('hide'); f.tick(500); f.clean();
});

test('throwing destroy listener cannot strand a completed hide', t => {
    const f = fixture(t), d = f.dialog();
    d.on('destroy.broken', () => {throw new Error('destroy listener');}).modal('show');
    d.modal('hide'); assert.throws(() => f.tick(500), /destroy listener/); f.clean();
});

test('a dialog externally hidden without calling the modal API is reconciled', t => {
    const f = fixture(t), d = f.dialog(); d.modal('show'); d.hide(); f.tick(1000); f.clean();
});
