import unicodedata
import logging

LOG = logging.getLogger(__name__)

def clean_data(data):
    cleaned_data = []
    for row_idx, row in enumerate(data):
        new_row = []
        for cell_idx, cell in enumerate(row):
            original_cell = cell if isinstance(cell, str) else str(cell) if cell else ''
            cell = unicodedata.normalize('NFKD', original_cell)
            cell = ''.join(c for c in cell if c.isprintable()).strip()
            cell = ' '.join(cell.split())
            if cell != original_cell:
                LOG.debug(f"Cleaned cell at Row {row_idx}, Col {cell_idx} from '{original_cell}' to '{cell}'")
            new_row.append(cell)
        cleaned_data.append(new_row)
    return cleaned_data
