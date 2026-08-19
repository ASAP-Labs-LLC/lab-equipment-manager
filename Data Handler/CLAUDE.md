# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project purpose

Desktop GUI application for parsing and consolidating lab instrument CSV output (e.g. Eraspec octane/density analyzers). Watches input files/folders or RS-232 COM ports, applies configurable column reordering and math operations to incoming rows, and appends results to per-machine master CSVs on a shared lab drive.

## Versions

- `Data Handler 1.05 ISO/` - current stable; adds ISO uncertainty handling on top of the 1.04 line.
- `Beta/Data Handler 1.5 beta/` - same file layout as ISO, in-progress changes.
- `Beta/Data Handler v2/` - larger refactor: adds `pipeline_editor.py`, `serial_manager.py`, and a `requirements.txt`.
- `Legacy/` - historical snapshots (1.01 through 1.04.x, plus OptiMPP and EMS experiments). Read-only reference.

## Run / develop

- GUI framework: PyQt5 (confirmed via imports in `ui.pyw`, `parser_tab.py`, `csv_parser.py`, etc.).
- Entry point: `ui.pyw` - launch with `pythonw ui.pyw` (or via the `.lnk` shortcut on Windows).
- Install deps (from Beta v2's requirements.txt, applies here too):
  `pip install PyQt5 pyserial pandas numexpr chardet`
- Runtime config and logs live in `~/csv_parser_configs/` (per-user), not next to the source.

## Architecture

- **UI layer** (PyQt5):
  - `ui.pyw` - `MainWindow`, tab container, menus, settings/theme loading.
  - `parser_tab.py` - `ParserTab` widget per configured parser; owns a `QTimer` refresh, status label, table view, and background COM/CSV worker threads.
  - `config_dialogs.py` - `ConfigDialog` to add/edit a parser's `parser_config.json`.
  - `settings_dialog.py` - app-wide settings (scale factor, theme).
- **Parsing layer**:
  - `csv_parser.py` - core `parse_csv`, encoding detection (chardet), `numexpr` math ops, per-file RLocks, config caching, Lab ID prompting bridge.
  - `parser_config.json` - per-parser config: `header`, `data` pipeline (`reorder`, `math_operations` like `C3 = round((141.5 / (C13 / 0.99901)) - 131.5, 2)`), `parser_type` (`single` / `multi` / `COM`), and type-specific blocks (`single_csv`, `multi`, `com_port`) with input/output paths and delimiter. One per parser folder under `~/csv_parser_configs/<name>/`.
  - `data_handler.py` - orchestration: `process_single_csv` (tails one file via `last_position`), `process_multi_csv` (thread-pool scans a folder), `process_com_port` (pyserial STX/ETX framing, producer/consumer queue), and `MasterCSVWriter` (per-master-file queue+worker thread that appends, rotates daily into `Past Data/`, and holds file locks).
- **Data model**:
  - `pandas_model.py` - `PandasModel(QAbstractTableModel)` wraps a `DataFrame` for `QTableView` display and sorting in each `ParserTab`.
- **Settings**:
  - `settings_manager.py` - `SettingsManager` reads/writes `~/csv_parser_configs/settings.json` (scale_factor, current_theme).

Flow: `MainWindow` creates one `ParserTab` per parser folder -> `ParserTab` starts a worker (`process_single_csv` / `process_multi_csv` / `process_com_port`) -> workers call `parse_csv` (csv_parser) -> resulting DataFrame goes to `append_to_master_csv` -> `MasterCSVWriter` queue -> disk.

## Notes

- Themes are CSS Qt stylesheets loaded from `~/csv_parser_configs/themes/`. The repo ships `asap_dark.css`, `asap_light.css`, `test.css`, etc.
- `EQM_Correction Factor/correction_factors.json` is the default correction-factor file resolved via `BASE_DIR` in `csv_parser.py` / `settings_dialog.py`.
- COM parsing frames messages by ASCII STX (0x02) / ETX (0x03), with an `idle_gap` fallback. Worker count auto-scales to CPU.
- Master CSVs auto-rotate when their mtime date != today: prior day's file is moved to `Past Data/<name>_YYYY-MM-DD.csv`.
- `com_debug.py` is a standalone diagnostic helper, not part of the main app.
- Logs: `~/csv_parser_configs/parser.log` (app-wide) plus per-folder logs like `iso_uncertainty.log`.
- `Reference/` and `Past Data/` are runtime data folders, not source.
