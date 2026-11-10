import {
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';

import { Notification } from '@jupyterlab/apputils';

import { URLExt } from '@jupyterlab/coreutils';

import { INotebookTracker } from '@jupyterlab/notebook';

import { ServerConnection } from '@jupyterlab/services';

import { Widget } from '@lumino/widgets';

/**
 * The cell a table starts: the magic, then a query of at most a hundred rows.
 *
 * Every name is quoted the way Trino quotes an identifier, so a catalog or a
 * table with a quote in its name is one name and not the end of the string.
 */
export function sqlSnippet(
  catalog: string,
  schema: string,
  table: string
): string {
  const q = (name: string) => `"${name.replace(/"/g, '""')}"`;
  return `%%sql\nSELECT * FROM ${q(catalog)}.${q(schema)}.${q(table)} LIMIT 100`;
}

/**
 * One level of the tree, as the server answers it.
 */
async function load(
  settings: ServerConnection.ISettings,
  query: string
): Promise<string[]> {
  const url = URLExt.join(settings.baseUrl, 'stardelt', 'catalog') + query;
  const response = await ServerConnection.makeRequest(url, {}, settings);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.error ?? `${response.status} ${response.statusText}`);
  }
  return body.catalogs ?? body.schemas ?? body.tables ?? [];
}

/**
 * Which of the three levels a list of names belongs to.
 */
type Level = 'catalog' | 'schema' | 'table';

/**
 * The stardelt Catalog in the left sidebar: catalogs, then schemas, then tables.
 *
 * The server runs every level as the signed-in person, so what appears is what
 * that person may browse; nothing is cached. A level that fails shows the
 * server's sentence and leaves the rest of the panel usable.
 */
class CatalogTree extends Widget {
  constructor(tracker: INotebookTracker, settings: ServerConnection.ISettings) {
    super();
    this._tracker = tracker;
    this._settings = settings;
    this.id = 'stardelt-catalog';
    // The product name, not a sentence: no translation.
    // eslint-disable-next-line jupyter/no-untranslated-string
    this.title.label = 'stardelt Catalog';
    this.addClass('stardelt-Catalog');
    this._list = document.createElement('ul');
    this._list.className = 'stardelt-Catalog-list';
    this.node.appendChild(this._list);
  }

  /**
   * The panel is in a tab: load the catalogs the first time it is shown.
   */
  protected onAfterShow(): void {
    if (this._loaded) {
      return;
    }
    this._loaded = true;
    void this.reload();
  }

  private async reload(): Promise<void> {
    while (this._list.firstChild) {
      this._list.removeChild(this._list.firstChild);
    }
    const names = await this.loadInto(this._list, '');
    if (names) {
      this.addLevel(this._list, names, 'catalog', []);
    }
  }

  /**
   * The names the server answered for `query`, or `null` after writing the
   * server's sentence into `list` as an error line.
   */
  private async loadInto(
    list: HTMLUListElement,
    query: string
  ): Promise<string[] | null> {
    try {
      return await load(this._settings, query);
    } catch (error) {
      const item = document.createElement('li');
      item.className = 'stardelt-Catalog-error';
      item.textContent = error instanceof Error ? error.message : String(error);
      list.appendChild(item);
      return null;
    }
  }

  /**
   * One `<li>` per name; a catalog or a schema opens on click, a table starts
   * a query on double-click.
   *
   * `parent` is the path already opened: `[]` at the top, `[catalog]` under a
   * catalog, `[catalog, schema]` under a schema.
   */
  private addLevel(
    list: HTMLUListElement,
    names: string[],
    level: Level,
    parent: string[]
  ): void {
    for (const name of names) {
      const item = document.createElement('li');
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'stardelt-Catalog-item';
      button.textContent = name;
      item.appendChild(button);
      list.appendChild(item);
      if (level === 'table') {
        button.addEventListener('dblclick', () => {
          this.insertSnippet(parent[0], parent[1], name);
        });
      } else {
        button.addEventListener('click', () => {
          void this.toggle(item, name, level, parent);
        });
      }
    }
  }

  /**
   * Open one level under its item, or close it again when it is already open.
   */
  private async toggle(
    item: HTMLLIElement,
    name: string,
    level: Level,
    parent: string[]
  ): Promise<void> {
    const open = item.querySelector('ul');
    if (open) {
      open.remove();
      return;
    }
    const list = document.createElement('ul');
    list.className = 'stardelt-Catalog-list';
    item.appendChild(list);
    const query =
      level === 'catalog'
        ? `?catalog=${encodeURIComponent(name)}`
        : `?catalog=${encodeURIComponent(parent[0])}&schema=${encodeURIComponent(
            name
          )}`;
    const names = await this.loadInto(list, query);
    if (!names) {
      return;
    }
    if (level === 'catalog') {
      this.addLevel(list, names, 'schema', [name]);
    } else {
      this.addLevel(list, names, 'table', [parent[0], name]);
    }
  }

  /**
   * Append the query for one table to the active code cell.
   *
   * The cell's text is appended to, never replaced: a half-written query is
   * never lost. Outside a notebook, or on a cell that is not code, the panel
   * says what to do instead of throwing.
   */
  private insertSnippet(catalog: string, schema: string, table: string): void {
    const cell = this._tracker.activeCell;
    if (!cell || cell.model.type !== 'code') {
      Notification.warning('Open a notebook and click a code cell first.');
      return;
    }
    const snippet = sqlSnippet(catalog, schema, table);
    const current = cell.model.sharedModel.getSource();
    cell.model.sharedModel.setSource(
      current ? current + '\n\n' + snippet : snippet
    );
  }

  private readonly _tracker: INotebookTracker;
  private readonly _settings: ServerConnection.ISettings;
  private readonly _list: HTMLUListElement;
  private _loaded = false;
}

/**
 * Put the catalog tree in the left sidebar.
 */
const catalogPlugin: JupyterFrontEndPlugin<void> = {
  id: '@stardelt/jupyterlab:catalog',
  description: 'The stardelt Catalog tree in the left sidebar.',
  autoStart: true,
  requires: [INotebookTracker],
  activate: (app: JupyterFrontEnd, tracker: INotebookTracker) => {
    app.shell.add(
      new CatalogTree(tracker, ServerConnection.makeSettings()),
      'left',
      { rank: 600 }
    );
  }
};

export { catalogPlugin };
