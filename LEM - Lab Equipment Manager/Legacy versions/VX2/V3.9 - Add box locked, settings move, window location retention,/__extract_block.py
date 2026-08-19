import pathlib
from itertools import islice
path = pathlib.Path(r"\\asapserver\Labsharedrive\Ryan C\EQM\VX - Work Dir\main_window.py")
text = path.read_text(encoding="utf-8", errors="replace")
start = text.index("        menu = QMenu(self)\n")
end = text.index("    # ----- settings -----", start)
block = text[start:end]
print(block)
