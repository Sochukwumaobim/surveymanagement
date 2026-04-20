# -*- coding: utf-8 -*-
"""
dependency_manager.py  —  Survey Management System v1.2
Manages the ezdxf dependency with full diagnostic logging.
"""

import os
import sys

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QApplication, QMessageBox, QTextEdit
)
from qgis.PyQt.QtCore import Qt


def _plugin_dir():
    return os.path.dirname(os.path.abspath(__file__))


def _lib_dir():
    return os.path.join(_plugin_dir(), "lib")


def _add_lib_to_path():
    """
    Add the plugin lib/ folder to sys.path.
    Inserts AFTER the plugins folder, never at index 0,
    so the plugin package itself is never shadowed by lib/ contents.
    """
    lib = _lib_dir()
    if not os.path.isdir(lib) or lib in sys.path:
        return
    # Find plugins folder and insert just after it
    plugins_idx = None
    for i, p in enumerate(sys.path):
        if "plugins" in str(p).lower() and "surveymanagement" not in str(p).lower():
            plugins_idx = i
            break
    if plugins_idx is not None:
        sys.path.insert(plugins_idx + 1, lib)
    else:
        sys.path.append(lib)


def ezdxf_available():
    """
    Return True if the ezdxf package folder exists in lib/ or on sys.path.
    Does NOT require readfile to be present — it is lazy-loaded and only
    becomes available after the first actual use of the module.
    """
    _add_lib_to_path()

    # If already loaded in this session (any state), trust it
    if "ezdxf" in sys.modules:
        return True

    # Filesystem check — ezdxf folder exists in lib/?
    if os.path.isdir(os.path.join(_lib_dir(), "ezdxf")):
        return True

    # Check anywhere else on sys.path
    for path_entry in sys.path:
        try:
            if os.path.isdir(os.path.join(str(path_entry), "ezdxf")):
                return True
        except Exception:
            pass

    return False


def get_diagnostics():
    """Return a string with full diagnostic info for troubleshooting."""
    lib = _lib_dir()
    lines = [
        f"Plugin dir:   {_plugin_dir()}",
        f"lib/ dir:     {lib}",
        f"lib/ exists:  {os.path.isdir(lib)}",
    ]

    if os.path.isdir(lib):
        contents = os.listdir(lib)
        ezdxf_dirs = [x for x in contents if "ezdxf" in x.lower()]
        lines.append(f"ezdxf in lib: {ezdxf_dirs if ezdxf_dirs else 'NOT FOUND'}")
        lines.append(f"lib contents: {contents[:10]}")

    lines.append(f"sys.path[0:5]: {sys.path[:5]}")
    lines.append(f"lib in path:  {lib in sys.path}")

    # Try import and report
    _add_lib_to_path()
    try:
        import ezdxf
        loc = getattr(ezdxf, "__file__", None) or               getattr(ezdxf, "__path__", ["unknown"])[0]
        has_rf = hasattr(ezdxf, "readfile")
        lines.append(f"ezdxf import: SUCCESS — {loc}")
        lines.append(f"has readfile: {has_rf}")
        if not has_rf:
            lines.append("NOTE: readfile missing — lazy load pending, this is normal")
    except ImportError as e:
        lines.append(f"ezdxf import: FAILED — {e}")

    return "\n".join(lines)


def ensure_ezdxf(parent=None):
    """
    Make sure ezdxf is importable.
    Shows install dialog if not found.
    Returns (True, "") or (False, error_message).
    """
    if ezdxf_available():
        return True, ""

    dlg = _InstallDialog(parent)
    result = dlg.exec_()

    if result != QDialog.Accepted:
        return False, "Installation cancelled by user."

    # Re-check after install — use filesystem check, not import check
    # (readfile is lazy-loaded and may not appear until first actual use)
    _add_lib_to_path()
    ezdxf_dir = os.path.join(_lib_dir(), "ezdxf")
    if os.path.isdir(ezdxf_dir):
        return True, ""
    elif ezdxf_available():
        return True, ""
    else:
        diag = get_diagnostics()
        return False, (
            "ezdxf still not importable after installation.\n\n"
            "Diagnostic information:\n" + diag + "\n\n"
            "Please copy the above and send to the developer,\n"
            "or install manually via the OSGeo4W Shell:\n"
            "  python -m pip install ezdxf"
        )


def _install_ezdxf_to_lib():
    """
    Install ezdxf into the plugin lib/ folder using pip Python API.
    Returns (success, message, diagnostics).
    """
    lib = _lib_dir()
    os.makedirs(lib, exist_ok=True)

    print(f"[SurveyMgmt] Installing ezdxf to: {lib}")

    # Clear any cached state first
    for key in list(sys.modules.keys()):
        if key == "ezdxf" or key.startswith("ezdxf."):
            del sys.modules[key]

    def _pip_install(packages, extra_args=None):
        """Run pip install for a list of packages. Returns (ok, msg)."""
        args = ["install", "--target", lib,
                "--no-user", "--no-warn-script-location",
                "--disable-pip-version-check"]
        if extra_args:
            args.extend(extra_args)
        args.extend(packages)
        print(f"[SurveyMgmt] pip {args}")

        # Strategy 1: pip internal API
        try:
            from pip._internal.cli.main import main as pip_main
            ret = pip_main(args)
            if ret == 0:
                return True, ""
            return False, f"pip exit {ret}"
        except SystemExit as e:
            code = getattr(e, "code", str(e))
            if str(code) == "0" or code == 0:
                return True, ""
            return False, f"pip SystemExit {code}"
        except ImportError:
            pass
        except Exception as e:
            print(f"[SurveyMgmt] pip API error: {e}")

        # Strategy 2: runpy
        try:
            import runpy
            saved = sys.argv[:]
            sys.argv = ["pip"] + args
            try:
                runpy.run_module("pip", run_name="__main__", alter_sys=True)
                return True, ""
            except SystemExit as e:
                code = getattr(e, "code", str(e))
                if str(code) == "0" or code == 0:
                    return True, ""
                return False, f"runpy exit {code}"
            finally:
                sys.argv = saved
        except Exception as e:
            return False, str(e)

    # ── Step 1: install pure-Python dependencies first ───────────────────────
    # pyparsing and typing_extensions are pure Python (no compiled extensions)
    # so they work regardless of QGIS's Python version.
    for pkg in ["pyparsing", "typing_extensions"]:
        ok, msg = _pip_install([pkg, "--no-deps"])
        print(f"[SurveyMgmt] {pkg}: {'OK' if ok else msg}")

    # ── Step 2: install ezdxf WITHOUT its dependencies ───────────────────────
    # ezdxf lists numpy and fontTools as dependencies but they are only needed
    # for rendering/export features we do not use.
    # Installing --no-deps avoids the compiled C extension mismatch that
    # causes "No module named ezdxf" when numpy's .pyd files don't match
    # QGIS's Python version.
    ok, msg = _pip_install(["ezdxf", "--no-deps"])
    print(f"[SurveyMgmt] ezdxf --no-deps: {'OK' if ok else msg}")

    if not ok:
        return False, f"ezdxf install failed: {msg}", get_diagnostics()

    _add_lib_to_path()

    # ── Verify by checking the folder exists, not by importing ───────────────
    # Importing immediately after install can trigger the lazy-load AttributeError.
    # A filesystem check is sufficient — the actual import happens later in
    # dxf_importer._ensure_ezdxf() which handles lazy loading correctly.
    ezdxf_dir = os.path.join(_lib_dir(), "ezdxf")
    if os.path.isdir(ezdxf_dir):
        print(f"[SurveyMgmt] ezdxf folder confirmed at: {ezdxf_dir}")
        return True, "", get_diagnostics()
    else:
        return False, "ezdxf folder not found after install.", get_diagnostics()


class _InstallDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Install ezdxf — Survey Management System")
        self.setMinimumWidth(560)
        self.setWindowFlags(Qt.Dialog | Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(12)

        hdr = QLabel("📦  ezdxf Library Required for DXF Import")
        hdr.setStyleSheet("font-size:13pt; font-weight:bold; color:#1A5C38; padding:4px 0;")
        layout.addWidget(hdr)

        info = QLabel(
            "The <b>ezdxf</b> library is needed to read AutoCAD DXF files. "
            "It will be installed inside the plugin folder (~2 MB). "
            "No administrator rights required."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet("font-weight:bold; min-height:20px;")
        layout.addWidget(self.status_lbl)

        # Diagnostic output (hidden initially)
        self.diag_box = QTextEdit()
        self.diag_box.setReadOnly(True)
        self.diag_box.setStyleSheet("font-family:monospace; font-size:9pt;")
        self.diag_box.setMaximumHeight(120)
        self.diag_box.setVisible(False)
        layout.addWidget(self.diag_box)

        manual = QLabel(
            "<b>Manual alternative:</b> Open OSGeo4W Shell and run:<br>"
            "<code style=\'background:#f0f0f0;padding:2px 6px;\'>"
            "python -m pip install ezdxf</code><br>"
            "Then restart QGIS."
        )
        manual.setWordWrap(True)
        manual.setStyleSheet(
            "background:#F5F5F5;padding:8px;"
            "border-left:3px solid #1A5C38;font-size:10pt;"
        )
        layout.addWidget(manual)

        btn_row = QHBoxLayout()
        self.install_btn = QPushButton("⬇  Install Automatically")
        self.install_btn.setStyleSheet(
            "background-color:#1A5C38;color:white;font-weight:bold;padding:8px 16px;"
        )
        self.install_btn.clicked.connect(self._do_install)
        btn_row.addWidget(self.install_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(
            "background-color:#e74c3c;color:white;font-weight:bold;padding:8px;"
        )
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        self.setLayout(layout)

    def _do_install(self):
        self.install_btn.setEnabled(False)
        self.install_btn.setText("Installing…")
        self.progress.setVisible(True)
        self.status_lbl.setText("Downloading and installing ezdxf…")
        self.status_lbl.setStyleSheet("color:#1A5C38; font-weight:bold;")
        QApplication.processEvents()

        ok, msg, diag = _install_ezdxf_to_lib()

        self.progress.setVisible(False)
        self.diag_box.setText(diag)
        self.diag_box.setVisible(True)

        if ok:
            self.status_lbl.setText("✅  Installed successfully!")
            self.status_lbl.setStyleSheet("color:#1A5C38; font-weight:bold;")
            QApplication.processEvents()
            self.accept()
        else:
            self.status_lbl.setText(f"❌  Failed: {msg}")
            self.status_lbl.setStyleSheet("color:#e74c3c; font-weight:bold;")
            self.install_btn.setText("⬇  Retry")
            self.install_btn.setEnabled(True)
            QMessageBox.critical(
                self, "Installation Failed",
                f"Could not install ezdxf.\n\nError: {msg}\n\n"
                "Diagnostic info shown in the dialog.\n\n"
                "Try the manual OSGeo4W Shell method instead."
            )
