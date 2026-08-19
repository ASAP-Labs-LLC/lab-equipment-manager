"""Cross-platform helpers: OS detection + portable equivalents of Windows-specific calls."""
import os
import sys
import platform
import subprocess
from pathlib import Path

SYSTEM = platform.system()
IS_WINDOWS = SYSTEM == 'Windows'
IS_MAC = SYSTEM == 'Darwin'
IS_LINUX = SYSTEM == 'Linux'


def open_with_default_app(path) -> None:
    p = str(path)
    if IS_WINDOWS:
        os.startfile(p)  # type: ignore[attr-defined]
    elif IS_MAC:
        subprocess.run(['open', p], check=False)
    else:
        subprocess.run(['xdg-open', p], check=False)


def subprocess_no_window_kwargs() -> dict:
    if IS_WINDOWS and hasattr(subprocess, 'CREATE_NO_WINDOW'):
        return {'creationflags': subprocess.CREATE_NO_WINDOW}
    return {}


def user_config_dir(app_name: str) -> Path:
    if IS_WINDOWS:
        base = Path(os.environ.get('APPDATA') or (Path.home() / 'AppData' / 'Roaming'))
    elif IS_MAC:
        base = Path.home() / 'Library' / 'Application Support'
    else:
        base = Path(os.environ.get('XDG_CONFIG_HOME') or (Path.home() / '.config'))
    p = base / app_name
    p.mkdir(parents=True, exist_ok=True)
    return p
