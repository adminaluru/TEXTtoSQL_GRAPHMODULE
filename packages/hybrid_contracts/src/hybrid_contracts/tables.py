"""Typed tabular output. Values retain their types; Decimal serializes as text."""
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from pydantic import AwareDatetime, Field, model_validator
from .base import Contract, Identifier, Text

class TableColumn(Contract):
    key: Identifier
    label: Text
    data_type: Literal['text', 'integer', 'decimal', 'boolean', 'date', 'datetime']
    nullable: bool = False

class TableCell(Contract):
    value: str | int | Decimal | bool | date | AwareDatetime | None

class TableRow(Contract):
    cells: tuple[TableCell, ...]
    evidence_ids: tuple[Identifier, ...] = Field(min_length=1)

class ResultTable(Contract):
    table_id: Identifier
    title: Text
    columns: tuple[TableColumn, ...] = Field(min_length=1)
    rows: tuple[TableRow, ...] = ()
    truncated: bool = False
    next_cursor: Identifier | None = None

    @model_validator(mode='before')
    @classmethod
    def restore_wire_types(cls, data):
        if not isinstance(data, dict):
            return data
        data = dict(data)
        columns = data.get('columns', ())
        types = [c.get('data_type') if isinstance(c, dict) else c.data_type for c in columns]
        rows = []
        for row in data.get('rows', ()):
            if not isinstance(row, dict):
                rows.append(row)
                continue
            row = dict(row)
            cells = []
            for i, cell in enumerate(row.get('cells', ())):
                if isinstance(cell, dict):
                    cell = dict(cell)
                    value = cell.get('value')
                    if isinstance(value, str) and i < len(types):
                        if types[i] == 'decimal':
                            cell['value'] = Decimal(value)
                        elif types[i] == 'date':
                            cell['value'] = date.fromisoformat(value)
                        elif types[i] == 'datetime':
                            cell['value'] = datetime.fromisoformat(value.replace('Z', '+00:00'))
                cells.append(cell)
            row['cells'] = cells
            rows.append(row)
        data['rows'] = rows
        return data

    @model_validator(mode='after')
    def validate_shape(self):
        if len({c.key for c in self.columns}) != len(self.columns):
            raise ValueError('Duplicate column key')
        if self.next_cursor and not self.truncated:
            raise ValueError('A continuation cursor requires truncated output')
        for row in self.rows:
            if len(row.cells) != len(self.columns):
                raise ValueError('Row width does not match columns')
            for col, cell in zip(self.columns, row.cells):
                v = cell.value
                if v is None:
                    if not col.nullable:
                        raise ValueError('Null in non-nullable column')
                    continue
                valid = {
                    'text': type(v) is str,
                    'integer': type(v) is int,
                    'decimal': isinstance(v, Decimal) and v.is_finite(),
                    'boolean': type(v) is bool,
                    'date': type(v) is date,
                    'datetime': hasattr(v, 'tzinfo') and v.tzinfo is not None and v.utcoffset() is not None,
                }[col.data_type]
                if not valid:
                    raise ValueError('Cell type does not match column')
        return self
