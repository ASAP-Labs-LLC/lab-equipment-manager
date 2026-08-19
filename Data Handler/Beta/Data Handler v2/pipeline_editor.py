import json
import os
import csv
import io
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem, 
    QPushButton, QLabel, QSplitter, QPlainTextEdit, QFrame, QDialog,
    QFormLayout, QLineEdit, QComboBox, QMessageBox, QAbstractItemView
)
from PyQt5.QtCore import Qt, pyqtSignal, QMimeData
from PyQt5.QtGui import QDrag

class PipelineEditor(QDialog):
    """
    Main dialog for editing the data processing pipeline.
    """
    def __init__(self, existing_actions=None, config=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pipeline Editor")
        self.resize(1100, 700)
        
        self.actions = existing_actions or []
        self.config = config or {}
        
        self.init_ui()
        self._load_initial_sample()
        self.update_preview()

    def init_ui(self):
        dialog_layout = QVBoxLayout(self)
        content_layout = QHBoxLayout()
        
        # --- LEFT: Action List ---
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0,0,0,0)
        
        lbl_pipeline = QLabel("Processing Steps (Drag to Reorder)")
        lbl_pipeline.setStyleSheet("font-weight: bold;")
        left_layout.addWidget(lbl_pipeline)
        
        self.action_list = QListWidget()
        self.action_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.action_list.itemDoubleClicked.connect(self.edit_selected_action)
        self.action_list.model().rowsMoved.connect(self.on_reorder)
        
        for action in self.actions:
            self.add_action_to_list(action)
            
        left_layout.addWidget(self.action_list)
        
        toolbar = QHBoxLayout()
        btn_add = QPushButton("Add Step")
        btn_add.clicked.connect(self.add_new_action)
        btn_remove = QPushButton("Remove")
        btn_remove.clicked.connect(self.remove_selected_action)
        
        toolbar.addWidget(btn_add)
        toolbar.addWidget(btn_remove)
        left_layout.addLayout(toolbar)
        
        content_layout.addWidget(left_panel, 35)
        
        # --- RIGHT: Preview ---
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0,0,0,0)
        
        lbl_preview = QLabel("Live Preview / Sandbox")
        lbl_preview.setStyleSheet("font-weight: bold;")
        right_layout.addWidget(lbl_preview)
        
        splitter = QSplitter(Qt.Vertical)
        
        # Input
        inp_w = QWidget()
        inp_l = QVBoxLayout(inp_w)
        inp_l.setContentsMargins(0,0,0,0)
        inp_l.addWidget(QLabel("Sample CSV Input:"))
        self.input_preview = QPlainTextEdit()
        self.input_preview.setPlaceholderText("Enter a sample raw CSV line here...")
        self.input_preview.textChanged.connect(self.update_preview)
        inp_l.addWidget(self.input_preview)
        splitter.addWidget(inp_w)
        
        # Output
        out_w = QWidget()
        out_l = QVBoxLayout(out_w)
        out_l.setContentsMargins(0,0,0,0)
        out_l.addWidget(QLabel("Pipeline Result:"))
        self.output_preview = QPlainTextEdit()
        self.output_preview.setReadOnly(True)
        self.output_preview.setStyleSheet("background-color: #2b2b2b; color: #a9b7c6; font-family: monospace;")
        out_l.addWidget(self.output_preview)
        splitter.addWidget(out_w)
        
        right_layout.addWidget(splitter)
        content_layout.addWidget(right_panel, 65)
        
        dialog_layout.addLayout(content_layout)
        
        # Footer
        footer = QHBoxLayout()
        btn_save = QPushButton("Save Pipeline")
        btn_save.clicked.connect(self.accept)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        
        footer.addStretch()
        footer.addWidget(btn_save)
        footer.addWidget(btn_cancel)
        dialog_layout.addLayout(footer)

    def _load_initial_sample(self):
        """Try to load the last line from the input file if configured."""
        sample_line = "TestID, 100, 200, 300" # Default fallback
        
        try:
            ptype = self.config.get('parser_type')
            input_path = None
            
            if ptype == 'single':
                input_path = self.config.get('single_csv', {}).get('input')
            elif ptype == 'multi':
                input_dir = self.config.get('multi', {}).get('input')
                if input_dir and os.path.isdir(input_dir):
                    files = [f for f in os.listdir(input_dir) if f.lower().endswith('.csv')]
                    if files:
                        input_path = os.path.join(input_dir, files[0])
            
            if input_path and os.path.isfile(input_path):
                # Efficiently read the last few lines to find one that isn't empty
                with open(input_path, 'rb') as f:
                    f.seek(0, os.SEEK_END)
                    size = f.tell()
                    # Read last 4KB
                    offset = max(0, size - 4096)
                    f.seek(offset)
                    blob = f.read().decode('utf-8', errors='replace')
                    lines = [l.strip() for l in blob.split('\n') if l.strip()]
                    if lines:
                        sample_line = lines[-1]
        except Exception:
            pass # Fall back to default if any error
            
        self.input_preview.setPlainText(sample_line)

    def add_action_to_list(self, action):
        item = QListWidgetItem()
        self.update_item_display(item, action)
        item.setData(Qt.UserRole, action)
        self.action_list.addItem(item)

    def update_item_display(self, item, action):
        kind = action.get('action', 'unknown')
        if kind == 'remove':
            text = f"REMOVE substring: '{action.get('substring')}'"
            icon = "✂️"
        elif kind == 'force_to_cell':
            text = f"FORCE '{action.get('substring')}' -> Col {action.get('target_column')}"
            icon = "📍"
        elif kind == 'reorder':
            text = f"REORDER: {action.get('order')}"
            icon = "⇄"
        elif kind == 'math_operations':
            ops = len(action.get('operations', []))
            text = f"MATH: {ops} operation(s)"
            icon = "∑"
        else:
            text = f"UNKNOWN: {kind}"
            icon = "?"
        item.setText(f"{icon} {text}")

    def add_new_action(self):
        type_dialog = QDialog(self)
        type_dialog.setWindowTitle("Select Action Type")
        l = QVBoxLayout(type_dialog)
        combo = QComboBox()
        combo.addItems(["remove", "force_to_cell", "reorder", "math_operations"])
        btn_ok = QPushButton("Next")
        btn_ok.clicked.connect(type_dialog.accept)
        l.addWidget(QLabel("Action Type:"))
        l.addWidget(combo)
        l.addWidget(btn_ok)
        
        if type_dialog.exec_():
            new_type = combo.currentText()
            new_action = {'action': new_type}
            if new_type == 'reorder': new_action['order'] = []
            if new_type == 'math_operations': new_action['operations'] = []
            
            self._edit_action(new_action, is_new=True)

    def edit_selected_action(self):
        item = self.action_list.currentItem()
        if not item: return
        action = item.data(Qt.UserRole)
        self._edit_action(action, item=item)

    def _edit_action(self, action, item=None, is_new=False):
        # Determine the current columns for reorder/force preview
        sample = self.input_preview.toPlainText()
        cols = []
        try:
            reader = csv.reader(io.StringIO(sample))
            data = list(reader)
            if data: cols = [c.strip() for c in data[0]]
        except:
            pass

        editor = ActionConfigDialog(action, current_columns=cols, parent=self)
        if editor.exec_():
            final_action = editor.get_action()
            if is_new:
                self.actions.append(final_action)
                self.add_action_to_list(final_action)
            else:
                row = self.action_list.row(item)
                self.actions[row] = final_action
                item.setData(Qt.UserRole, final_action)
                self.update_item_display(item, final_action)
            self.update_preview()

    def remove_selected_action(self):
        row = self.action_list.currentRow()
        if row >= 0:
            self.actions.pop(row)
            self.action_list.takeItem(row)
            self.update_preview()

    def on_reorder(self):
        new_actions = []
        for i in range(self.action_list.count()):
            item = self.action_list.item(i)
            new_actions.append(item.data(Qt.UserRole))
        self.actions = new_actions
        self.update_preview()

    def update_preview(self):
        input_text = self.input_preview.toPlainText()
        try:
            f = io.StringIO(input_text)
            reader = csv.reader(f)
            data = list(reader)
            processed = self.simulate_pipeline(data)
            out = io.StringIO()
            writer = csv.writer(out)
            writer.writerows(processed)
            self.output_preview.setPlainText(out.getvalue())
        except Exception as e:
            self.output_preview.setPlainText(f"Preview Error: {e}")

    def simulate_pipeline(self, data):
        working_data = [row[:] for row in data]
        for action in self.actions:
            atype = action.get('action')
            if atype == 'remove':
                sub = action.get('substring', '')
                if sub:
                    for r in working_data:
                        for i, cell in enumerate(r):
                            if isinstance(cell, str):
                                r[i] = cell.replace(sub, '').strip()
            elif atype == 'force_to_cell':
                sub = action.get('substring', '')
                try:
                    target = int(action.get('target_column', 1)) - 1
                    for r in working_data:
                        for i, cell in enumerate(r):
                            if sub in cell:
                                if target >= len(r): r.extend(['']*(target-len(r)+1))
                                r[target] = cell
                                break
                except: pass
            elif atype == 'reorder':
                order = action.get('order', [])
                new_data = []
                for r in working_data:
                    new_row = []
                    for idx in order:
                        i = idx - 1
                        val = r[i] if 0 <= i < len(r) else ''
                        new_row.append(val)
                    new_data.append(new_row)
                working_data = new_data
            elif atype == 'math_operations':
                # No logic here, just placeholder for preview
                pass
        return working_data
    
    def get_pipeline(self):
        return self.actions


class ActionConfigDialog(QDialog):
    """Configuration for a single action, now with better reorder UX."""
    def __init__(self, action, current_columns=None, parent=None):
        super().__init__(parent)
        self.action = action.copy()
        self.cols = current_columns or []
        self.setWindowTitle(f"Edit {action.get('action')}")
        self.resize(500, 400)
        self.init_ui()
        
    def init_ui(self):
        layout = QVBoxLayout(self)
        kind = self.action.get('action')
        
        form = QFormLayout()
        
        if kind == 'remove':
            self.inp_sub = QLineEdit(self.action.get('substring', ''))
            form.addRow("Substring to Remove:", self.inp_sub)
            layout.addLayout(form)
            
        elif kind == 'force_to_cell':
            self.inp_sub = QLineEdit(self.action.get('substring', ''))
            self.inp_col = QLineEdit(str(self.action.get('target_column', 1)))
            form.addRow("If cell contains:", self.inp_sub)
            form.addRow("Move to Column #:", self.inp_col)
            # Maybe show column names if available
            if self.cols:
                lbl = QLabel("Current columns: " + ", ".join([f"{i+1}: {c}" for i, c in enumerate(self.cols)]))
                lbl.setWordWrap(True)
                lbl.setStyleSheet("font-style: italic; color: gray;")
                layout.addWidget(lbl)
            layout.addLayout(form)
            
        elif kind == 'reorder':
            layout.addWidget(QLabel("Drag columns to define the output order (Top List):"))
            
            # --- Selected columns (Included) ---
            layout.addWidget(QLabel("<b>Included (Output Row)</b>"))
            self.list_sel = QListWidget()
            self.list_sel.setFlow(QListWidget.LeftToRight)
            self.list_sel.setWrapping(True)
            self.list_sel.setResizeMode(QListWidget.Adjust)
            self.list_sel.setDragDropMode(QAbstractItemView.DragDrop)
            self.list_sel.setDefaultDropAction(Qt.MoveAction)
            self.list_sel.setFixedHeight(80)
            self.list_sel.setStyleSheet("background-color: #2b2b2b; border: 1px solid #555;")
            layout.addWidget(self.list_sel)
            
            layout.addSpacing(10)
            
            # --- Available columns (Ignored) ---
            layout.addWidget(QLabel("<b>Ignored (Available Columns)</b>"))
            self.list_avail = QListWidget()
            self.list_avail.setFlow(QListWidget.LeftToRight)
            self.list_avail.setWrapping(True)
            self.list_avail.setResizeMode(QListWidget.Adjust)
            self.list_avail.setDragDropMode(QAbstractItemView.DragDrop)
            self.list_avail.setDefaultDropAction(Qt.MoveAction)
            self.list_avail.setFixedHeight(120) 
            self.list_avail.setStyleSheet("background-color: #333; border: 1px dashed #777;")
            layout.addWidget(self.list_avail)
            
            # Populate
            current_order = self.action.get('order', [])
            included_indices = set(current_order)
            
            for idx in current_order:
                i = idx - 1
                name = self.cols[i] if 0 <= i < len(self.cols) else f"Col {idx}"
                self._add_col_item(self.list_sel, idx, name)
                    
            for i, val in enumerate(self.cols):
                idx = i + 1
                if idx not in included_indices:
                    self._add_col_item(self.list_avail, idx, val)
            
        elif kind == 'math_operations':
            self.inp_ops = QPlainTextEdit()
            ops = self.action.get('operations', [])
            self.inp_ops.setPlainText("\n".join(ops))
            form.addRow("Operations (e.g. C1 = C2 + C3):", self.inp_ops)
            layout.addLayout(form)
            
        btn_save = QPushButton("Save Step")
        btn_save.clicked.connect(self.save)
        layout.addStretch()
        layout.addWidget(btn_save)

    def _add_col_item(self, list_widget, index, value):
        item = QListWidgetItem(f"{index}: {value}")
        item.setData(Qt.UserRole, index)
        list_widget.addItem(item)
        
    def save(self):
        kind = self.action.get('action')
        if kind == 'remove':
            self.action['substring'] = self.inp_sub.text().strip()
        elif kind == 'force_to_cell':
            self.action['substring'] = self.inp_sub.text().strip()
            try: self.action['target_column'] = int(self.inp_col.text())
            except: pass
        elif kind == 'reorder':
            order = []
            for i in range(self.list_sel.count()):
                idx = self.list_sel.item(i).data(Qt.UserRole)
                order.append(idx)
            self.action['order'] = order
        elif kind == 'math_operations':
            txt = self.inp_ops.toPlainText()
            self.action['operations'] = [x.strip() for x in txt.split('\n') if x.strip()]
            
        self.accept()
        
    def get_action(self):
        return self.action
