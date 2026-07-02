// Whisper Dart — client controller (runs once via demo.load).
//
//   • Meetings list: select (click / ↑↓ keys), kebab menu (Tags / Rename /
//     Delete), inline rename, client-side search + column sort.
//   • Row actions are stashed in window.__wd_cmd and dispatched by clicking a
//     hidden Gradio button (#cmd-trigger); its `js` reads the global back as the
//     handler input. Gradio 6 ignores programmatic Textbox edits but fires
//     button clicks reliably, so this is the robust bridge.
//   • After Python re-renders the list HTML, a MutationObserver re-applies the
//     current search / sort / selection.
//   • Sidebar resize handle.
//
// All interactions use document-level delegation, so they survive the list
// being replaced wholesale on every server round-trip.
() => {
    if (window.__wdInit) return;          // idempotent — attach listeners once
    window.__wdInit = true;

    const MT = { selected: '', search: '', sortKey: 'date', sortDir: 'desc' };
    let menuEl = null;

    // ── Command bridge ───────────────────────────────────────────────────────
    function sendCommand(obj) {
        window.__wd_cmd = JSON.stringify(obj);
        const btn = document.getElementById('cmd-trigger');
        if (btn) btn.click();
        else console.warn('[whisper-dart] cmd-trigger button not found');
    }

    // ── Helpers ──────────────────────────────────────────────────────────────
    function rowEl(id) {                    // no CSS.escape — it mangles uuids
        return Array.prototype.find.call(
            document.querySelectorAll('.mt-row'), r => r.getAttribute('data-id') === id) || null;
    }
    function rowTags(id) {
        const row = rowEl(id);
        if (!row) return [];
        try { return JSON.parse(row.getAttribute('data-tags') || '[]'); } catch (e) { return []; }
    }
    function allTags() {
        const c = document.getElementById('meetings');
        if (!c) return [];
        try { return JSON.parse(c.getAttribute('data-alltags') || '[]'); } catch (e) { return []; }
    }
    function escapeHtml(s) {
        return String(s).replace(/[&<>"']/g, c => (
            { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    }
    function visibleRows() {
        return Array.prototype.filter.call(
            document.querySelectorAll('.mt-row'), r => r.style.display !== 'none');
    }
    function renaming() { return !!document.querySelector('.mt-rename-input'); }

    // ── Floating menu ────────────────────────────────────────────────────────
    function closeMenu() { if (menuEl) { menuEl.remove(); menuEl = null; } }

    function positionMenu(m, anchor) {
        const r = anchor.getBoundingClientRect();
        const mw = m.offsetWidth || 200, mh = m.offsetHeight || 130;
        let left = Math.max(8, r.right - mw);
        let top = r.bottom + 4;
        if (top + mh > window.innerHeight) top = Math.max(8, r.top - mh - 4);
        m.style.left = left + 'px';
        m.style.top = top + 'px';
    }

    function openMenu(id) {
        closeMenu();
        const m = document.createElement('div');
        m.className = 'mt-menu';
        m.dataset.id = id;
        m.innerHTML =
            '<button class="mt-menu-item" data-act="tags"><span class="mt-mi-ico">🏷</span>Tags<span class="mt-mi-arrow">›</span></button>' +
            '<button class="mt-menu-item" data-act="rename"><span class="mt-mi-ico">✎</span>Rename</button>' +
            '<button class="mt-menu-item mt-danger" data-act="delete"><span class="mt-mi-ico">🗑</span>Delete</button>';
        document.body.appendChild(m);
        const anchor = rowEl(id) && rowEl(id).querySelector('.mt-kebab');
        if (anchor) positionMenu(m, anchor);
        menuEl = m;
    }

    function openTagPicker(id) {
        closeMenu();
        const active = new Set(rowTags(id).map(t => t.id));
        const m = document.createElement('div');
        m.className = 'mt-menu mt-tagpicker';
        m.dataset.id = id;
        const items = allTags().map(t =>
            '<button class="mt-tag-opt' + (active.has(t.id) ? ' checked' : '') + '" data-tag="' + t.id + '">' +
            '<span class="mt-tag-dot" style="--tag-color:' + t.color + '"></span>' +
            '<span class="mt-tag-name">' + escapeHtml(t.name) + '</span>' +
            '<span class="mt-tag-check">✓</span></button>').join('');
        m.innerHTML =
            '<div class="mt-tag-search"><input type="text" placeholder="Add tags…" class="mt-tag-input"></div>' +
            '<div class="mt-tag-list">' + (items || '<div class="mt-tag-empty">Type a name + Enter to create a tag.</div>') + '</div>';
        document.body.appendChild(m);
        const anchor = rowEl(id) && rowEl(id).querySelector('.mt-kebab');
        if (anchor) positionMenu(m, anchor);
        menuEl = m;
        const input = m.querySelector('.mt-tag-input');
        if (input) setTimeout(() => input.focus(), 10);
    }

    // ── Inline rename ────────────────────────────────────────────────────────
    function startRename(id) {
        closeMenu();
        const row = rowEl(id);
        if (!row) return;
        const titleEl = row.querySelector('.mt-title');
        if (!titleEl || row.querySelector('.mt-rename-input')) return;
        const cur = titleEl.textContent;
        const input = document.createElement('input');
        input.className = 'mt-rename-input';
        input.value = cur;
        titleEl.replaceWith(input);
        input.focus();
        input.select();
        let done = false;
        const commit = (save) => {
            if (done) return;
            done = true;
            const val = input.value.trim();
            if (save && val && val !== cur) {
                sendCommand({ action: 'rename', id: id, value: val });
            } else {                                   // revert to a plain title span
                const span = document.createElement('span');
                span.className = 'mt-title';
                span.textContent = cur;
                if (input.parentNode) input.replaceWith(span);
            }
        };
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') { e.preventDefault(); commit(true); }
            else if (e.key === 'Escape') { e.preventDefault(); commit(false); }
        });
        input.addEventListener('blur', () => commit(true));
        input.addEventListener('click', (e) => e.stopPropagation());
    }

    // ── Filter / sort / selection ──────────────────────────────────────────────
    function applyFilter() {
        document.querySelectorAll('.mt-row').forEach(row => {
            const hay = row.getAttribute('data-search') || '';
            row.style.display = (!MT.search || hay.indexOf(MT.search) !== -1) ? '' : 'none';
        });
    }
    function applySort() {
        const list = document.querySelector('#meetings .mt-list');
        if (!list) return;
        const current = Array.prototype.slice.call(list.querySelectorAll('.mt-row'));
        const key = MT.sortKey, dir = MT.sortDir === 'asc' ? 1 : -1;
        const sorted = current.slice().sort((a, b) => {
            let av = a.getAttribute('data-' + key) || '', bv = b.getAttribute('data-' + key) || '';
            if (key === 'date') return (Number(av) - Number(bv)) * dir;
            return av.localeCompare(bv) * dir;
        });
        // Only touch the DOM when the order actually changes — re-appending
        // unconditionally would mutate the tree and retrigger the observer.
        const changed = sorted.some((r, i) => r !== current[i]);
        if (changed) sorted.forEach(r => list.appendChild(r));
        document.querySelectorAll('.mt-sort-btn').forEach(b =>
            b.classList.toggle('active', b.getAttribute('data-sort') === key));
        const dirBtn = document.querySelector('.mt-sort-dir');
        if (dirBtn) { dirBtn.textContent = MT.sortDir === 'asc' ? '↑' : '↓'; dirBtn.setAttribute('data-dir', MT.sortDir); }
    }
    function applySelection() {
        document.querySelectorAll('.mt-row').forEach(r =>
            r.classList.toggle('mt-selected', r.getAttribute('data-id') === MT.selected));
    }
    function reapplyAll() { applyFilter(); applySort(); applySelection(); }

    function syncSelectionFromDOM() {
        const c = document.getElementById('meetings');
        if (!c) return;
        const ds = c.getAttribute('data-selected') || '';
        if (ds === '__none__') MT.selected = '';
        else if (ds) MT.selected = ds;   // '' → preserve current selection
    }

    function selectRow(id) {
        MT.selected = id;
        applySelection();
        sendCommand({ action: 'select', id: id });
    }

    // ── Click delegation ───────────────────────────────────────────────────────
    document.addEventListener('click', (e) => {
        // Clicks inside an open menu / tag picker.
        if (menuEl && menuEl.contains(e.target)) {
            const item = e.target.closest('.mt-menu-item');
            if (item) {
                const id = menuEl.dataset.id;
                const act = item.getAttribute('data-act');
                if (act === 'tags') openTagPicker(id);
                else if (act === 'rename') startRename(id);
                else if (act === 'delete') {
                    closeMenu();
                    if (window.confirm('Delete this transcript permanently?')) {
                        sendCommand({ action: 'delete', id: id });
                    }
                }
                return;
            }
            const tagOpt = e.target.closest('.mt-tag-opt');
            if (tagOpt) {
                tagOpt.classList.toggle('checked');                    // optimistic
                sendCommand({ action: 'tag_toggle', id: menuEl.dataset.id, tag_id: tagOpt.getAttribute('data-tag') });
                return;
            }
            return;   // other clicks inside the menu are inert
        }

        // Kebab → open/close menu.
        const kebab = e.target.closest('.mt-kebab');
        if (kebab) {
            e.stopPropagation();
            e.preventDefault();
            const id = kebab.getAttribute('data-id');
            if (menuEl && menuEl.dataset.id === id) closeMenu();
            else openMenu(id);
            return;
        }

        // Sort controls.
        const sortBtn = e.target.closest('.mt-sort-btn');
        if (sortBtn) { MT.sortKey = sortBtn.getAttribute('data-sort'); applySort(); return; }
        const dirBtn = e.target.closest('.mt-sort-dir');
        if (dirBtn) { MT.sortDir = MT.sortDir === 'asc' ? 'desc' : 'asc'; applySort(); return; }

        // Anything else closes an open menu.
        closeMenu();

        // Row click → select (ignore clicks on the inline rename input).
        const row = e.target.closest('.mt-row');
        if (row && !e.target.closest('.mt-rename-input')) {
            selectRow(row.getAttribute('data-id'));
        }
    });

    // ── Keyboard ────────────────────────────────────────────────────────────────
    document.addEventListener('keydown', (e) => {
        const t = e.target;
        // Tag-picker input: Enter creates a tag, Esc closes.
        if (t && t.classList && t.classList.contains('mt-tag-input')) {
            if (e.key === 'Enter') {
                e.preventDefault();
                const v = t.value.trim();
                if (v && menuEl) { sendCommand({ action: 'tag_create', id: menuEl.dataset.id, value: v }); t.value = ''; }
            } else if (e.key === 'Escape') {
                closeMenu();
            }
            return;
        }
        // Don't hijack typing elsewhere (search box, rename input, textareas).
        const typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable);
        if (typing || menuEl || renaming()) return;
        // ↑/↓ move the selection between transcripts.
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            const rows = visibleRows();
            if (!rows.length) return;
            let idx = rows.findIndex(r => r.getAttribute('data-id') === MT.selected);
            if (idx === -1) idx = e.key === 'ArrowDown' ? -1 : 0;
            idx = e.key === 'ArrowDown' ? Math.min(rows.length - 1, idx + 1) : Math.max(0, idx - 1);
            const row = rows[idx];
            if (row) { row.scrollIntoView({ block: 'nearest' }); selectRow(row.getAttribute('data-id')); }
            e.preventDefault();
        }
    });

    // ── Search box (client-side filter) ──────────────────────────────────────────
    document.addEventListener('input', (e) => {
        if (e.target && e.target.closest && e.target.closest('#search-box')) {
            MT.search = (e.target.value || '').toLowerCase().trim();
            applyFilter();
        }
    });

    window.addEventListener('resize', closeMenu);
    window.addEventListener('scroll', closeMenu, true);

    // ── Re-apply state after Gradio re-renders the list ──────────────────────────
    function watchList() {
        const host = document.getElementById('meetings-list');
        if (!host) return false;
        if (host.dataset.wdObserved) { reapplyAll(); return true; }
        host.dataset.wdObserved = '1';
        let scheduled = false;
        const obs = new MutationObserver(() => {
            if (renaming()) return;              // never reorder while renaming (would blur the input)
            if (scheduled) return;
            scheduled = true;
            requestAnimationFrame(() => {
                scheduled = false;
                if (renaming()) return;
                syncSelectionFromDOM();
                // Disconnect while we mutate the DOM ourselves, otherwise our
                // own re-ordering retriggers the observer in an endless loop
                // (which constantly detaches rows and makes clicks miss).
                obs.disconnect();
                reapplyAll();
                obs.observe(host, { childList: true, subtree: true });
            });
        });
        obs.observe(host, { childList: true, subtree: true });
        reapplyAll();
        return true;
    }

    // ── Sidebar resize handle ─────────────────────────────────────────────────────
    function attachResize() {
        const sidebar = document.getElementById('sidebar');
        if (!sidebar || sidebar.querySelector('.resize-handle')) return !!sidebar;
        const h = document.createElement('div');
        h.className = 'resize-handle';
        sidebar.style.position = 'relative';
        sidebar.appendChild(h);
        let resizing = false, startX = 0, startW = 0;
        h.addEventListener('mousedown', (e) => {
            resizing = true; startX = e.clientX; startW = sidebar.offsetWidth;
            h.classList.add('dragging');
            document.body.style.cursor = 'col-resize';
            document.body.style.userSelect = 'none';
            e.preventDefault();
        });
        document.addEventListener('mousemove', (e) => {
            if (!resizing) return;
            const w = Math.max(300, Math.min(760, startW + (e.clientX - startX)));
            // Use `important` so these inline values beat the persisted
            // `#sidebar { … !important }` rule injected at launch.
            sidebar.style.setProperty('flex', '0 0 ' + w + 'px', 'important');
            sidebar.style.setProperty('min-width', w + 'px', 'important');
            sidebar.style.setProperty('max-width', w + 'px', 'important');
        });
        document.addEventListener('mouseup', () => {
            if (!resizing) return;
            resizing = false; h.classList.remove('dragging');
            document.body.style.cursor = ''; document.body.style.userSelect = '';
            // Persist the chosen width so it's restored on the next app start.
            sendCommand({ action: 'set_sidebar_width', value: Math.round(sidebar.offsetWidth) });
        });
        return true;
    }

    // Elements mount asynchronously — retry a few times until they exist.
    [50, 200, 600, 1500, 3000].forEach(t => {
        setTimeout(attachResize, t);
        setTimeout(watchList, t);
    });
}
