#!/usr/bin/env python3
"""Минималистичное окно с лимитами Claude: сессия (5 ч) и неделя.

Берёт токен из входа в Claude Code и раз в минуту спрашивает
текущее использование. Окно без рамки, поверх остальных.

  • перетаскивание — зажать мышью в любом месте
  • клик по нижней строке — обновить сейчас
  • × или Esc — закрыть
"""
import json
import os
import platform
import subprocess
import threading
import tkinter as tk
import urllib.error
import urllib.request
from datetime import datetime, timezone

API_URL = "https://api.anthropic.com/api/oauth/usage"
REFRESH_MS = 60_000
WIDTH = 248

BG = "#141414"
FG = "#ececec"
DIM = "#7c7c7c"
TRACK = "#2a2a2a"
LOW = "#e6e6e6"
MID = "#e3a64f"
HIGH = "#e5604d"

FONT = "Segoe UI" if platform.system() == "Windows" else "Helvetica"

# Ключ в ответе API → подпись. Строки без данных не показываются.
ROWS = [
    ("five_hour", "Сессия · 5 ч"),
    ("seven_day", "Неделя"),
    ("seven_day_opus", "Неделя · Opus"),
    ("seven_day_sonnet", "Неделя · Sonnet"),
]

WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def read_token():
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if token:
        return token

    raw = None
    if platform.system() == "Darwin":
        try:
            raw = subprocess.run(
                ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
        except Exception:
            raw = None

    if not raw:
        base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
        path = os.path.join(base, ".credentials.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                raw = f.read()

    if not raw:
        raise RuntimeError("Войди в Claude Code (claude → /login)")
    return json.loads(raw)["claudeAiOauth"]["accessToken"]


def fetch_usage():
    req = urllib.request.Request(API_URL, headers={
        "Authorization": f"Bearer {read_token()}",
        "anthropic-beta": "oauth-2025-04-20",
        "Content-Type": "application/json",
        "User-Agent": "claude-limits-widget",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise RuntimeError("Токен устарел — запусти Claude Code") from e
        raise RuntimeError(f"Ошибка API: {e.code}") from e
    except urllib.error.URLError as e:
        raise RuntimeError("Нет связи с сервером") from e


def parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def format_reset(value):
    when = parse_time(value)
    if when is None:
        return ""
    minutes = int((when - datetime.now(timezone.utc)).total_seconds() // 60)
    if minutes <= 0:
        return "сброс сейчас"
    if minutes < 60:
        return f"сброс через {minutes} мин"
    if minutes < 24 * 60:
        return f"сброс через {minutes // 60} ч {minutes % 60:02d} мин"
    local = when.astimezone()
    return f"сброс {WEEKDAYS[local.weekday()]} {local:%H:%M}"


def bar_color(pct):
    if pct >= 90:
        return HIGH
    if pct >= 70:
        return MID
    return LOW


class LimitsWindow:
    def __init__(self, root):
        self.root = root
        self.result = None
        self.loading = False
        self.drag = (0, 0)

        root.title("Лимиты Claude")
        root.configure(bg=BG)
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        try:
            root.attributes("-alpha", 0.96)
        except tk.TclError:
            pass

        head = tk.Frame(root, bg=BG)
        head.pack(fill="x", padx=16, pady=(12, 4))
        tk.Label(head, text="CLAUDE", bg=BG, fg=DIM, font=(FONT, 8, "bold")).pack(side="left")
        close = tk.Label(head, text="×", bg=BG, fg=DIM, font=(FONT, 12), cursor="hand2")
        close.pack(side="right")
        close.bind("<Button-1>", lambda e: root.destroy())

        self.body = tk.Frame(root, bg=BG)
        self.body.pack(fill="x", padx=16)

        self.status = tk.Label(root, text="загрузка…", bg=BG, fg=DIM, font=(FONT, 8), cursor="hand2", anchor="w")
        self.status.pack(fill="x", padx=16, pady=(6, 12), anchor="w")
        self.status.bind("<Button-1>", lambda e: self.refresh())

        root.bind_all("<ButtonPress-1>", self.start_drag, add="+")
        root.bind_all("<B1-Motion>", self.on_drag, add="+")
        root.bind("<Escape>", lambda e: root.destroy())

        root.update_idletasks()
        root.geometry(f"{WIDTH}x{root.winfo_reqheight()}+{root.winfo_screenwidth() - WIDTH - 24}+48")

        self.refresh()
        self.schedule()

    def start_drag(self, event):
        self.drag = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def on_drag(self, event):
        self.root.geometry(f"+{event.x_root - self.drag[0]}+{event.y_root - self.drag[1]}")

    def schedule(self):
        self.root.after(REFRESH_MS, lambda: (self.refresh(), self.schedule()))

    def refresh(self):
        if self.loading:
            return
        self.loading = True
        self.status.config(text="обновление…")

        def work():
            try:
                self.result = ("ok", fetch_usage())
            except Exception as e:
                self.result = ("error", str(e))

        threading.Thread(target=work, daemon=True).start()
        self.wait_result()

    def wait_result(self):
        if self.result is None:
            self.root.after(150, self.wait_result)
            return
        kind, payload = self.result
        self.result = None
        self.loading = False
        if kind == "ok":
            self.render(payload)
            self.status.config(text=f"обновлено {datetime.now():%H:%M}", fg=DIM)
        else:
            self.status.config(text=payload, fg=HIGH)

    def render(self, data):
        for child in self.body.winfo_children():
            child.destroy()

        inner = WIDTH - 32
        for key, label in ROWS:
            item = data.get(key)
            if not isinstance(item, dict) or item.get("utilization") is None:
                continue
            pct = max(0.0, min(100.0, float(item["utilization"])))

            row = tk.Frame(self.body, bg=BG)
            row.pack(fill="x", pady=(8, 0))
            top = tk.Frame(row, bg=BG)
            top.pack(fill="x")
            tk.Label(top, text=label, bg=BG, fg=FG, font=(FONT, 10)).pack(side="left")
            tk.Label(top, text=f"{pct:.0f}%", bg=BG, fg=FG, font=(FONT, 10, "bold")).pack(side="right")

            bar = tk.Canvas(row, width=inner, height=4, bg=TRACK, highlightthickness=0, bd=0)
            bar.pack(fill="x", pady=(5, 3))
            if pct > 0:
                bar.create_rectangle(0, 0, max(2, inner * pct / 100), 4, fill=bar_color(pct), width=0)

            reset = format_reset(item.get("resets_at"))
            if reset:
                tk.Label(row, text=reset, bg=BG, fg=DIM, font=(FONT, 8)).pack(anchor="w")

        self.root.update_idletasks()
        self.root.geometry(f"{WIDTH}x{self.root.winfo_reqheight()}")


def main():
    root = tk.Tk()
    LimitsWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
