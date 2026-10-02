/* wall_boot.js: a wall's ?theme= pin, read before first paint.

   A TV's bookmark is its configuration: /floor?theme=dark keeps that TV dark
   whatever the browser's stored choice or the OS says. shell.js (loaded
   next) reads window.LEM_THEME_PIN in place of the stored choice. Nothing
   is written to storage: pinning one TV must not change anybody's desk. */
(function () {
    'use strict';
    try {
        const t = new URLSearchParams(window.location.search).get('theme');
        if (t === 'dark' || t === 'light') {
            window.LEM_THEME_PIN = t;
            document.documentElement.setAttribute('data-theme', t);
        }
    } catch (_e) { /* no URLSearchParams: the stored choice applies */ }
})();
