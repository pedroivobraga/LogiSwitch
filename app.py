"""LogiSwitch tray app. Coordena watch_loop em thread + tray + settings UI."""

import sys
import threading
import time

from PIL import Image, ImageDraw
import pystray

import config as config_mod
import logi_switch as ls
import settings_ui


def make_icon_image(active: bool, channel: int) -> Image.Image:
    """Cria um icone 64x64 com cor de estado + numero do canal."""
    size = 64
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    bg = (60, 180, 75, 255) if active else (130, 130, 130, 255)
    draw.ellipse([2, 2, size - 2, size - 2], fill=bg)
    # Numero do canal (com fonte padrao - pequeno mas legivel)
    text = str(channel)
    try:
        # Tenta uma fonte basica via PIL default
        from PIL import ImageFont
        font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        draw.text(((size - tw) / 2, (size - th) / 2 - 2), text, fill='white', font=font)
    except Exception:
        draw.text((size / 2 - 4, size / 2 - 6), text, fill='white')
    return img


class App:
    def __init__(self):
        self.cfg = config_mod.load()
        self.stop_event = threading.Event()
        self.targets = []
        self.watch_thread = None
        self.icon = None
        self.last_event_text = "iniciando..."

    # ----- watch lifecycle -----

    def start_watch(self):
        self.targets = ls.discover_targets(verbose=False)
        if not self.targets:
            self.last_event_text = "nenhum device encontrado"
            return
        self.watch_thread = threading.Thread(
            target=ls.watch_loop,
            args=(self.targets, self.cfg, self.stop_event),
            kwargs={'on_switch': self._on_switch, 'on_status': self._on_status},
            daemon=True,
        )
        self.watch_thread.start()
        self.last_event_text = f"vigiando {self.cfg.edge}"

    def stop_watch(self):
        self.stop_event.set()
        if self.watch_thread:
            self.watch_thread.join(timeout=2)
        ls.close_targets(self.targets)
        self.targets = []
        self.watch_thread = None

    # ----- callbacks do watch_loop -----

    def _on_switch(self, host, results):
        ts = time.strftime('%H:%M:%S')
        ok_count = sum(1 for _, ok, _ in results if ok)
        self.last_event_text = f"{ts} -> host {host} ({ok_count}/{len(results)} ok)"
        self._refresh_icon()

    def _on_status(self, msg):
        self.last_event_text = f"{time.strftime('%H:%M:%S')} {msg}"

    # ----- tray actions -----

    def toggle_pause(self, _icon=None, _item=None):
        self.cfg.paused = not self.cfg.paused
        config_mod.save(self.cfg)
        self._refresh_icon()

    def open_settings(self, _icon=None, _item=None):
        # tkinter precisa rodar na propria thread (mainloop bloqueia)
        def runner():
            settings_ui.open_settings(self.cfg, on_save=self._on_settings_saved)
        threading.Thread(target=runner, daemon=True).start()

    def _on_settings_saved(self):
        # cfg ja foi mutado in-place pelo settings_ui; watch_loop ve no proximo ciclo
        self.last_event_text = f"{time.strftime('%H:%M:%S')} config atualizada (hot-reload)"
        self._refresh_icon()

    def rediscover(self, _icon=None, _item=None):
        self.stop_watch()
        self.stop_event = threading.Event()
        self.start_watch()
        self._refresh_icon()

    def quit_app(self, _icon=None, _item=None):
        self.stop_watch()
        if self.icon:
            self.icon.stop()

    # ----- menu / icon -----

    def _status_text(self):
        state = "pausado" if self.cfg.paused else "ativo"
        return f"LogiSwitch ({state}) - canal {self.cfg.my_channel}\n{self.last_event_text}"

    def _build_menu(self):
        return pystray.Menu(
            pystray.MenuItem(lambda _: self._status_text(), None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                lambda _: "Retomar" if self.cfg.paused else "Pausar",
                self.toggle_pause,
            ),
            pystray.MenuItem("Configuracoes...", self.open_settings),
            pystray.MenuItem("Re-detectar devices", self.rediscover),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Sair", self.quit_app),
        )

    def _refresh_icon(self):
        if self.icon:
            self.icon.icon = make_icon_image(
                active=not self.cfg.paused,
                channel=self.cfg.my_channel,
            )

    def run(self):
        self.start_watch()
        self.icon = pystray.Icon(
            'LogiSwitch',
            icon=make_icon_image(active=not self.cfg.paused, channel=self.cfg.my_channel),
            title=self._status_text(),
            menu=self._build_menu(),
        )
        try:
            self.icon.run()
        finally:
            self.stop_watch()


def main():
    if sys.platform != 'win32':
        print("Apenas Windows.", file=sys.stderr)
        sys.exit(1)
    App().run()


if __name__ == '__main__':
    main()
