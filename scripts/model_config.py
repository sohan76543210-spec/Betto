"""Production model configuration loader.

Keeps tunable model parameters in data/model_config.json while providing a
validated, safe fallback when the file is missing or malformed.
"""
import json
import os
import tempfile

DEFAULT_CONFIG = {
    "h2h_weight": 0.15,
    "form_weight": 0.40,
    "venue_weight": 0.45,
    "half_life": 4,
    "rho": -0.11,
}

CONFIG_PATH = os.environ.get(
    "PREDICTOR_MODEL_CONFIG_PATH",
    os.path.join(os.path.dirname(__file__), "..", "data", "model_config.json"),
)


def _validate(cfg):
    out = dict(DEFAULT_CONFIG)
    if not isinstance(cfg, dict):
        return out

    try:
        weights = [float(cfg.get(k, out[k])) for k in ("h2h_weight", "form_weight", "venue_weight")]
        if all(x >= 0 for x in weights) and sum(weights) > 0:
            total = sum(weights)
            out["h2h_weight"], out["form_weight"], out["venue_weight"] = [x / total for x in weights]
    except (TypeError, ValueError):
        pass

    try:
        hl = float(cfg.get("half_life", out["half_life"]))
        if hl > 0:
            out["half_life"] = hl if not hl.is_integer() else int(hl)
    except (TypeError, ValueError):
        pass

    try:
        rho = float(cfg.get("rho", out["rho"]))
        if -1.0 <= rho <= 1.0:
            out["rho"] = rho
    except (TypeError, ValueError):
        pass
    return out


def load_config(path=None):
    path = path or CONFIG_PATH
    try:
        with open(path, "r", encoding="utf-8") as f:
            return _validate(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return dict(DEFAULT_CONFIG)


def write_config(config, path=None):
    path = path or CONFIG_PATH
    cfg = _validate(config)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="model_config_", suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
