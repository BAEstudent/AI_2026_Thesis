import logging
from pathlib import Path
from typing import Any, Dict, Optional

import joblib

logger = logging.getLogger(__name__)

class ModelLoader:
    """
    Scans models_dir for serialised model files and loads them into memory.
    """

    def __init__(self, models_dir: Path) -> None:
        self.models_dir = models_dir
        self.models = {}


    def load_all(self) -> None:
        """Discover and load every supported model file in *models_dir*."""
        if not self.models_dir.exists():
            logger.warning(
                "Models directory %s does not exist — no models loaded.",
                self.models_dir,
            )
            return

        for path in self.models_dir.iterdir():
            if path.suffix not in LOADERS:
                continue
            self._load_file(path)

        logger.info("ModelLoader: %d model(s) loaded.", len(self.models))

    def get(self, key: str) -> Optional:
        """Return the model for *key*, or None if not found."""
        model = self.models.get(key)
        if model is None:
            logger.debug("Model '%s' not found in registry.", key)
        return model

    def get_for_table(self, table: str) -> Dict[str, Any]:
        """Return all models whose key starts with *table*."""
        return {k: v for k, v in self.models.items() if k.startswith(f"{table}_")}

    def reload(self) -> None:
        """Hot-reload all models from disk (zero-downtime swap)."""
        new_models: Dict[str, Any] = {}
        for path in self.models_dir.iterdir():
            if path.suffix not in _LOADERS:
                continue
            key = path.stem
            try:
                new_models[key] = _LOADERS[path.suffix](path)
                logger.debug("Reloaded model '%s' from %s", key, path)
            except Exception:
                logger.exception("Failed to reload model from %s", path)
        self.models = new_models
        logger.info("ModelLoader: hot-reload complete — %d model(s).", len(self.models))

    def _load_file(self, path: Path) -> None:
        key = path.stem 
        loader = _LOADERS[path.suffix]
        try:
            self.models[key] = loader(path)
            logger.debug("Loaded model '%s' from %s", key, path)
        except Exception:
            logger.exception("Failed to load model from %s — skipping.", path)
