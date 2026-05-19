"""Janela de configuracao tkinter pra LogiSwitch."""

import tkinter as tk
from tkinter import ttk

import config as config_mod


def open_settings(cfg: config_mod.Config, on_save):
    """Abre janela modal. Ao salvar, atualiza cfg in-place e chama on_save()."""
    win = tk.Tk()
    win.title("LogiSwitch - Configuracoes")
    win.resizable(False, False)
    try:
        win.attributes('-topmost', True)
    except tk.TclError:
        pass

    pad = {'padx': 10, 'pady': 6}
    row = 0

    ttk.Label(win, text="Canal Easy-Switch deste PC:").grid(row=row, column=0, sticky='w', **pad)
    my_var = tk.IntVar(value=cfg.my_channel)
    my_combo = ttk.Combobox(win, textvariable=my_var, values=[1, 2, 3], width=5, state='readonly')
    my_combo.grid(row=row, column=1, sticky='w', **pad)
    row += 1

    ttk.Label(win, text="Canal do outro PC (alvo da troca):").grid(row=row, column=0, sticky='w', **pad)
    tgt_var = tk.IntVar(value=cfg.target_channel)
    tgt_combo = ttk.Combobox(win, textvariable=tgt_var, values=[1, 2, 3], width=5, state='readonly')
    tgt_combo.grid(row=row, column=1, sticky='w', **pad)
    row += 1

    ttk.Label(win, text="Outro PC esta a:").grid(row=row, column=0, sticky='w', **pad)
    side_var = tk.StringVar(value=cfg.other_side)
    side_combo = ttk.Combobox(win, textvariable=side_var,
                              values=['left', 'right'], width=8, state='readonly')
    side_combo.grid(row=row, column=1, sticky='w', **pad)
    row += 1

    ttk.Separator(win, orient='horizontal').grid(row=row, column=0, columnspan=2, sticky='ew', pady=8)
    row += 1

    ttk.Label(win, text="Hold na borda (ms):").grid(row=row, column=0, sticky='w', **pad)
    hold_var = tk.IntVar(value=cfg.hold_ms)
    ttk.Spinbox(win, from_=20, to=2000, increment=10, textvariable=hold_var, width=8).grid(
        row=row, column=1, sticky='w', **pad)
    row += 1

    ttk.Label(win, text="Cooldown apos troca (ms):").grid(row=row, column=0, sticky='w', **pad)
    cd_var = tk.IntVar(value=cfg.cooldown_ms)
    ttk.Spinbox(win, from_=100, to=5000, increment=100, textvariable=cd_var, width=8).grid(
        row=row, column=1, sticky='w', **pad)
    row += 1

    ttk.Label(win, text="Keep-alive (s):").grid(row=row, column=0, sticky='w', **pad)
    ka_var = tk.DoubleVar(value=cfg.keepalive_s)
    ttk.Spinbox(win, from_=1.0, to=30.0, increment=0.5, textvariable=ka_var, width=8).grid(
        row=row, column=1, sticky='w', **pad)
    row += 1

    status_lbl = ttk.Label(win, text="", foreground='gray')
    status_lbl.grid(row=row, column=0, columnspan=2, sticky='w', padx=10)
    row += 1

    def do_save():
        # Validacao basica
        if my_var.get() == tgt_var.get():
            status_lbl.config(text="O canal deste PC e do outro nao podem ser iguais.",
                              foreground='red')
            return
        cfg.my_channel = my_var.get()
        cfg.target_channel = tgt_var.get()
        cfg.other_side = side_var.get()
        cfg.hold_ms = hold_var.get()
        cfg.cooldown_ms = cd_var.get()
        cfg.keepalive_s = ka_var.get()
        config_mod.save(cfg)
        on_save()
        status_lbl.config(text="Salvo. Hot-reload aplicado.", foreground='green')

    def do_close():
        win.destroy()

    btn_frame = ttk.Frame(win)
    btn_frame.grid(row=row, column=0, columnspan=2, pady=10)
    ttk.Button(btn_frame, text="Salvar", command=do_save).pack(side='left', padx=4)
    ttk.Button(btn_frame, text="Fechar", command=do_close).pack(side='left', padx=4)

    win.protocol("WM_DELETE_WINDOW", do_close)
    win.mainloop()


if __name__ == '__main__':
    cfg = config_mod.load()
    open_settings(cfg, lambda: print("Saved:", cfg))
