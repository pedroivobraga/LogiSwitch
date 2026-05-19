"""Config storage for LogiSwitch (%APPDATA%/LogiSwitch/config.json)."""

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


def _config_dir() -> Path:
    base = os.environ.get('APPDATA') or str(Path.home() / 'AppData' / 'Roaming')
    return Path(base) / 'LogiSwitch'


CONFIG_PATH = _config_dir() / 'config.json'


@dataclass
class Config:
    # Channel (1-based, Easy-Switch button) que ESTE PC ocupa.
    # Usado pra decidir, ao trocar de volta, qual canal pertence aqui.
    my_channel: int = 2

    # Channel (1-based) do OUTRO PC, pra onde mandamos quando empurra na borda.
    target_channel: int = 1

    # Qual lado o OUTRO PC esta fisicamente. Se "right", vigiamos a borda direita.
    other_side: str = "right"  # "left" | "right"

    # Tempo (ms) que o cursor precisa ficar na borda pra disparar.
    hold_ms: int = 80

    # Cooldown apos disparar pra evitar repeticao.
    cooldown_ms: int = 800

    # Pausado? (controlado pelo tray)
    paused: bool = False

    @property
    def target_host_idx(self) -> int:
        """Converte target_channel (1-based) pro host_idx do HID++ (0-based)."""
        return max(0, self.target_channel - 1)

    @property
    def edge(self) -> str:
        """Borda a vigiar = a borda que aponta pro outro PC."""
        return self.other_side


def load() -> Config:
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
            return Config(**{k: v for k, v in data.items() if k in Config.__dataclass_fields__})
        except Exception:
            pass
    return Config()


def save(cfg: Config) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(asdict(cfg), indent=2), encoding='utf-8')
