"""Autostart via HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run.

Nao precisa de admin - escreve no hive do usuario corrente."""

import sys
import winreg
from pathlib import Path

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "LogiSwitch"


def _python_exe() -> str:
    """Prefere pythonw.exe (sem console) quando disponivel."""
    exe = sys.executable
    if exe.endswith('python.exe'):
        candidate = exe[:-len('python.exe')] + 'pythonw.exe'
        if Path(candidate).exists():
            return candidate
    return exe


def _command() -> str:
    """Linha de comando a registrar no Run."""
    app_path = str((Path(__file__).resolve().parent / 'app.py'))
    return f'"{_python_exe()}" "{app_path}"'


def is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ) as k:
            value, _ = winreg.QueryValueEx(k, _VALUE_NAME)
            return bool(value)
    except (FileNotFoundError, OSError):
        return False


def enable() -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, _VALUE_NAME, 0, winreg.REG_SZ, _command())


def disable() -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, _VALUE_NAME)
    except FileNotFoundError:
        pass


def toggle() -> bool:
    """Retorna o estado final (True = enabled)."""
    if is_enabled():
        disable()
        return False
    enable()
    return True
