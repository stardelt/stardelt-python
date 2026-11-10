# Changelog

<!-- <START NEW CHANGELOG ENTRY> -->

## 0.2.0

- **Publish** button on the notebook toolbar: saves the notebook, asks the server
  for the caller's groups, and publishes through `stardelt.publish` in the pod —
  the browser never holds a token. The notification offers "Open in Nova".
- **Mark as parameters cell** in a cell's context menu toggles the `parameters`
  tag, the cell a schedule replaces values in.
- New server routes `/stardelt/publish-info` and `/stardelt/publish`
  (`stardelt_jupyterlab/handlers.py`).

<!-- <END NEW CHANGELOG ENTRY> -->
