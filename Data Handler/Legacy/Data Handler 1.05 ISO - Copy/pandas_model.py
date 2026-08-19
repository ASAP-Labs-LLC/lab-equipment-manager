import pandas as pd
from PyQt5.QtCore import QAbstractTableModel, Qt

class PandasModel(QAbstractTableModel):
    """A model to interface a pandas DataFrame with QTableView."""
    def __init__(self, data):
        super(PandasModel, self).__init__()
        self._data = data

    def rowCount(self, parent=None):
        """Return the number of rows in the DataFrame."""
        return len(self._data.index)

    def columnCount(self, parent=None):
        """Return the number of columns in the DataFrame."""
        return len(self._data.columns)

    def data(self, index, role=Qt.DisplayRole):
        """Return the data at the given index for display."""
        if index.isValid():
            if role == Qt.DisplayRole:
                value = self._data.iloc[index.row(), index.column()]
                return str(value)
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        """Return the header data."""
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self._data.columns[section]
        elif orientation == Qt.Vertical and role == Qt.DisplayRole:
            return str(self._data.index[section])
        return None

    def sort(self, column, order):
        """Sort the data in the DataFrame based on the given column and order."""
        colname = self._data.columns.tolist()[column]
        self.layoutAboutToBeChanged.emit()
        self._data.sort_values(colname, ascending=order == Qt.AscendingOrder, inplace=True)
        self._data.reset_index(inplace=True, drop=True)
        self.layoutChanged.emit()
