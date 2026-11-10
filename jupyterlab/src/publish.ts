import {
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';

import {
  Dialog,
  Notification,
  showDialog,
  ToolbarButton
} from '@jupyterlab/apputils';

import { PageConfig, URLExt } from '@jupyterlab/coreutils';

import { INotebookTracker, NotebookPanel } from '@jupyterlab/notebook';

import { ServerConnection } from '@jupyterlab/services';

import { Widget } from '@lumino/widgets';

/**
 * What the server answers for one notebook: the groups the person is in, the
 * name the notebook would get, whether the file has changes git has not seen,
 * and the principal a schedule would run as (empty when there is no single one).
 */
interface IPublishInfo {
  groups: string[];
  name: string;
  uncommitted: boolean;
  runAs: string;
}

/**
 * What the server stored: the version is the immutable object version a
 * schedule pins.
 */
interface IStored {
  name: string;
  group: string;
  version: string;
  sha256: string;
  parameters: unknown[];
}

/**
 * Call one of the extension's routes, joined onto the server's base URL.
 *
 * The request goes through `ServerConnection.makeRequest`, so it carries the
 * page's XSRF token and the user's session — the browser never sees the token
 * the server uses to sign in to Nova.
 */
async function requestAPI<T>(
  settings: ServerConnection.ISettings,
  endpoint: string,
  init: RequestInit = {}
): Promise<T> {
  const [route, query] = endpoint.split('?');
  const url =
    URLExt.join(settings.baseUrl, 'stardelt', route) +
    (query ? `?${query}` : '');
  let response: Response;
  try {
    response = await ServerConnection.makeRequest(url, init, settings);
  } catch (error) {
    throw new Error(`stardelt: the server is not reachable (${String(error)})`);
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.error ?? `${response.status} ${response.statusText}`);
  }
  return body as T;
}

/**
 * The dialog body: the name to publish under, the group, and what the reader
 * should know before pressing Publish.
 */
class PublishBody extends Widget {
  constructor(info: IPublishInfo) {
    super();
    this.addClass('stardelt-PublishDialog');

    const nameLabel = document.createElement('label');
    // eslint-disable-next-line jupyter/no-untranslated-string
    nameLabel.textContent = 'Name';
    this._name = document.createElement('input');
    this._name.type = 'text';
    this._name.value = info.name;

    const groupLabel = document.createElement('label');
    // eslint-disable-next-line jupyter/no-untranslated-string
    groupLabel.textContent = 'Group';
    this._group = document.createElement('select');
    for (const group of info.groups) {
      const option = document.createElement('option');
      option.value = group;
      option.textContent = group;
      this._group.appendChild(option);
    }

    this.node.appendChild(nameLabel);
    this.node.appendChild(this._name);
    this.node.appendChild(groupLabel);
    this.node.appendChild(this._group);

    if (info.runAs) {
      const runAs = document.createElement('p');
      runAs.textContent = `Scheduled runs use the team principal ${info.runAs}, not you.`;
      this.node.appendChild(runAs);
    }

    if (info.uncommitted) {
      const uncommitted = document.createElement('p');
      uncommitted.textContent =
        // eslint-disable-next-line jupyter/no-untranslated-string
        'Uncommitted changes: the published version will not match a commit.';
      this.node.appendChild(uncommitted);
    }
  }

  /**
   * The name the notebook is published under.
   */
  get name(): string {
    return this._name.value.trim();
  }

  /**
   * The group the notebook belongs to.
   */
  get group(): string {
    return this._group.value;
  }

  /**
   * What the dialog resolved with, read before the widget is disposed.
   */
  getValue(): { name: string; group: string } {
    return { name: this.name, group: this.group };
  }

  private readonly _name: HTMLInputElement;
  private readonly _group: HTMLSelectElement;
}

/**
 * Publish the open notebook, from a button on its toolbar.
 *
 * The notebook is saved first, so what is published is what is on screen. The
 * server does the publish with the pod's own identity; the browser only picks
 * the group and the name, and is told the version that was stored.
 */
const publishPlugin: JupyterFrontEndPlugin<void> = {
  id: '@stardelt/jupyterlab:publish',
  description: 'A Publish button on the notebook toolbar.',
  autoStart: true,
  requires: [INotebookTracker],
  activate: (app: JupyterFrontEnd, tracker: INotebookTracker) => {
    const settings = app.serviceManager.serverSettings;

    const publish = async (panel: NotebookPanel): Promise<void> => {
      await panel.context.save();

      let info: IPublishInfo;
      try {
        info = await requestAPI<IPublishInfo>(
          settings,
          'publish-info?path=' + encodeURIComponent(panel.context.path)
        );
      } catch (error) {
        Notification.error(messageOf(error), { autoClose: false });
        return;
      }

      const body = new PublishBody(info);
      const answer = await showDialog({
        // eslint-disable-next-line jupyter/no-untranslated-string
        title: 'Publish to stardelt',
        body,
        buttons: [
          Dialog.cancelButton(),
          // eslint-disable-next-line jupyter/no-untranslated-string
          Dialog.okButton({ label: 'Publish' })
        ]
      });
      if (!answer.button.accept) {
        return;
      }
      const choice = answer.value as { name: string; group: string };

      let stored: IStored;
      try {
        stored = await requestAPI<IStored>(settings, 'publish', {
          method: 'POST',
          body: JSON.stringify({
            path: panel.context.path,
            group: choice.group,
            name: choice.name
          })
        });
      } catch (error) {
        Notification.error(messageOf(error), { autoClose: false });
        return;
      }

      const novaUrl = PageConfig.getOption('stardeltNovaUrl');
      Notification.success(
        `Published ${stored.name} \u00B7 version ${stored.version.slice(0, 8)}`,
        novaUrl
          ? {
              actions: [
                {
                  // eslint-disable-next-line jupyter/no-untranslated-string
                  label: 'Open in Nova',
                  callback: () => {
                    window.open(
                      `${novaUrl}/notebooks/${stored.group}/${encodeURIComponent(
                        stored.name
                      )}`
                    );
                  }
                }
              ]
            }
          : {}
      );
    };

    app.docRegistry.addWidgetExtension('Notebook', {
      createNew: (panel: NotebookPanel) => {
        const button = new ToolbarButton({
          // eslint-disable-next-line jupyter/no-untranslated-string
          label: 'Publish',
          // eslint-disable-next-line jupyter/no-untranslated-string
          tooltip: 'Publish to stardelt',
          onClick: () => {
            void publish(panel);
          }
        });
        panel.toolbar.insertItem(10, 'stardelt-publish', button);
      }
    });
  }
};

/**
 * A message from an unknown thrown value.
 */
function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * The tag a schedule reads its parameter values from.
 */
const PARAMETERS_TAG = 'parameters';

/**
 * Toggle the `parameters` tag on the active cell: a schedule replaces the
 * values in it before it runs the notebook.
 */
const parametersCellPlugin: JupyterFrontEndPlugin<void> = {
  id: '@stardelt/jupyterlab:parameters-cell',
  description: 'Mark the active cell as the parameters cell of a schedule.',
  autoStart: true,
  requires: [INotebookTracker],
  activate: (app: JupyterFrontEnd, tracker: INotebookTracker) => {
    const command = 'stardelt:mark-parameters';
    app.commands.addCommand(command, {
      // eslint-disable-next-line jupyter/no-untranslated-string
      label: 'Mark as parameters cell',
      // eslint-disable-next-line jupyter/no-untranslated-string
      caption: 'Values here are replaced when a schedule runs the notebook.',
      // The command takes no arguments: it reads the active cell.
      describedBy: { args: null },
      isEnabled: () => tracker.activeCell !== null,
      execute: () => {
        const cell = tracker.activeCell;
        if (!cell) {
          return;
        }
        const tags =
          (cell.model.getMetadata('tags') as string[] | undefined) ?? [];
        cell.model.setMetadata(
          'tags',
          tags.includes(PARAMETERS_TAG)
            ? tags.filter(tag => tag !== PARAMETERS_TAG)
            : [...tags, PARAMETERS_TAG]
        );
      }
    });
    app.contextMenu.addItem({
      command,
      selector: '.jp-Notebook .jp-Cell',
      rank: 50
    });
  }
};

export { parametersCellPlugin, publishPlugin };
