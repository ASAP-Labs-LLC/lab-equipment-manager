// Settings › This browser: the Theme and Sidebar choices, through the
// shell's LEMTheme and LEMSidebar so the user menu and this page are one
// control. Both apply at once; nothing is sent to the server.
(function () {
    'use strict';
    function wire(groupId, get, set, eventName) {
        const group = document.getElementById(groupId);
        if (!group) return;
        const buttons = Array.from(group.querySelectorAll('[data-choice]'));
        const sync = () => {
            const now = get();
            buttons.forEach(b => b.setAttribute('aria-checked', b.dataset.choice === now ? 'true' : 'false'));
        };
        buttons.forEach(b => b.addEventListener('click', () => {
            set(b.dataset.choice);
            sync();
            const state = document.getElementById('saved-state');
            if (state) state.textContent = 'Saved on this computer';
        }));
        // a radio group: arrow keys move the choice
        group.addEventListener('keydown', (ev) => {
            const i = buttons.findIndex(b => b.getAttribute('aria-checked') === 'true');
            const step = ev.key === 'ArrowRight' || ev.key === 'ArrowDown' ? 1
                : ev.key === 'ArrowLeft' || ev.key === 'ArrowUp' ? -1 : 0;
            if (!step) return;
            ev.preventDefault();
            const next = buttons[(i + step + buttons.length) % buttons.length];
            next.click();
            next.focus();
        });
        document.addEventListener(eventName, sync);
        sync();
    }
    document.addEventListener('DOMContentLoaded', () => {
        wire('theme-choice', () => window.LEMTheme.get().choice, (c) => window.LEMTheme.set(c), 'lem:theme');
        wire('sidebar-choice', () => window.LEMSidebar.get(), (c) => window.LEMSidebar.set(c), 'lem:sidebar');

        // the sub-nav marks the section in view
        const links = Array.from(document.querySelectorAll('#set-nav a'));
        const mark = (id) => links.forEach(a => {
            const on = a.getAttribute('href') === '#' + id;
            a.classList.toggle('active', on);
            if (on) a.setAttribute('aria-current', 'true'); else a.removeAttribute('aria-current');
        });
        // the last section whose title has reached the upper third; the
        // first one at the top of the page (a short page never skips it)
        const sections = links.map(a => document.querySelector(a.getAttribute('href'))).filter(Boolean);
        const pick = () => {
            const line = window.innerHeight / 3;
            let current = sections[0];
            for (const s of sections) if (s.getBoundingClientRect().top <= line) current = s;
            if (window.scrollY < 8) current = sections[0];
            if (current) mark(current.id);
        };
        window.addEventListener('scroll', pick, { passive: true });
        pick();
        links.forEach(a => a.addEventListener('click', () => setTimeout(() => mark(a.getAttribute('href').slice(1)), 0)));
    });
})();
