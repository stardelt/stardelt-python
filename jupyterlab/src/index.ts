import {
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';

import { IThemeManager } from '@jupyterlab/apputils';

import { PageConfig } from '@jupyterlab/coreutils';

import { Widget } from '@lumino/widgets';

import { parametersCellPlugin, publishPlugin } from './publish';

/**
 * Register the stardelt themes: Graphite (dark, the default) and Silver (light).
 */
const themePlugin: JupyterFrontEndPlugin<void> = {
  id: '@stardelt/jupyterlab:theme',
  description: 'The stardelt Graphite and Silver themes.',
  autoStart: true,
  requires: [IThemeManager],
  activate: (app: JupyterFrontEnd, manager: IThemeManager) => {
    manager.register({
      name: 'stardelt Graphite',
      isLight: false,
      load: () => manager.loadCSS('@stardelt/jupyterlab/graphite.css'),
      unload: () => Promise.resolve(undefined)
    });
    manager.register({
      name: 'stardelt Silver',
      isLight: true,
      load: () => manager.loadCSS('@stardelt/jupyterlab/silver.css'),
      unload: () => Promise.resolve(undefined)
    });
  }
};

/**
 * A slim bar in the JupyterLab top area: a link back to Nova, the user, and the
 * runtime label the server put into the page config.
 */
class StardeltTopBar extends Widget {
  constructor() {
    super();
    this.id = 'stardelt-top-bar';
    this.addClass('stardelt-TopBar');

    const novaUrl = PageConfig.getOption('stardeltNovaUrl');
    if (novaUrl) {
      const back = document.createElement('a');
      back.className = 'stardelt-TopBar-nova';
      back.href = novaUrl;
      // The product name, not a sentence: no translation.
      // eslint-disable-next-line jupyter/no-untranslated-string
      back.textContent = '\u25C2 stardelt';
      this.node.appendChild(back);
    }

    const user = PageConfig.getOption('hubUser');
    if (user) {
      const userNode = document.createElement('span');
      userNode.className = 'stardelt-TopBar-user';
      userNode.textContent = user;
      this.node.appendChild(userNode);
    }

    const runtime = PageConfig.getOption('stardeltRuntime');
    if (runtime) {
      const runtimeNode = document.createElement('span');
      runtimeNode.className = 'stardelt-TopBar-runtime';
      runtimeNode.textContent = `runtime ${runtime}`;
      this.node.appendChild(runtimeNode);
    }
  }
}

/**
 * Add the stardelt bar to the top area.
 */
const topBarPlugin: JupyterFrontEndPlugin<void> = {
  id: '@stardelt/jupyterlab:top-bar',
  description: 'The stardelt bar in the JupyterLab top area.',
  autoStart: true,
  activate: (app: JupyterFrontEnd) => {
    app.shell.add(new StardeltTopBar(), 'top', { rank: 1000 });
  }
};

export default [themePlugin, topBarPlugin, publishPlugin, parametersCellPlugin];
