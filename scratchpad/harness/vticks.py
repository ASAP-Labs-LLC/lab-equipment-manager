#!/usr/bin/env python3
"""vticks.py file.js — every backtick that is INSIDE a template literal rather
than delimiting one, which is the one way to break this file that neither the
eye nor a diff catches. Walks the source as a tiny tokeniser so that backticks
living in // and /* */ comments are ignored, the way the parser ignores them."""
import sys

src = open(sys.argv[1], encoding='utf-8').read()
i, n, line = 0, len(src), 1
state = 'code'          # code | line-comment | block-comment | tmpl | str
opened = 0
bad = []
while i < n:
    c = src[i]
    if c == '\n':
        line += 1
    if state == 'code':
        if src.startswith('//', i):
            state = 'line-comment'; i += 2; continue
        if src.startswith('/*', i):
            state = 'block-comment'; i += 2; continue
        if c in '\'"':
            state, quote = 'str', c; i += 1; continue
        if c == '`':
            state = 'tmpl'; opened = line; i += 1; continue
    elif state == 'line-comment':
        if c == '\n':
            state = 'code'
    elif state == 'block-comment':
        if src.startswith('*/', i):
            state = 'code'; i += 2; continue
    elif state == 'str':
        if c == '\\':
            i += 2; continue
        if c == quote:
            state = 'code'
    elif state == 'tmpl':
        if c == '\\':
            i += 2; continue
        if c == '`':
            # A literal that closes on a line whose text is prose, not `;` or
            # similar, is the tell: the tokeniser cannot know intent, so report
            # every close and let the caller read the line.
            bad.append((opened, line, src[src.rfind('\n', 0, i) + 1:
                                          src.find('\n', i)][:90]))
            state = 'code'
    i += 1

print(f'{sys.argv[1]}: {len(bad)} template literals')
for o, cl, text in bad:
    flag = '' if text.strip() in ('`;', '`,', '`)', '`', '`;,') else '  <-- PROSE'
    if flag:
        print(f'  opened {o:5d}  closed {cl:5d}  {text.strip()[:80]}{flag}')
print('final state:', state)
