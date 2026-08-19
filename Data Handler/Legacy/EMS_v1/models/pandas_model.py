# File: csv_parser_app/models/pandas_model.py

import pandas as pd
from PyQt5.QtCore import QAbstractTableModel, Qt, QModelIndex

class PandasModel(QAbstractTableModel):
    """
    A model to interface a pandas DataFrame with QTableView.
    """

    def __init__(self, data: pd.DataFrame):
        super().__init__()
        self._data = data

    def rowCount(self, parent=None) -> int:
        """Return the number of rows in the DataFrame."""
        return len(self._data.index)

    def columnCount(self, parent=None) -> int:
        """Return the number of columns in the DataFrame."""
        return len(self._data.columns)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        """Return the data at the given index for display in the view."""
        if not index.isValid():
            return None
        if role == Qt.DisplayRole:
            value = self._data.iloc[index.row(), index.column()]
            return str(value)
        return None

    def headerData(self, section: int, orientation, role=Qt.DisplayRole):
        """Return the header data for the given section and orientation."""
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self._data.columns[section]
        elif orientation == Qt.Vertical and role == Qt.DisplayRole:
            return str(self._data.index[section])
        return None

    def sort(self, column: int, order: Qt.SortOrder) -> None:
        """Sort the data in the DataFrame based on the given column and order."""
        colname = self._data.columns.tolist()[column]
        self.layoutAboutToBeChanged.emit()
        self._data.sort_values(colname, ascending=order == Qt.AscendingOrder, inplace=True)
        self._data.reset_index(inplace=True, drop=True)
        self.layoutChanged.emit()
