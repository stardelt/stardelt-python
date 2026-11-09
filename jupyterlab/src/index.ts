import {
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';

import { requestAPI } from './request';

/**
 * Initialization data for the @stardelt/jupyterlab extension.
 */
const plugin: JupyterFrontEndPlugin<void> = {
  id: '@stardelt/jupyterlab:plugin',
  description: 'stardelt inside JupyterLab: theme, top bar, catalog, publish',
  autoStart: true,
  activate: (app: JupyterFrontEnd) => {
    console.log('JupyterLab extension @stardelt/jupyterlab is activated!');

    requestAPI<any>('hello', app.serviceManager.serverSettings)
      .then(data => {
        console.log(data);
      })
      .catch(reason => {
        console.error(
          `The stardelt_jupyterlab server extension appears to be missing.\n${reason}`
        );
      });
  }
};

export default plugin;
