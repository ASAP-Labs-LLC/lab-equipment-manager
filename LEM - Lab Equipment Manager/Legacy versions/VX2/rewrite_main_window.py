from pathlib import Path

content = """#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""

Path('main_window.py').write_text(content)
