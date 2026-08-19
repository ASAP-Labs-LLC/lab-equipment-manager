# File: csv_parser_app/core/main.py
# or  EMS_v1/core/main.py  (depending on your folder name)

import sys
import ctypes
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt
from ..ui.main_window import MainWindow  # or use absolute import if needed

def main():
    """
    Main entry point for the CSV Parser application.
    Demonstrates enhanced debugging statements for troubleshooting.
    """
    print("========== Starting CSV Parser Application ==========")

    # Attempt to enable DPI awareness on Windows (for high-DPI displays)
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        print("[DEBUG] Successfully set process DPI awareness.")
    except Exception as dpi_ex:
        print(f"[WARNING] Failed to set DPI awareness: {dpi_ex}")

    # Enable High DPI scaling (Qt attributes)
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    print("[DEBUG] Enabled high DPI scaling in QApplication.")

    # Create the QApplication instance
    print("[DEBUG] Creating QApplication...")
    app = QApplication(sys.argv)

    # (Optional) Set the Fusion style for consistency on all platforms
    app.setStyle('Fusion')
    print("[DEBUG] Set application style to Fusion.")

    try:
        print("[DEBUG] Instantiating MainWindow...")
        window = MainWindow()

        # For debugging, optionally force a known geometry:
        print("[DEBUG] Setting MainWindow geometry (x=100, y=100, width=900, height=600).")
        window.setGeometry(100, 100, 900, 600)

        print("[DEBUG] Showing MainWindow on screen...")
        window.show()

        print("[DEBUG] Entering the Qt event loop. The next line won't run until the window closes.")
        exit_code = app.exec_()
        print(f"[DEBUG] Application event loop exited with code: {exit_code}")
        sys.exit(exit_code)

    except Exception as ex:
        print(f"[ERROR] Exception occurred while creating or showing the MainWindow: {ex}")
        sys.exit(1)

if __name__ == "__main__":
    main()
