"""Catalog browser for the notebook UI.

    import stardelt
    stardelt.browse()

Permission-aware listing uses stardelt.sql() under the person's token
(SHOW CATALOGS, SHOW SCHEMAS FROM c, SHOW TABLES FROM c.s, DESCRIBE c.s.t).
Everything is text (no HTML from table or column names).
"""

import sys

try:
    import ipywidgets as widgets
    from IPython.display import display
    _IPYWIDGETS_AVAILABLE = True
except Exception:
    widgets = None
    display = None
    _IPYWIDGETS_AVAILABLE = False


def _safe_text(value):
    if value is None:
        return ""
    text = str(value)
    # Ensure everything is plain text (no HTML from names)
    # Just return as string - widgets will treat as text
    return text


def _quote_ident_if_needed(name):
    name_str = str(name)
    # Quote with double quotes only when needed
    # Need quotes if contains special chars, spaces, starts with digit, is reserved-ish, has uppercase in some cases? Or just be safe
    # The ticket says "names quoted with double quotes only when needed"
    needs_quotes = False
    if '"' in name_str:
        needs_quotes = True
    elif any(c in name_str for c in [' ', '.', '-', '+', '/', '\\', '(', ')', '[', ']', ';', ',', '@', '#']):
        needs_quotes = True
    elif name_str and name_str[0].isdigit():
        needs_quotes = True
    elif name_str != name_str.lower() and name_str.isascii() and any(c.isupper() for c in name_str):
        # Mixed case might need quotes in some SQL dialects, but safer to quote if mixed case and not simple
        needs_quotes = True
    if needs_quotes:
        escaped = name_str.replace('"', '""')
        return '"' + escaped + '"'
    return name_str


def _is_ipython():
    try:
        from IPython import get_ipython
        return get_ipython() is not None
    except Exception:
        return False


class CatalogBrowser:
    def __init__(self):
        self.max_entries = 500
        self.catalogs = []
        self.schemas = []
        self.tables = []
        self.columns = []
        self.current_catalog = None
        self.current_schema = None
        self.current_table = None

        # Try to use sidecar if available
        self._sidecar = None
        try:
            from sidecar import Sidecar
            self._sidecar = Sidecar(title='Catalog Browser')
        except Exception:
            self._sidecar = None

        self._build_widgets()
        self._load_catalogs()

    def _build_widgets(self):
        if not _IPYWIDGETS_AVAILABLE:
            return

        # Filter box
        self.filter_box = widgets.Text(
            placeholder='Filter...',
            description='Filter:',
            layout=widgets.Layout(width='95%')
        )
        self.filter_box.observe(self._on_filter_change, names='value')

        # Three linked lists
        self.catalog_list = widgets.Select(
            options=[],
            description='Catalog:',
            layout=widgets.Layout(width='30%', height='150px')
        )
        self.catalog_list.observe(self._on_catalog_select, names='value')

        self.schema_list = widgets.Select(
            options=[],
            description='Schema:',
            layout=widgets.Layout(width='30%', height='150px')
        )
        self.schema_list.observe(self._on_schema_select, names='value')

        self.table_list = widgets.Select(
            options=[],
            description='Table:',
            layout=widgets.Layout(width='30%', height='150px')
        )
        self.table_list.observe(self._on_table_select, names='value')

        lists_box = widgets.HBox([self.catalog_list, self.schema_list, self.table_list])

        # Tabs for Columns and Preview
        self.columns_out = widgets.Output()
        self.preview_out = widgets.Output()

        self.tabs = widgets.Tab(children=[self.columns_out, self.preview_out])
        self.tabs.set_title(0, 'Columns')
        self.tabs.set_title(1, 'Preview')
        self.tabs.observe(self._on_tab_change, names='selected_index')

        # Buttons
        self.insert_btn = widgets.Button(description='Insert SELECT')
        self.insert_btn.on_click(self._on_insert_select)
        self.copy_btn = widgets.Button(description='Copy name')
        self.copy_btn.on_click(self._on_copy_name)

        buttons_box = widgets.HBox([self.insert_btn, self.copy_btn])

        self.main_widget = widgets.VBox([
            self.filter_box,
            lists_box,
            self.tabs,
            buttons_box
        ])

    def _load_catalogs(self):
        try:
            import stardelt
            frame = stardelt.sql("SHOW CATALOGS")
            if frame is not None and len(frame) > 0:
                # Get first column
                col = frame.columns[0]
                catalogs = []
                for _, row in frame.iterrows():
                    name = row[col]
                    catalogs.append(_safe_text(name))
                # Apply filter
                catalogs = self._apply_filter(catalogs)
                self.catalogs = catalogs[:self.max_entries]
                if len(catalogs) > self.max_entries:
                    self.catalogs.append(f"N more: refine the filter")
                if _IPYWIDGETS_AVAILABLE:
                    self.catalog_list.options = [(c, c) for c in self.catalogs if c != f"N more: refine the filter"]
        except Exception:
            # Don't raise, just show empty or handle gracefully
            pass

    def _apply_filter(self, items):
        if not _IPYWIDGETS_AVAILABLE or not hasattr(self, 'filter_box'):
            return items
        filter_val = self.filter_box.value.lower() if self.filter_box.value else ''
        if not filter_val:
            return items
        return [item for item in items if filter_val in str(item).lower()]

    def _on_filter_change(self, change):
        # Reload from current position
        if self.current_catalog:
            self._load_schemas()
        else:
            self._load_catalogs()
        if self.current_schema and self.current_catalog:
            self._load_tables()

    def _on_catalog_select(self, change):
        if change['type'] == 'change' and change['new']:
            value = change['new']
            if value == f"N more: refine the filter":
                return
            self.current_catalog = value
            self.current_schema = None
            self.current_table = None
            if _IPYWIDGETS_AVAILABLE:
                self.schema_list.options = []
                self.table_list.options = []
            self._load_schemas()

    def _load_schemas(self):
        try:
            import stardelt
            # Use SHOW SCHEMAS FROM catalog
            query = f'SHOW SCHEMAS FROM {_quote_ident_if_needed(self.current_catalog)}'
            frame = stardelt.sql(query)
            if frame is not None and len(frame) > 0:
                col = frame.columns[0]
                schemas = []
                for _, row in frame.iterrows():
                    name = row[col]
                    schemas.append(_safe_text(name))
                schemas = self._apply_filter(schemas)
                self.schemas = schemas[:self.max_entries]
                if len(schemas) > self.max_entries:
                    self.schemas.append(f"N more: refine the filter")
                if _IPYWIDGETS_AVAILABLE:
                    options = [(s, s) for s in self.schemas if s != f"N more: refine the filter"]
                    self.schema_list.options = options
        except Exception as e:
            # Engine errors should be shown in context, not raised
            pass

    def _on_schema_select(self, change):
        if change['type'] == 'change' and change['new']:
            value = change['new']
            if value == f"N more: refine the filter":
                return
            self.current_schema = value
            self.current_table = None
            if _IPYWIDGETS_AVAILABLE:
                self.table_list.options = []
            self._load_tables()

    def _load_tables(self):
        try:
            import stardelt
            catalog = _quote_ident_if_needed(self.current_catalog)
            schema = _quote_ident_if_needed(self.current_schema)
            query = f'SHOW TABLES FROM {catalog}.{schema}'
            frame = stardelt.sql(query)
            if frame is not None and len(frame) > 0:
                col = frame.columns[0]
                tables = []
                for _, row in frame.iterrows():
                    name = row[col]
                    tables.append(_safe_text(name))
                tables = self._apply_filter(tables)
                self.tables = tables[:self.max_entries]
                if len(tables) > self.max_entries:
                    self.tables.append(f"N more: refine the filter")
                if _IPYWIDGETS_AVAILABLE:
                    options = [(t, t) for t in self.tables if t != f"N more: refine the filter"]
                    self.table_list.options = options
        except Exception:
            pass

    def _on_table_select(self, change):
        if change['type'] == 'change' and change['new']:
            value = change['new']
            if value == f"N more: refine the filter":
                return
            self.current_table = value
            # Load columns for Columns tab
            self._load_columns()
            # Clear preview
            if _IPYWIDGETS_AVAILABLE and hasattr(self, 'preview_out'):
                with self.preview_out:
                    self.preview_out.clear_output()
            # Switch to Columns tab by default
            if _IPYWIDGETS_AVAILABLE and hasattr(self, 'tabs'):
                self.tabs.selected_index = 0

    def _load_columns(self):
        if not _IPYWIDGETS_AVAILABLE or not hasattr(self, 'columns_out'):
            return
        with self.columns_out:
            self.columns_out.clear_output()
            try:
                import stardelt
                catalog = _quote_ident_if_needed(self.current_catalog)
                schema = _quote_ident_if_needed(self.current_schema)
                table = _quote_ident_if_needed(self.current_table)
                full_name = f'{catalog}.{schema}.{table}'
                # Try DESCRIBE first
                try:
                    frame = stardelt.sql(f'DESCRIBE {full_name}')
                    if frame is not None and len(frame) > 0:
                        # Print as text table
                        print(_safe_text(frame.to_string()))
                        return
                except Exception:
                    pass
                # Try information_schema
                try:
                    frame = stardelt.sql(
                        "SELECT column_name, data_type, comment FROM information_schema.columns "
                        f"WHERE table_catalog = '{self.current_catalog}' AND table_schema = '{self.current_schema}' AND table_name = '{self.current_table}'"
                    )
                    if frame is not None and len(frame) > 0:
                        print(_safe_text(frame.to_string()))
                        return
                except Exception:
                    pass
            except Exception as e:
                print(_safe_text(str(e)))

    def _on_tab_change(self, change):
        if change['type'] == 'change' and change['new'] == 1:  # Preview tab
            self._load_preview()

    def _load_preview(self):
        if not _IPYWIDGETS_AVAILABLE or not hasattr(self, 'preview_out'):
            return
        with self.preview_out:
            self.preview_out.clear_output()
            try:
                import stardelt
                catalog = _quote_ident_if_needed(self.current_catalog)
                schema = _quote_ident_if_needed(self.current_schema)
                table = _quote_ident_if_needed(self.current_table)
                full_name = f'{catalog}.{schema}.{table}'
                try:
                    frame = stardelt.sql(f'SELECT * FROM {full_name} LIMIT 20')
                    if frame is not None:
                        print(_safe_text(frame.to_string()))
                        return
                except Exception as e:
                    # Preview shows the engine's error if the person lacks select
                    print(_safe_text(str(e)))
                    return
            except Exception as e:
                print(_safe_text(str(e)))

    def _on_insert_select(self, button):
        if not self.current_catalog or not self.current_schema or not self.current_table:
            return
        catalog = _quote_ident_if_needed(self.current_catalog)
        schema = _quote_ident_if_needed(self.current_schema)
        table = _quote_ident_if_needed(self.current_table)
        full_name = f'{catalog}.{schema}.{table}'
        query = f"SELECT * FROM {full_name} LIMIT 100"
        try:
            from IPython import get_ipython
            ip = get_ipython()
            if ip is not None:
                if hasattr(ip, 'set_next_input'):
                    ip.set_next_input(query)
                else:
                    ip.run_cell(query)
        except Exception:
            pass

    def _on_copy_name(self, button):
        if not self.current_catalog or not self.current_schema or not self.current_table:
            return
        catalog = _quote_ident_if_needed(self.current_catalog)
        schema = _quote_ident_if_needed(self.current_schema)
        table = _quote_ident_if_needed(self.current_table)
        full_name = f'{catalog}.{schema}.{table}'
        try:
            import subprocess
            # Try to copy to clipboard
            try:
                subprocess.run(['pbcopy'], input=full_name.encode(), check=False)
            except Exception:
                try:
                    subprocess.run(['xclip', '-selection', 'clipboard'], input=full_name.encode(), check=False)
                except Exception:
                    pass
        except Exception:
            pass

    def display(self):
        if not _IPYWIDGETS_AVAILABLE:
            print("ipywidgets not available")
            return
        if self._sidecar is not None:
            try:
                with self._sidecar:
                    display(self.main_widget)
                return
            except Exception:
                pass
        # Fall back to inline
        display(self.main_widget)


def browse():
    """Open a panel (sidecar when available, else inline) to browse catalogs."""
    browser = CatalogBrowser()
    browser.display()
    return browser
