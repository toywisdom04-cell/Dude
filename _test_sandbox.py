"""Test-only config and persistence isolation, shared by unittest and pytest.

No pytest dependency and no application startup: install before importing tests.
Explicit database paths supplied by individual tests remain under their control.
"""

from contextlib import ExitStack, contextmanager
from copy import deepcopy
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch


_STORE_GLOBALS = {
    "procedure_store": ("_store", "_store_db_path"),
    "pattern_store": ("_store", "_store_db_path"),
    "procedure_adaptation": ("_adaptation_store",),
    "procedure_composition": ("_composition_store",),
}


@contextmanager
def _isolated_store_singletons():
    # Do not import the orchestrator just to reset its caches. A fresh process
    # imports it only after the sandbox config has been installed.
    saved = {}
    names = {
        f"{prefix}.orchestrator.{module}": attributes
        for prefix in ("core", "dude.core")
        for module, attributes in _STORE_GLOBALS.items()
    }
    for name, attributes in names.items():
        module = sys.modules.get(name)
        if module is not None:
            saved[name] = {attr: getattr(module, attr) for attr in attributes}
            for attr in attributes:
                setattr(module, attr, None)
    try:
        yield
    finally:
        for name, attributes in names.items():
            module = sys.modules.get(name)
            if module is None:
                continue
            for attr in attributes:
                previous = saved.get(name, {}).get(attr)
                current = getattr(module, attr, None)
                # ProcedureStore may hold a private in-memory keeper connection.
                if attr == "_store" and current is not previous and name.endswith(".procedure_store"):
                    if current is not None:
                        current.close()
                setattr(module, attr, previous)


@contextmanager
def runtime_sandbox():
    """Yield an independent Config, then restore state and remove temporary data."""
    from core import config as config_module

    with TemporaryDirectory(prefix="dude-unit-runtime-") as directory:
        root = Path(directory)
        data_dir = root / "data"
        for name in ("screenshots", "tts_cache", "models", "logs"):
            (data_dir / name).mkdir(parents=True, exist_ok=True)

        # Config.__init__ reads config.local.json; never call it in the harness.
        cfg = object.__new__(config_module.Config)
        cfg._data = deepcopy(config_module.DEFAULTS)
        cfg.root = str(root)
        cfg.data_dir = str(data_dir)
        cfg._data["data_dir"] = cfg.data_dir
        cfg._data["memory"] = {"data_dir": cfg.data_dir}
        # Config.get accepts nested arguments, while several stores pass one
        # dotted argument. Support both in test data without changing Config.
        cfg._data["memory.data_dir"] = cfg.data_dir
        cfg._data["vault_path"] = ""
        cfg._data["brain"]["providers"] = []
        cfg._vault_cache = ""
        cfg._vault_loaded = True

        with ExitStack() as stack:
            for name, value in {
                "_config": cfg,
                "ROOT": cfg.root,
                "CONFIG_PATH": str(root / "config.json"),
                "LOCAL_CONFIG_PATH": str(root / "config.local.json"),
                # Config key lookup must not consult the user's provider env.
                "ENV_KEYS": {},
            }.items():
                stack.enter_context(patch.object(config_module, name, value))
            stack.enter_context(_isolated_store_singletons())
            yield cfg
