import os

try:
    from ._version import __version__
except ImportError:
    # Fallback when using the package in dev mode without installing
    # in editable mode with pip. It is highly recommended to install
    # the package from a stable release or in editable mode: https://pip.pypa.io/en/stable/topics/local-project-installs/#editable-installs
    import warnings
    warnings.warn("Importing 'stardelt_jupyterlab' outside a proper installation.")
    __version__ = "dev"


def _jupyter_labextension_paths():
    return [{
        "src": "labextension",
        "dest": "@stardelt/jupyterlab"
    }]


def _jupyter_server_extension_points():
    return [{
        "module": "stardelt_jupyterlab"
    }]


def _load_jupyter_server_extension(server_app):
    """Put the stardelt values the frontend reads into the page config.

    Parameters
    ----------
    server_app: jupyterlab.labapp.LabApp
        JupyterLab application instance
    """
    page_config = server_app.web_app.settings.setdefault('page_config_data', {})
    page_config['stardeltNovaUrl'] = os.environ.get('STARDELT_NOVA_URL', '')
    page_config['stardeltRuntime'] = os.environ.get('STARDELT_RUNTIME', '')
    name = "stardelt_jupyterlab"
    server_app.log.info(f"Registered {name} server extension")
