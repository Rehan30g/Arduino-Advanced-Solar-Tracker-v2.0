#!/usr/bin/env python3

import tkinter as tk
from tkinter import ttk, scrolledtext
import serial
import serial.tools.list_ports
import threading
import time
import math
import random
import os
import sys
import ctypes

try:
    import pygame
except Exception:
    pygame = None

# ======================== CONSTANTS ========================

BAUD_RATE = 115200
STATE_NAMES = ["SLEEP", "IDLE", "VERIFY", "HUNTING"]
STATE_COLORS = {
    0: "#6366f1",  # SLEEP  — indigo
    1: "#22c55e",  # IDLE   — green
    2: "#f59e0b",  # VERIFY — amber
    3: "#ef4444",  # HUNTING — red
}
STATE_EMOJIS = {
    0: "💤", 1: "✅", 2: "🔍", 3: "🎯"
}
CANVAS_BG = "#1a1a2e"
PANEL_BG = "#16213e"
HEADER_BG = "#0f3460"
TEXT_FG = "#e2e8f0"
ACCENT = "#00d4ff"

SERVO_MIN = 82
SERVO_MAX = 170
SERVO_CENTER = 126
WAKE_THRESHOLD = 650
SLEEP_THRESHOLD = 850
WAKE_CONFIRM_TARGET = 2
DARK_CONFIRM_TARGET = 3
RUN_MODE_NAMES = {
    0: "STANDALONE",
    1: "GUI",
    2: "PRESENTATION",
}
SLEEP_KIND_NAMES = {
    0: "SOFT",
    1: "TRUE",
}
PIN11_MODE_NAMES = {
    0: "AUTO",
    1: "FORCE OFF",
    2: "FORCE ON",
}
HEARTBEAT_INTERVAL_MS = 2000
HEARTBEAT_STALE_MS = 4500
PRESENTATION_FRAME_MS = 16
CONTROLLER_POLL_MS = 40
CONTROLLER_DEADZONE = 0.22
CONTROLLER_AXIS_SMOOTHING = 0.35
CONTROLLER_SERVO_SPEED_DPS = 120.0
CONTROLLER_IDLE_SYNC_EPSILON = 0.03
XBOX_LEFT_AXIS = 0
XBOX_A_BUTTON = 0


def _enable_windows_ansi():
    if os.name != "nt":
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        if handle == 0 or handle == -1:
            return False
        mode = ctypes.c_uint()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)) == 0:
            return False
        enabled = mode.value | 0x0004  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        if kernel32.SetConsoleMode(handle, enabled) == 0:
            return False
        return True
    except Exception:
        return False


ANSI_ENABLED = _enable_windows_ansi() if sys.stdout.isatty() else False


def _supports_ansi():
    return ANSI_ENABLED


def _cli_tag(kind):
    if not _supports_ansi():
        plain = {
            "ok": "[OK]",
            "wait": "[..]",
            "info": "[--]",
            "warn": "[!!]",
            "err": "[XX]",
        }
        return plain.get(kind, "[--]")

    colors = {
        "ok": "\033[92m",
        "wait": "\033[93m",
        "info": "\033[96m",
        "warn": "\033[91m",
        "err": "\033[91m",
    }
    labels = {
        "ok": "[OK]",
        "wait": "[..]",
        "info": "[--]",
        "warn": "[!!]",
        "err": "[XX]",
    }
    return f"{colors.get(kind, '\033[96m')}{labels.get(kind, '[--]')}\033[0m"


def run_console_intro():
    print("================================================================", flush=True)
    print("Credits: Rehan30g (Github)", flush=True)
    print("SOLAR TRACKER v2.0 - FSM EDITION | WACANA", flush=True)
    print("================================================================", flush=True)
    print("", flush=True)
    time.sleep(0.12)


def run_console_loading():
    print(f"{_cli_tag('wait')} Opening screen", end="", flush=True)
    for _ in range(4):
        time.sleep(0.16)
        print(".", end="", flush=True)
    print("", flush=True)


def terminal_serial_log(message):
    print(message, flush=True)

# ======================== PRESENTATION WINDOW ========================

PRES_STATE_LABELS = {
    0: "Tidur",
    1: "Siap",
    2: "Memeriksa",
    3: "Mencari Matahari"
}
PRES_STATE_DESC = {
    0: "Panel surya sedang istirahat karena gelap",
    1: "Panel surya dalam posisi optimal",
    2: "Mendeteksi perubahan cahaya...",
    3: "Menggerakkan panel menuju matahari!"
}
PRES_STATE_ICONS = {
    0: "🌙", 1: "☀️", 2: "🔍", 3: "🎯"
}


class PresentationWindow:
    """Modern light-themed presentation window with smooth gradient sky."""

    PRES_STATE_COLORS = {
        0: "#818cf8",  # SLEEP — soft indigo
        1: "#34d399",  # IDLE  — emerald
        2: "#fbbf24",  # VERIFY — amber
        3: "#f87171",  # HUNTING — coral
    }

    @staticmethod
    def _relative_angle(angle):
        return int(round(angle - SERVO_CENTER))

    @staticmethod
    def _normalized_servo_t(angle):
        span = max(1.0, float(SERVO_MAX - SERVO_MIN))
        return max(0.0, min(1.0, (float(angle) - SERVO_MIN) / span))

    def __init__(self, parent_app):
        self.app = parent_app
        self.win = tk.Toplevel(parent_app.root)
        self.win.title("\u2600\ufe0f Solar Tracker \u2014 Live Monitor")
        self.win.configure(bg="#f1f5f9")
        self.win.geometry("1050x720")
        self.win.minsize(850, 600)
        self.alive = True
        self.fullscreen = False
        self.win.protocol("WM_DELETE_WINDOW", self._on_close)
        self.win.bind("<F11>", self._toggle_fullscreen)
        self.win.bind("<Escape>", lambda e: self._set_fullscreen(False))

        # Interpolation state for smooth presentation animation
        self._smooth_pos = 126.0
        self._smooth_brightness = 512.0
        self._target_pos = 126.0
        self._target_brightness = 512.0
        self._lerp_speed = 0.18
        self._sun_scene_key = None

        # Pre-generate star positions (reused every frame)
        self._stars = [(random.random(), random.random(), random.uniform(1, 2.5))
                       for _ in range(24)]

        self._build()
        self._update_loop()
        self.app._send_cmd("CMD:PRES_ON")

    def _on_close(self):
        self.alive = False
        self.app._send_cmd("CMD:PRES_OFF")
        self.win.destroy()
        self.app.pres_window = None

    def _toggle_fullscreen(self, event=None):
        self._set_fullscreen(not self.fullscreen)

    def _set_fullscreen(self, state):
        self.fullscreen = state
        self.win.attributes("-fullscreen", state)

    def _build(self):
        # === HEADER BAR ===
        header = tk.Frame(self.win, bg="#ffffff", highlightbackground="#e2e8f0",
                           highlightthickness=1)
        header.pack(fill=tk.X)

        header_inner = tk.Frame(header, bg="#ffffff")
        header_inner.pack(fill=tk.X, padx=20, pady=10)

        tk.Label(header_inner, text="\u2600\ufe0f", bg="#ffffff",
                 font=("Segoe UI", 22)).pack(side=tk.LEFT)
        title_block = tk.Frame(header_inner, bg="#ffffff")
        title_block.pack(side=tk.LEFT, padx=10)
        tk.Label(title_block, text="Solar Panel Tracker", bg="#ffffff",
                 fg="#0f172a", font=("Segoe UI", 16, "bold")).pack(anchor=tk.W)
        tk.Label(title_block, text="Live Monitor  \u2022  Rehan Christian X2",
                 bg="#ffffff", fg="#94a3b8", font=("Segoe UI", 9)).pack(anchor=tk.W)



        # === STATE BADGE ===
        state_container = tk.Frame(self.win, bg="#f1f5f9")
        state_container.pack(fill=tk.X, padx=20, pady=(12, 6))

        state_card = tk.Frame(state_container, bg="#ffffff",
                               highlightbackground="#e2e8f0", highlightthickness=1)
        state_card.pack(fill=tk.X)

        state_inner = tk.Frame(state_card, bg="#ffffff")
        state_inner.pack(fill=tk.X, padx=20, pady=14)

        self.pres_state_icon = tk.Label(state_inner, text="\ud83c\udf19", bg="#ffffff",
                                         font=("Segoe UI", 28))
        self.pres_state_icon.pack(side=tk.LEFT, padx=(0, 12))

        state_text = tk.Frame(state_inner, bg="#ffffff")
        state_text.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.pres_state_label = tk.Label(state_text, text="Tidur", bg="#ffffff",
                                          fg="#818cf8", font=("Segoe UI", 20, "bold"))
        self.pres_state_label.pack(anchor=tk.W)

        self.pres_state_desc = tk.Label(state_text,
                                         text="Panel surya sedang istirahat karena gelap",
                                         bg="#ffffff", fg="#94a3b8",
                                         font=("Segoe UI", 10))
        self.pres_state_desc.pack(anchor=tk.W)

        self.pres_pos_badge = tk.Label(state_inner, text="0\u00b0", bg="#f0f9ff",
                                        fg="#0369a1", font=("Segoe UI", 22, "bold"))
        self.pres_pos_badge.pack(side=tk.RIGHT, padx=10)

        # === MAIN ROW ===
        mid = tk.Frame(self.win, bg="#f1f5f9")
        mid.pack(fill=tk.BOTH, expand=True, padx=20, pady=6)

        # Sun Path card (big, left)
        sun_card = tk.Frame(mid, bg="#ffffff", highlightbackground="#e2e8f0",
                             highlightthickness=1)
        sun_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 6))

        sun_hdr = tk.Frame(sun_card, bg="#ffffff")
        sun_hdr.pack(fill=tk.X, padx=16, pady=(10, 4))
        tk.Label(sun_hdr, text="\u2600\ufe0f Prediksi Posisi Matahari", bg="#ffffff",
                 fg="#475569", font=("Segoe UI", 11, "bold")).pack(side=tk.LEFT)

        self.pres_sun_canvas = tk.Canvas(sun_card, bg="#0c1323",
                                          highlightthickness=0)
        self.pres_sun_canvas.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 10))

        # Right column
        right_col = tk.Frame(mid, bg="#f1f5f9", width=260)
        right_col.pack(side=tk.RIGHT, fill=tk.Y, padx=(6, 0))
        right_col.pack_propagate(False)

        # Servo card
        servo_card = tk.Frame(right_col, bg="#ffffff", highlightbackground="#e2e8f0",
                               highlightthickness=1)
        servo_card.pack(fill=tk.X, pady=(0, 6))

        tk.Label(servo_card, text="\ud83c\udfaf Posisi Panel", bg="#ffffff",
                 fg="#475569", font=("Segoe UI", 10, "bold")).pack(
            pady=(10, 2), padx=12, anchor=tk.W)

        self.pres_servo_canvas = tk.Canvas(servo_card, bg="#f8fafc",
                                            highlightthickness=0, height=130)
        self.pres_servo_canvas.pack(fill=tk.X, padx=12, pady=2)

        self.pres_servo_label = tk.Label(servo_card, text="0\u00b0", bg="#ffffff",
                                          fg="#0f172a", font=("Segoe UI", 18, "bold"))
        self.pres_servo_label.pack(pady=(0, 8))

        # LDR card
        ldr_card = tk.Frame(right_col, bg="#ffffff", highlightbackground="#e2e8f0",
                             highlightthickness=1)
        ldr_card.pack(fill=tk.BOTH, expand=True)

        tk.Label(ldr_card, text="\ud83d\udca1 Intensitas Cahaya", bg="#ffffff",
                 fg="#475569", font=("Segoe UI", 10, "bold")).pack(
            pady=(10, 2), padx=12, anchor=tk.W)

        self.pres_ldr_canvas = tk.Canvas(ldr_card, bg="#f8fafc",
                                          highlightthickness=0)
        self.pres_ldr_canvas.pack(fill=tk.BOTH, expand=True, padx=12, pady=2)

        self.pres_ldr_label = tk.Label(ldr_card, text="Kiri: 0  \u2022  Kanan: 0",
                                        bg="#ffffff", fg="#475569",
                                        font=("Segoe UI", 10))
        self.pres_ldr_label.pack(pady=(0, 8))

        self.pres_sun_canvas.bind("<Configure>", self._on_pres_canvas_resize)

    def _on_pres_canvas_resize(self, event=None):
        self._sun_scene_key = None

    # ==================== SKY GRADIENT ====================

    def _lerp_color(self, c1, c2, t):
        return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))

    def _sky_color(self, sky_factor, y_ratio):
        """Vibrant multi-stop sky gradient. sky_factor:0=night,1=day. y_ratio:0=top,1=horizon."""
        night = [(8, 8, 35), (12, 15, 50), (20, 25, 65), (30, 35, 80), (40, 50, 95)]
        dawn = [(60, 25, 80), (120, 50, 100), (200, 110, 85), (235, 155, 95), (250, 185, 130)]
        day = [(25, 110, 215), (45, 145, 235), (75, 175, 248), (115, 200, 255), (165, 218, 255)]

        if sky_factor < 0.2:
            t = sky_factor / 0.2
            ramp = [self._lerp_color(night[i], dawn[i], t) for i in range(5)]
        elif sky_factor < 0.45:
            t = (sky_factor - 0.2) / 0.25
            ramp = [self._lerp_color(dawn[i], day[i], t) for i in range(5)]
        else:
            ramp = list(day)

        pos = y_ratio * 4
        idx = min(int(pos), 3)
        frac = pos - idx
        r, g, b = self._lerp_color(ramp[idx], ramp[idx + 1], frac)
        return f"#{r:02x}{g:02x}{b:02x}"

    # ==================== SUN PATH ====================

    def _draw_sun_path(self):
        c = self.pres_sun_canvas
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 150 or h < 100:
            return

        cfg = self.app.pres_config
        angle = self._smooth_pos
        display_angle = (SERVO_MAX + SERVO_MIN - angle) if cfg["flip_servo"] else angle
        relative_angle = display_angle - SERVO_CENTER
        brightness = max(0, min(1023, self._smooth_brightness))
        sky_factor = max(0.0, 1.0 - brightness / 800.0)
        horizon_y = h - 55
        is_night = sky_factor < 0.15
        scene_key = (w, h, int(sky_factor * 6), is_night, cfg["flip_direction"])

        if self._sun_scene_key != scene_key:
            self._sun_scene_key = scene_key
            c.delete("all")

            bands = min(horizon_y, 24)
            for i in range(bands):
                y1 = int(i * horizon_y / bands)
                y2 = int((i + 1) * horizon_y / bands)
                color = self._sky_color(sky_factor, i / bands)
                c.create_rectangle(0, y1, w, y2, fill=color, outline=color, tags=("bg",))

            if sky_factor < 0.35:
                star_alpha = 1.0 - (sky_factor / 0.35)
                for sx_r, sy_r, sr in self._stars:
                    star_x = sx_r * w
                    star_y = sy_r * (horizon_y - 20)
                    brightness_val = int(150 + 105 * star_alpha * (sr / 2.5))
                    sc = f"#{brightness_val:02x}{brightness_val:02x}{brightness_val:02x}"
                    radius = 2 if sr > 2.0 else 1
                    c.create_oval(
                        star_x-radius, star_y-radius, star_x+radius, star_y+radius,
                        fill=sc, outline="", tags=("bg",)
                    )

            gf = min(1.0, sky_factor * 1.8)
            gr_mount = self._lerp_color((20, 40, 20), (60, 130, 50), gf)
            gr_base = self._lerp_color((15, 30, 15), (45, 105, 35), gf)
            mpts = [
                (0, horizon_y), (w*0.06, horizon_y-12), (w*0.12, horizon_y-30),
                (w*0.18, horizon_y-18), (w*0.25, horizon_y-45), (w*0.32, horizon_y-28),
                (w*0.38, horizon_y-52), (w*0.44, horizon_y-35), (w*0.50, horizon_y-58),
                (w*0.56, horizon_y-40), (w*0.62, horizon_y-50), (w*0.68, horizon_y-30),
                (w*0.75, horizon_y-42), (w*0.82, horizon_y-22), (w*0.90, horizon_y-35),
                (w*0.96, horizon_y-10), (w, horizon_y)
            ]
            flat = []
            for px, py in mpts:
                flat.extend([px, py])
            flat.extend([w, h, 0, h])
            c.create_polygon(flat, fill=f"#{gr_mount[0]:02x}{gr_mount[1]:02x}{gr_mount[2]:02x}", outline="", tags=("bg",))
            c.create_rectangle(0, horizon_y+8, w, h,
                               fill=f"#{gr_base[0]:02x}{gr_base[1]:02x}{gr_base[2]:02x}", outline="", tags=("bg",))

            acx, acy = w // 2, horizon_y
            arx, ary = w * 0.44, horizon_y * 0.82
            for deg in range(10, 171, 6):
                rad = math.radians(deg)
                px = acx + arx * math.cos(rad)
                py2 = acy - ary * math.sin(rad)
                if py2 < horizon_y:
                    dc = "#94a3b8" if sky_factor > 0.3 else "#475569"
                    c.create_oval(px-1, py2-1, px+1, py2+1, fill=dc, outline="", tags=("bg",))

            ll = "Barat" if cfg["flip_direction"] else "Timur"
            rl = "Timur" if cfg["flip_direction"] else "Barat"
            lf = "#cbd5e1" if sky_factor < 0.3 else "#1e293b"
            c.create_text(25, horizon_y-10, text=f"\u2190 {ll}", fill=lf,
                          font=("Segoe UI", 9, "bold"), anchor=tk.W, tags=("bg",))
            c.create_text(w-25, horizon_y-10, text=f"{rl} \u2192", fill=lf,
                          font=("Segoe UI", 9, "bold"), anchor=tk.E, tags=("bg",))

        c.delete("dynamic")

        # dynamic panel + sun/moon only

        # --- Solar panel (pole below, panel on top) ---
        pcx = w // 2
        pivot_y = horizon_y - 12
        pole_bot = horizon_y + 28
        panel_half_w = 34
        panel_half_h = 7
        tilt_rad = math.radians(relative_angle)
        ux = math.cos(tilt_rad)
        uy = -math.sin(tilt_rad)
        vx = -uy
        vy = ux

        # Pole and hinge
        c.create_line(pcx, pivot_y + 8, pcx, pole_bot, fill="#94a3b8", width=3, tags=("dynamic",))
        c.create_line(pcx, horizon_y + 8, pcx, pole_bot, fill="#64748b", width=4, tags=("dynamic",))
        c.create_oval(pcx-4, pivot_y-4, pcx+4, pivot_y+4, fill="#94a3b8", outline="", tags=("dynamic",))

        # Panel body: 0° = horizontal, +/- follows relative servo angle
        corners = []
        for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            x = pcx + sx * panel_half_w * ux + sy * panel_half_h * vx
            y = pivot_y + sx * panel_half_w * uy + sy * panel_half_h * vy
            corners.extend([x, y])
        c.create_polygon(corners, fill="#2563eb", outline="#1d4ed8", width=2, tags=("dynamic",))
        c.create_line(
            pcx - panel_half_w * 0.75 * ux,
            pivot_y - panel_half_w * 0.75 * uy,
            pcx + panel_half_w * 0.75 * ux,
            pivot_y + panel_half_w * 0.75 * uy,
            fill="#93c5fd",
            width=2,
            tags=("dynamic",),
        )

        # --- Sun arc ---
        acx, acy = w // 2, horizon_y
        arx, ary = w * 0.44, horizon_y * 0.82

        for deg in range(10, 171, 6):
            rad = math.radians(deg)
            px = acx + arx * math.cos(rad)
            py2 = acy - ary * math.sin(rad)
            if py2 < horizon_y:
                dc = "#94a3b8" if sky_factor > 0.3 else "#475569"
                c.create_oval(px-1, py2-1, px+1, py2+1, fill=dc, outline="", tags=("dynamic",))

        # --- Sun / Moon position ---
        t = (display_angle - SERVO_MIN) / (SERVO_MAX - SERVO_MIN)
        sun_deg = 15 + 150 * t
        sr = math.radians(sun_deg)
        sx = acx + arx * math.cos(sr)
        sy = acy - ary * math.sin(sr)
        body_r = 14

        if is_night:
            # Moon glow (bright enough to see)
            for gi in range(3):
                gr2 = 28 - gi * 5
                gv = 100 + gi * 30
                c.create_oval(sx-gr2, sy-gr2, sx+gr2, sy+gr2,
                               fill=f"#{gv:02x}{gv:02x}{min(255,gv+40):02x}", outline="", tags=("dynamic",))
            # Moon body
            c.create_oval(sx-body_r, sy-body_r, sx+body_r, sy+body_r,
                           fill="#f1f5f9", outline="#e2e8f0", width=2, tags=("dynamic",))
            # Crescent shadow
            c.create_oval(sx-body_r+6, sy-body_r-1, sx+body_r+6, sy+body_r-1,
                           fill="#1e2540", outline="", tags=("dynamic",))
            c.create_oval(sx-body_r, sy-body_r, sx+body_r, sy+body_r,
                           fill="", outline="#e2e8f0", width=2, tags=("dynamic",))
        else:
            # Sun glow
            if sky_factor > 0.15:
                for gi, gc in enumerate(["#fef3c7", "#fde68a", "#fbbf24"]):
                    gr2 = 30 - gi * 6
                    c.create_oval(sx-gr2, sy-gr2, sx+gr2, sy+gr2,
                                   fill=gc, outline="", stipple="gray25" if gi < 2 else "", tags=("dynamic",))
            # Sun body
            bc = "#fbbf24" if sky_factor > 0.3 else "#f59e0b" if sky_factor > 0.15 else "#b45309"
            oc = "#f97316" if sky_factor > 0.3 else "#92400e"
            c.create_oval(sx-body_r, sy-body_r, sx+body_r, sy+body_r,
                           fill=bc, outline=oc, width=2, tags=("dynamic",))
            # Rays
            if sky_factor > 0.3:
                for i in range(8):
                    ra2 = math.radians(i * 45)
                    c.create_line(sx+(body_r+3)*math.cos(ra2), sy+(body_r+3)*math.sin(ra2),
                                   sx+(body_r+10)*math.cos(ra2), sy+(body_r+10)*math.sin(ra2),
                                   fill="#fbbf24", width=2, tags=("dynamic",))

        # --- Labels ---
        ll = "Barat" if cfg["flip_direction"] else "Timur"
        rl = "Timur" if cfg["flip_direction"] else "Barat"
        lf = "#cbd5e1" if sky_factor < 0.3 else "#1e293b"
        # Direction labels stay in cached background

    # ==================== SERVO GAUGE ====================

    def _draw_servo(self, angle, state):
        c = self.pres_servo_canvas
        w = c.winfo_width()
        h = 130
        if w < 80:
            return
        c.delete("all")

        cfg = self.app.pres_config
        da = (SERVO_MAX + SERVO_MIN - angle) if cfg["flip_servo"] else angle
        t = self._normalized_servo_t(da)
        cx, cy = w // 2, h - 16
        r = 50

        c.create_arc(cx-r, cy-r, cx+r, cy+r, start=0, extent=180,
                      outline="#e2e8f0", width=6, style=tk.ARC)

        sweep = 180 * t
        start_arc = 180
        color = self.PRES_STATE_COLORS.get(state, "#3b82f6")
        c.create_arc(cx-r, cy-r, cx+r, cy+r,
                      start=start_arc, extent=-sweep,
                      outline=color, width=6, style=tk.ARC)

        rad = math.pi * (1.0 - t)
        nx = cx + (r-8)*math.cos(rad)
        ny = cy - (r-8)*math.sin(rad)
        c.create_line(cx, cy, nx, ny, fill=color, width=3, capstyle=tk.ROUND)
        c.create_oval(cx-4, cy-4, cx+4, cy+4, fill=color, outline="")

        c.create_text(cx-r-8, cy+2, text=f"{self._relative_angle(SERVO_MIN)}\u00b0", fill="#94a3b8",
                       font=("Segoe UI", 7))
        c.create_text(cx+r+8, cy+2, text=f"{self._relative_angle(SERVO_MAX):+d}\u00b0", fill="#94a3b8",
                       font=("Segoe UI", 7))

    # ==================== LDR BARS ====================

    def _draw_ldr(self, valL, valR):
        c = self.pres_ldr_canvas
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 80 or h < 60:
            return
        c.delete("all")

        bar_w, gap = 45, 24
        max_h = h - 44
        base_y = h - 18
        cx = w // 2

        for i, (val, label) in enumerate([(valL, "Kiri"), (valR, "Kanan")]):
            x = cx - gap//2 - bar_w if i == 0 else cx + gap//2
            bh = max(2, int((val / 1023) * max_h))
            by = base_y - bh

            c.create_rectangle(x, 18, x+bar_w, base_y,
                                fill="#f1f5f9", outline="#e2e8f0", width=1)
            color = self._brightness_color(val)
            c.create_rectangle(x+1, by, x+bar_w-1, base_y-1,
                                fill=color, outline="")

            c.create_text(x+bar_w//2, 8, text=str(val),
                           fill="#0f172a", font=("Segoe UI", 9, "bold"))
            c.create_text(x+bar_w//2, base_y+10, text=label,
                           fill="#64748b", font=("Segoe UI", 8))

    def _brightness_color(self, val):
        if val < 100:   return "#facc15"
        elif val < 250: return "#fbbf24"
        elif val < 400: return "#f59e0b"
        elif val < 550: return "#3b82f6"
        elif val < 700: return "#6366f1"
        else:           return "#312e81"

    # ==================== UPDATE (presentation-friendly fps) ====================

    def update_data(self, data):
        if not self.alive:
            return
        d = data
        state = d["state"]

        # Set interpolation targets from real data
        self._target_pos = float(d["pos"])
        self._target_brightness = float(d["rataRata"])

        # Lerp smooth values toward targets
        self._smooth_pos += (self._target_pos - self._smooth_pos) * self._lerp_speed
        self._smooth_brightness += (self._target_brightness - self._smooth_brightness) * self._lerp_speed

        # Update state labels (only when changed, cheap)
        color = self.PRES_STATE_COLORS.get(state, "#818cf8")
        self.pres_state_icon.configure(text=PRES_STATE_ICONS.get(state, "\u2753"))
        self.pres_state_label.configure(text=PRES_STATE_LABELS.get(state, "?"), fg=color)
        self.pres_state_desc.configure(text=PRES_STATE_DESC.get(state, ""))
        relative_angle = self._relative_angle(self._smooth_pos)
        self.pres_pos_badge.configure(text=f"{relative_angle:+d}\u00b0" if relative_angle else "0\u00b0")

        # Draw using interpolated values
        self._draw_sun_path()
        self._draw_servo(self._smooth_pos, state)
        self.pres_servo_label.configure(text=f"{relative_angle:+d}\u00b0" if relative_angle else "0\u00b0")
        self._draw_ldr(d["valL"], d["valR"])
        self.pres_ldr_label.configure(text=f"Kiri: {d['valL']}  \u2022  Kanan: {d['valR']}")

    def _update_loop(self):
        if not self.alive:
            return
        self.update_data(self.app.data)
        self.win.after(PRESENTATION_FRAME_MS, self._update_loop)




# ======================== DEBUGGER APP ========================

class SolarDebugger:
    def __init__(self, root):
        self.root = root
        self.root.title("🔧 Solar Tracker Debugger v2.0")
        self.root.configure(bg="#0a0a1a")
        self.root.geometry("1100x750")
        self.root.minsize(900, 650)

        # State
        self.ser = None
        self.connected = False
        self.running = True
        self.read_thread = None

        # Data
        self.data = {
            "state": 0, "valL": 0, "valR": 0, "selisih": 0,
            "rataRata": 0, "pos": 126, "attached": 0,
            "verifyCount": 0, "simMode": 0,
            "pin11State": 0, "pin11Mode": 0,
            "darkConfirmCount": 0, "brightConfirmCount": 0,
            "millis": 0,
            "runMode": 0,
            "sensorAgeMs": 0,
            "sleepKind": 0,
        }
        self.last_pong_time = 0.0
        self.last_heartbeat_time = 0.0
        self.awaiting_pong = False

        # Presentation window ref
        self.pres_window = None
        self.controller_status_text = "Controller: not available"
        self.controller_status_level = "warn"
        self.controller_ready = False
        self.controller_joystick = None
        self.controller_last_button_a = False
        self.controller_last_servo_sent = None
        self.controller_axis_filtered = 0.0
        self.controller_servo_float = float(self.data["pos"])
        self.controller_last_poll_time = time.time()

        # Presentation config
        self.pres_config = {
            "flip_servo": False,
            "flip_direction": False,
        }

        self._init_controller_support()
        self._build_ui()
        self._start_update_loop()

    # ==================== UI BUILD ====================

    def _build_ui(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Dark.TFrame", background=PANEL_BG)
        style.configure("Dark.TLabel", background=PANEL_BG, foreground=TEXT_FG,
                         font=("Consolas", 10))
        style.configure("Header.TLabel", background=HEADER_BG, foreground=ACCENT,
                         font=("Consolas", 12, "bold"))
        style.configure("Big.TLabel", background=PANEL_BG, foreground="#ffffff",
                         font=("Consolas", 14, "bold"))
        style.configure("State.TLabel", background=PANEL_BG, foreground=TEXT_FG,
                         font=("Consolas", 11))
        style.configure("Dark.TButton", font=("Consolas", 9))
        style.configure("Dark.TCheckbutton", background=PANEL_BG, foreground=TEXT_FG,
                         font=("Consolas", 10))

        # === TOP BAR: Connection ===
        top = ttk.Frame(self.root, style="Dark.TFrame")
        top.pack(fill=tk.X, padx=8, pady=(8, 4))

        ttk.Label(top, text="🔧 SOLAR TRACKER DEBUGGER", style="Header.TLabel").pack(side=tk.LEFT, padx=8)

        # Port selector
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(top, textvariable=self.port_var, width=12,
                                        font=("Consolas", 10), state="readonly")
        self.port_combo.pack(side=tk.LEFT, padx=(20, 4))

        ttk.Button(top, text="↻", width=3, command=self._refresh_ports).pack(side=tk.LEFT, padx=2)

        self.connect_btn = ttk.Button(top, text="Connect", command=self._toggle_connection)
        self.connect_btn.pack(side=tk.LEFT, padx=4)

        self.status_label = ttk.Label(top, text="● Disconnected", foreground="#ef4444",
                                       style="Dark.TLabel")
        self.status_label.pack(side=tk.LEFT, padx=8)

        # Presentation mode button
        ttk.Button(top, text="📺 Presentation",
                   command=self._open_presentation).pack(side=tk.RIGHT, padx=8)

        # === MAIN BODY (3 columns) ===
        body = ttk.Frame(self.root, style="Dark.TFrame")
        body.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        # --- LEFT: Servo + State ---
        left = ttk.Frame(body, style="Dark.TFrame", width=300)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 4))
        left.pack_propagate(False)

        self._build_servo_panel(left)
        self._build_state_panel(left)

        # --- CENTER: LDR Monitor ---
        center = ttk.Frame(body, style="Dark.TFrame", width=350)
        center.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4)

        self._build_ldr_panel(center)

        # --- RIGHT: Scrollable Sandbox ---
        right = ttk.Frame(body, style="Dark.TFrame", width=320)
        right.pack(side=tk.RIGHT, fill=tk.BOTH, padx=(4, 0))
        right.pack_propagate(False)

        self._build_right_scroll_area(right)

        # === BOTTOM: Serial Log ===
        self._build_log_panel(self.root)

        # Init
        self._refresh_ports()

    # ---------- SERVO PANEL ----------

    def _build_servo_panel(self, parent):
        frame = ttk.LabelFrame(parent, text=" 🎯 SERVO ", style="Dark.TFrame")
        frame.pack(fill=tk.X, padx=4, pady=4)

        self.servo_canvas = tk.Canvas(frame, width=280, height=180, bg=CANVAS_BG,
                                       highlightthickness=0)
        self.servo_canvas.pack(padx=8, pady=8)

        self.servo_label = ttk.Label(frame, text="Position: 126°", style="Big.TLabel")
        self.servo_label.pack(pady=(0,4))

        self.servo_attach_label = ttk.Label(frame, text="DETACHED", foreground="#ef4444",
                                             style="Dark.TLabel")
        self.servo_attach_label.pack(pady=(0,8))

    def _draw_servo(self, angle):
        c = self.servo_canvas
        c.delete("all")
        w, h = 280, 180
        cx, cy = w // 2, h - 20

        # Arc background
        r = 110
        c.create_arc(cx - r, cy - r, cx + r, cy + r, start=0, extent=180,
                      outline="#334155", width=2, style=tk.ARC)

        # Tick marks
        for deg in range(SERVO_MIN, SERVO_MAX + 1, 20):
            rad = math.radians(180 - deg)
            x1 = cx + (r - 8) * math.cos(rad)
            y1 = cy - (r - 8) * math.sin(rad)
            x2 = cx + (r + 2) * math.cos(rad)
            y2 = cy - (r + 2) * math.sin(rad)
            c.create_line(x1, y1, x2, y2, fill="#64748b", width=1)
            if deg % 40 == 10 or deg == SERVO_MIN or deg == SERVO_MAX:
                tx = cx + (r + 14) * math.cos(rad)
                ty = cy - (r + 14) * math.sin(rad)
                c.create_text(tx, ty, text=str(deg), fill="#94a3b8",
                               font=("Consolas", 7))

        # Colored arc showing position
        sweep = angle - SERVO_MIN
        total_range = SERVO_MAX - SERVO_MIN
        start_arc = 180 - SERVO_MIN
        c.create_arc(cx - r, cy - r, cx + r, cy + r,
                      start=start_arc, extent=-sweep,
                      outline=ACCENT, width=4, style=tk.ARC)

        # Needle
        rad = math.radians(180 - angle)
        nx = cx + (r - 25) * math.cos(rad)
        ny = cy - (r - 25) * math.sin(rad)
        state = self.data["state"]
        color = STATE_COLORS.get(state, ACCENT)
        c.create_line(cx, cy, nx, ny, fill=color, width=3)
        c.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, fill=color, outline="")

    # ---------- STATE PANEL ----------

    def _build_state_panel(self, parent):
        frame = ttk.LabelFrame(parent, text=" ⚡ STATE ", style="Dark.TFrame")
        frame.pack(fill=tk.X, padx=4, pady=4)

        self.state_indicators = []
        for i, name in enumerate(STATE_NAMES):
            row = ttk.Frame(frame, style="Dark.TFrame")
            row.pack(fill=tk.X, padx=12, pady=3)

            indicator = tk.Canvas(row, width=16, height=16, bg=PANEL_BG,
                                   highlightthickness=0)
            indicator.pack(side=tk.LEFT, padx=(0, 8))

            lbl = ttk.Label(row, text=f"{STATE_EMOJIS[i]} {name}", style="State.TLabel")
            lbl.pack(side=tk.LEFT)

            self.state_indicators.append(indicator)

        self.state_detail_label = ttk.Label(frame, text="verify: 0/3 | uptime: 0s",
                                             style="Dark.TLabel")
        self.state_detail_label.pack(pady=(4, 8))

        self.pin11_status_label = ttk.Label(frame, text="Pin 11: OFF | AUTO",
                                             style="Dark.TLabel")
        self.pin11_status_label.pack(pady=(0, 8))

        self.sleep_counter_label = ttk.Label(
            frame,
            text=f"sleep-dark 0/{DARK_CONFIRM_TARGET} | wake-bright 0/{WAKE_CONFIRM_TARGET}",
            style="Dark.TLabel"
        )
        self.sleep_counter_label.pack(pady=(0, 4))

        self.sleep_threshold_label = ttk.Label(
            frame,
            text=f"wake<{WAKE_THRESHOLD} | sleep>{SLEEP_THRESHOLD}",
            style="Dark.TLabel"
        )
        self.sleep_threshold_label.pack(pady=(0, 4))

        self.runtime_mode_label = ttk.Label(
            frame,
            text="mode: STANDALONE | sleep: SOFT",
            style="Dark.TLabel"
        )
        self.runtime_mode_label.pack(pady=(0, 4))

        self.sensor_age_label = ttk.Label(
            frame,
            text="sensor age: 0ms | heartbeat: waiting",
            style="Dark.TLabel"
        )
        self.sensor_age_label.pack(pady=(0, 8))

    def _update_state_indicators(self, active_state):
        for i, canvas in enumerate(self.state_indicators):
            canvas.delete("all")
            if i == active_state:
                color = STATE_COLORS[i]
                canvas.create_oval(2, 2, 14, 14, fill=color, outline=color)
            else:
                canvas.create_oval(2, 2, 14, 14, fill="", outline="#475569", width=2)

    # ---------- LDR PANEL ----------

    def _build_ldr_panel(self, parent):
        frame = ttk.LabelFrame(parent, text=" 📊 LDR MONITOR ", style="Dark.TFrame")
        frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        self.ldr_canvas = tk.Canvas(frame, bg=CANVAS_BG, highlightthickness=0)
        self.ldr_canvas.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self._ldr_items = None

        info = ttk.Frame(frame, style="Dark.TFrame")
        info.pack(fill=tk.X, padx=8, pady=(0, 8))

        self.ldr_info_label = ttk.Label(info, text="L=0  R=0  |  Selisih: 0  |  Avg: 0",
                                         style="Dark.TLabel")
        self.ldr_info_label.pack()

    def _draw_ldr(self, valL, valR):
        c = self.ldr_canvas
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 50 or h < 50:
            return

        bar_w = 60
        gap = 60
        max_h = h - 60
        base_y = h - 30
        center_x = w // 2
        lx = center_x - gap // 2 - bar_w
        rx = center_x + gap // 2
        gelap_y = base_y - int((100 / 1023) * max_h)

        if self._ldr_items is None:
            self._ldr_items = {
                "bar_l_fill": c.create_rectangle(0, 0, 0, 0, fill="#22c55e", outline=""),
                "bar_l_outline": c.create_rectangle(0, 0, 0, 0, fill="", outline="#475569", width=1),
                "bar_r_fill": c.create_rectangle(0, 0, 0, 0, fill="#22c55e", outline=""),
                "bar_r_outline": c.create_rectangle(0, 0, 0, 0, fill="", outline="#475569", width=1),
                "label_l": c.create_text(0, 0, text="", fill="#e2e8f0", font=("Consolas", 10, "bold")),
                "label_r": c.create_text(0, 0, text="", fill="#e2e8f0", font=("Consolas", 10, "bold")),
                "value_l": c.create_text(0, 0, text="", fill="#22c55e", font=("Consolas", 12, "bold")),
                "value_r": c.create_text(0, 0, text="", fill="#22c55e", font=("Consolas", 12, "bold")),
                "tol_text": c.create_text(0, 0, text="", fill="#22c55e", font=("Consolas", 11, "bold")),
                "gelap_line": c.create_line(0, 0, 0, 0, fill="#ef4444", dash=(4, 4), width=1),
                "gelap_text": c.create_text(0, 0, text="TERANG<100", fill="#ef4444", font=("Consolas", 7)),
            }

        lh = int((valL / 1023) * max_h)
        ly = base_y - lh
        color_l = self._value_color(valL)
        rh = int((valR / 1023) * max_h)
        ry = base_y - rh
        color_r = self._value_color(valR)

        tol_label = f"Selisih: {abs(valL - valR)}"
        sel = abs(valL - valR)
        tol_color = "#ef4444" if sel > 50 else "#22c55e"

        items = self._ldr_items
        c.coords(items["bar_l_fill"], lx, ly, lx + bar_w, base_y)
        c.coords(items["bar_l_outline"], lx, ly, lx + bar_w, base_y)
        c.coords(items["bar_r_fill"], rx, ry, rx + bar_w, base_y)
        c.coords(items["bar_r_outline"], rx, ry, rx + bar_w, base_y)
        c.itemconfigure(items["bar_l_fill"], fill=color_l)
        c.itemconfigure(items["bar_r_fill"], fill=color_r)

        c.coords(items["label_l"], lx + bar_w // 2, base_y + 14)
        c.coords(items["label_r"], rx + bar_w // 2, base_y + 14)
        c.itemconfigure(items["label_l"], text=f"L: {valL}")
        c.itemconfigure(items["label_r"], text=f"R: {valR}")

        c.coords(items["value_l"], lx + bar_w // 2, ly - 10)
        c.coords(items["value_r"], rx + bar_w // 2, ry - 10)
        c.itemconfigure(items["value_l"], text=str(valL), fill=color_l)
        c.itemconfigure(items["value_r"], text=str(valR), fill=color_r)

        c.coords(items["tol_text"], center_x, 15)
        c.itemconfigure(items["tol_text"], text=tol_label, fill=tol_color)

        c.coords(items["gelap_line"], 20, gelap_y, w - 20, gelap_y)
        c.coords(items["gelap_text"], w - 60, gelap_y - 10)

    def _value_color(self, val):
        """HIGH value = gelap, LOW value = terang (pull-up LDR circuit)"""
        if val < 100:
            return "#22c55e"  # Terang (hijau)
        elif val < 300:
            return "#84cc16"
        elif val < 500:
            return "#f59e0b"
        elif val < 700:
            return "#dc2626"
        else:
            return "#7f1d1d"  # Gelap (merah tua)

    # ---------- SANDBOX PANEL ----------

    def _build_sandbox_panel(self, parent):
        frame = ttk.LabelFrame(parent, text=" 🧪 SANDBOX ", style="Dark.TFrame")
        frame.pack(fill=tk.X, padx=4, pady=4)

        self.controller_status_label = ttk.Label(
            frame,
            text=self.controller_status_text,
            style="Dark.TLabel",
        )
        self.controller_status_label.pack(padx=12, pady=(8, 4), anchor=tk.W)
        self._set_controller_status(self.controller_status_text, self.controller_status_level)

        # Sim mode toggle
        self.sim_var = tk.BooleanVar(value=False)
        sim_chk = ttk.Checkbutton(frame, text="🔀 Simulasi Mode", variable=self.sim_var,
                                   command=self._on_sim_toggle, style="Dark.TCheckbutton")
        sim_chk.pack(padx=12, pady=(4, 4), anchor=tk.W)

        # LDR Sliders
        ttk.Label(frame, text="LDR Kiri (A0):", style="Dark.TLabel").pack(padx=12, anchor=tk.W, pady=(8,0))
        self.sim_l_var = tk.IntVar(value=512)
        self.sim_l_slider = ttk.Scale(frame, from_=0, to=1023, variable=self.sim_l_var,
                                       orient=tk.HORIZONTAL, command=lambda v: self._on_sim_slider("L"))
        self.sim_l_slider.pack(fill=tk.X, padx=12)
        self.sim_l_label = ttk.Label(frame, text="512", style="Dark.TLabel")
        self.sim_l_label.pack(padx=12, anchor=tk.E)

        ttk.Label(frame, text="LDR Kanan (A1):", style="Dark.TLabel").pack(padx=12, anchor=tk.W, pady=(8,0))
        self.sim_r_var = tk.IntVar(value=512)
        self.sim_r_slider = ttk.Scale(frame, from_=0, to=1023, variable=self.sim_r_var,
                                       orient=tk.HORIZONTAL, command=lambda v: self._on_sim_slider("R"))
        self.sim_r_slider.pack(fill=tk.X, padx=12)
        self.sim_r_label = ttk.Label(frame, text="512", style="Dark.TLabel")
        self.sim_r_label.pack(padx=12, anchor=tk.E)

        # Separator
        ttk.Separator(frame, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=12, pady=8)

        # Force state buttons
        ttk.Label(frame, text="⚡ Force State:", style="Dark.TLabel").pack(padx=12, anchor=tk.W)
        btn_frame = ttk.Frame(frame, style="Dark.TFrame")
        btn_frame.pack(fill=tk.X, padx=12, pady=4)

        for i, name in enumerate(STATE_NAMES):
            btn = ttk.Button(btn_frame, text=f"{STATE_EMOJIS[i]} {name}",
                              command=lambda s=i: self._force_state(s))
            btn.pack(side=tk.LEFT, padx=2, expand=True, fill=tk.X)

        # Separator
        ttk.Separator(frame, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=12, pady=8)

        # Servo manual
        ttk.Label(frame, text="🎯 Servo Manual:", style="Dark.TLabel").pack(padx=12, anchor=tk.W)
        self.manual_servo_var = tk.IntVar(value=126)
        self.manual_servo_slider = ttk.Scale(frame, from_=SERVO_MIN, to=SERVO_MAX,
                                              variable=self.manual_servo_var,
                                              orient=tk.HORIZONTAL,
                                              command=lambda v: self._on_servo_slider())
        self.manual_servo_slider.pack(fill=tk.X, padx=12)
        self.manual_servo_label = ttk.Label(frame, text="126°", style="Dark.TLabel")
        self.manual_servo_label.pack(padx=12, anchor=tk.E)

        servo_btn_frame = ttk.Frame(frame, style="Dark.TFrame")
        servo_btn_frame.pack(fill=tk.X, padx=12, pady=4)
        ttk.Button(servo_btn_frame, text="🔌 Attach", command=self._servo_attach).pack(
            side=tk.LEFT, padx=2, expand=True, fill=tk.X)
        ttk.Button(servo_btn_frame, text="⛔ Detach", command=self._servo_detach).pack(
            side=tk.LEFT, padx=2, expand=True, fill=tk.X)
        # ---------- PIN 11 ----------

        ttk.Separator(frame, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=12, pady=8)

        ttk.Label(frame, text="Pin 11:", style="Dark.TLabel").pack(padx=12, anchor=tk.W)
        self.pin11_override_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Manual Override",
                         variable=self.pin11_override_var,
                         command=self._on_pin11_override_toggle,
                         style="Dark.TCheckbutton").pack(padx=12, pady=(4, 2), anchor=tk.W)

        self.pin11_var = tk.BooleanVar(value=False)
        self.pin11_toggle = ttk.Checkbutton(frame, text="Nyala (HIGH)",
                                             variable=self.pin11_var,
                                             command=self._on_pin11_toggle,
                                             style="Dark.TCheckbutton")
        self.pin11_toggle.pack(padx=12, pady=(0, 2), anchor=tk.W)
        self.pin11_toggle.configure(state=tk.DISABLED)

        self.pin11_control_label = ttk.Label(frame, text="Mode AUTO mengikuti state alat",
                                              style="Dark.TLabel")
        self.pin11_control_label.pack(padx=12, pady=(0, 8), anchor=tk.W)

    def _build_pres_config_panel(self, parent):
        frame = ttk.LabelFrame(parent, text=" 📺 PRESENTASI CONFIG ", style="Dark.TFrame")
        frame.pack(fill=tk.X, padx=4, pady=4)

        # Flip servo visual
        self.flip_servo_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="🔄 Flip Posisi Servo",
                         variable=self.flip_servo_var,
                         command=self._on_pres_config_change,
                         style="Dark.TCheckbutton").pack(padx=12, pady=(8, 2), anchor=tk.W)

        # Flip direction labels
        self.flip_dir_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="🧭 Swap Timur ↔ Barat",
                         variable=self.flip_dir_var,
                         command=self._on_pres_config_change,
                         style="Dark.TCheckbutton").pack(padx=12, pady=(2, 8), anchor=tk.W)

    def _on_pres_config_change(self):
        self.pres_config["flip_servo"] = self.flip_servo_var.get()
        self.pres_config["flip_direction"] = self.flip_dir_var.get()
        self._log(f"📺 Config: flip_servo={self.pres_config['flip_servo']}, "
                  f"flip_dir={self.pres_config['flip_direction']}", "info")

    def _build_right_scroll_area(self, parent):
        canvas = tk.Canvas(parent, bg=PANEL_BG, highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        inner = ttk.Frame(canvas, style="Dark.TFrame")
        canvas_window = canvas.create_window((0, 0), window=inner, anchor=tk.NW)

        def _sync_scrollregion(event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _sync_inner_width(event):
            canvas.itemconfigure(canvas_window, width=event.width)

        def _on_mousewheel(event):
            if canvas.winfo_height() >= inner.winfo_reqheight():
                return
            delta = 0
            if getattr(event, "num", None) == 4:
                delta = -1
            elif getattr(event, "num", None) == 5:
                delta = 1
            elif getattr(event, "delta", 0):
                delta = -1 * int(event.delta / 120)
            if delta:
                canvas.yview_scroll(delta, "units")

        inner.bind("<Configure>", _sync_scrollregion)
        canvas.bind("<Configure>", _sync_inner_width)

        for widget in (canvas, inner):
            widget.bind("<MouseWheel>", _on_mousewheel)
            widget.bind("<Button-4>", _on_mousewheel)
            widget.bind("<Button-5>", _on_mousewheel)

        self.right_panel_canvas = canvas
        self.right_panel_inner = inner
        self.right_panel_scrollbar = scrollbar

        self._build_sandbox_panel(inner)
        self._build_pres_config_panel(inner)

    # ---------- LOG PANEL ----------

    def _build_log_panel(self, parent):
        frame = ttk.LabelFrame(parent, text=" 📝 SERIAL LOG ", style="Dark.TFrame")
        frame.pack(fill=tk.X, padx=8, pady=(4, 8))

        self.log_text = scrolledtext.ScrolledText(frame, height=8, bg="#0a0a1a", fg="#22c55e",
                                                    font=("Consolas", 9), insertbackground=ACCENT,
                                                    wrap=tk.WORD, state=tk.DISABLED)
        self.log_text.pack(fill=tk.X, padx=4, pady=4)

        # Tags for coloring
        self.log_text.tag_configure("data", foreground="#64748b")
        self.log_text.tag_configure("log", foreground="#22c55e")
        self.log_text.tag_configure("error", foreground="#ef4444")
        self.log_text.tag_configure("info", foreground="#00d4ff")

    def _log(self, msg, tag="log"):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, msg + "\n", tag)
        self.log_text.see(tk.END)
        # Keep max 500 lines
        lines = int(self.log_text.index('end-1c').split('.')[0])
        if lines > 500:
            self.log_text.delete('1.0', f'{lines - 500}.0')
        self.log_text.configure(state=tk.DISABLED)

    # ==================== SERIAL ====================

    def _refresh_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_combo["values"] = ports
        if ports:
            self.port_combo.set(ports[0])

    def _toggle_connection(self):
        if self.connected:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        port = self.port_var.get()
        if not port:
            self._log("❌ No port selected!", "error")
            terminal_serial_log(f"{_cli_tag('warn')} Serial port not selected")
            return
        try:
            self.ser = serial.Serial(port, BAUD_RATE, timeout=0.1)
            time.sleep(2)  # Wait for Arduino reset
            self.connected = True
            self.connect_btn.configure(text="Disconnect")
            self.status_label.configure(text=f"● Connected ({port})", foreground="#22c55e")
            self._log(f"✅ Connected to {port} @ {BAUD_RATE} baud", "info")
            terminal_serial_log(f"{_cli_tag('ok')} Serial connected: {port} @ {BAUD_RATE}")

            self.last_pong_time = 0.0
            self.last_heartbeat_time = 0.0
            self.awaiting_pong = False

            # Start read thread
            self.read_thread = threading.Thread(target=self._serial_reader, daemon=True)
            self.read_thread.start()

            # Send ping
            self._send_cmd("CMD:PING")
        except Exception as e:
            self._log(f"❌ Connection failed: {e}", "error")
            terminal_serial_log(f"{_cli_tag('err')} Serial connection failed: {e}")

    def _disconnect(self):
        was_connected = self.connected or self.ser is not None
        self.connected = False
        self.awaiting_pong = False
        self.last_pong_time = 0.0
        if self.ser:
            try:
                self.ser.close()
            except:
                pass
            self.ser = None
        self.connect_btn.configure(text="Connect")
        self.status_label.configure(text="● Disconnected", foreground="#ef4444")
        self._log("🔌 Disconnected", "info")
        if was_connected:
            terminal_serial_log(f"{_cli_tag('warn')} Serial disconnected")

    def _send_cmd(self, cmd):
        if self.connected and self.ser:
            try:
                self.ser.write((cmd + "\n").encode())
            except Exception as e:
                self._log(f"❌ Send error: {e}", "error")
                self._disconnect()

    def _serial_reader(self):
        """Background thread: read serial data"""
        while self.connected and self.running:
            try:
                if self.ser and self.ser.in_waiting:
                    line = self.ser.readline().decode('utf-8', errors='replace').strip()
                    if line:
                        self.root.after(0, self._process_line, line)
            except Exception as e:
                if self.connected:
                    self.root.after(0, self._log, f"❌ Read error: {e}", "error")
                    self.root.after(0, terminal_serial_log, f"{_cli_tag('err')} Serial read error: {e}")
                    self.root.after(0, self._disconnect)
                break
            time.sleep(0.01)

    def _process_line(self, line):
        """Process a line from Arduino (runs on main thread)"""
        if line.startswith("DATA:"):
            self._parse_data(line)
        elif line.startswith("LOG:"):
            self._log(line[4:], "log")
            terminal_serial_log(f"{_cli_tag('info')} {line[4:]}")
        elif line == "PONG":
            self.last_pong_time = time.time()
            self.awaiting_pong = False
        else:
            self._log(line, "info")
            terminal_serial_log(f"{_cli_tag('info')} {line}")

    def _parse_data(self, line):
        """Parse DATA stream from Arduino, with backward compatibility for old firmware."""
        try:
            parts = line[5:].split(",")
            if len(parts) >= 17:
                self.data["state"] = int(parts[0])
                self.data["valL"] = int(parts[1])
                self.data["valR"] = int(parts[2])
                self.data["selisih"] = int(parts[3])
                self.data["rataRata"] = int(parts[4])
                self.data["pos"] = int(parts[5])
                self.data["attached"] = int(parts[6])
                self.data["verifyCount"] = int(parts[7])
                self.data["simMode"] = int(parts[8])
                self.data["pin11State"] = int(parts[9])
                self.data["pin11Mode"] = int(parts[10])
                self.data["darkConfirmCount"] = int(parts[11])
                self.data["brightConfirmCount"] = int(parts[12])
                self.data["millis"] = int(parts[13])
                self.data["runMode"] = int(parts[14])
                self.data["sensorAgeMs"] = int(parts[15])
                self.data["sleepKind"] = int(parts[16])
            elif len(parts) >= 14:
                self.data["state"] = int(parts[0])
                self.data["valL"] = int(parts[1])
                self.data["valR"] = int(parts[2])
                self.data["selisih"] = int(parts[3])
                self.data["rataRata"] = int(parts[4])
                self.data["pos"] = int(parts[5])
                self.data["attached"] = int(parts[6])
                self.data["verifyCount"] = int(parts[7])
                self.data["simMode"] = int(parts[8])
                self.data["pin11State"] = int(parts[9])
                self.data["pin11Mode"] = int(parts[10])
                self.data["darkConfirmCount"] = int(parts[11])
                self.data["brightConfirmCount"] = int(parts[12])
                self.data["millis"] = int(parts[13])
                self.data["runMode"] = 1
                self.data["sensorAgeMs"] = 0
                self.data["sleepKind"] = 0
            elif len(parts) >= 12:
                self.data["state"] = int(parts[0])
                self.data["valL"] = int(parts[1])
                self.data["valR"] = int(parts[2])
                self.data["selisih"] = int(parts[3])
                self.data["rataRata"] = int(parts[4])
                self.data["pos"] = int(parts[5])
                self.data["attached"] = int(parts[6])
                self.data["verifyCount"] = int(parts[7])
                self.data["simMode"] = int(parts[8])
                self.data["pin11State"] = int(parts[9])
                self.data["pin11Mode"] = int(parts[10])
                self.data["darkConfirmCount"] = 0
                self.data["brightConfirmCount"] = 0
                self.data["millis"] = int(parts[11])
                self.data["runMode"] = 1
                self.data["sensorAgeMs"] = 0
                self.data["sleepKind"] = 0
            elif len(parts) >= 10:
                self.data["state"] = int(parts[0])
                self.data["valL"] = int(parts[1])
                self.data["valR"] = int(parts[2])
                self.data["selisih"] = int(parts[3])
                self.data["rataRata"] = int(parts[4])
                self.data["pos"] = int(parts[5])
                self.data["attached"] = int(parts[6])
                self.data["verifyCount"] = int(parts[7])
                self.data["simMode"] = int(parts[8])
                self.data["pin11State"] = 0
                self.data["pin11Mode"] = 0
                self.data["darkConfirmCount"] = 0
                self.data["brightConfirmCount"] = 0
                self.data["millis"] = int(parts[9])
                self.data["runMode"] = 1
                self.data["sensorAgeMs"] = 0
                self.data["sleepKind"] = 0
        except (ValueError, IndexError):
            pass

    # ==================== PRESENTATION ====================

    def _open_presentation(self):
        if self.pres_window and self.pres_window.alive:
            self.pres_window.win.lift()
            self.pres_window.win.focus_force()
            return
        self.pres_window = PresentationWindow(self)
        self._log("📺 Presentation window opened", "info")

    # ==================== CALLBACKS ====================

    def _init_controller_support(self):
        if pygame is None:
            self.controller_status_text = "Controller: pygame not installed"
            self.controller_status_level = "warn"
            return
        try:
            pygame.init()
            pygame.joystick.init()
            self._refresh_controller_device(log_event=False)
        except Exception as e:
            self.controller_status_text = f"Controller: init failed ({e})"
            self.controller_status_level = "error"
            self.controller_ready = False

    def _set_controller_status(self, text, level="info", log_message=None):
        self.controller_status_text = text
        self.controller_status_level = level
        if hasattr(self, "controller_status_label"):
            color = "#e2e8f0"
            if level == "good":
                color = "#22c55e"
            elif level == "warn":
                color = "#f59e0b"
            elif level == "error":
                color = "#ef4444"
            self.controller_status_label.configure(text=text, foreground=color)
        if log_message:
            self._log(log_message, "info" if level != "error" else "error")

    def _refresh_controller_device(self, log_event=True):
        if pygame is None:
            return False
        try:
            pygame.joystick.quit()
            pygame.joystick.init()
            count = pygame.joystick.get_count()
            if count <= 0:
                was_ready = self.controller_ready
                self.controller_ready = False
                self.controller_joystick = None
                self.controller_last_button_a = False
                self.controller_axis_filtered = 0.0
                self._set_controller_status(
                    "Controller: not detected",
                    "warn",
                    "Controller disconnected" if was_ready and log_event else None,
                )
                return False

            joystick = pygame.joystick.Joystick(0)
            joystick.init()
            name = joystick.get_name() or "Controller 1"
            new_name = self.controller_joystick is None or name != self.controller_joystick.get_name()
            self.controller_joystick = joystick
            self.controller_ready = True
            self._set_controller_status(
                f"Controller: {name} ready",
                "good",
                f"Controller connected: {name}" if new_name and log_event else None,
            )
            return True
        except Exception as e:
            self.controller_ready = False
            self.controller_joystick = None
            self.controller_last_button_a = False
            self.controller_axis_filtered = 0.0
            self._set_controller_status(
                f"Controller: error ({e})",
                "error",
                f"Controller error: {e}" if log_event else None,
            )
            return False

    def _set_servo_from_controller(self, target_pos):
        target_pos = int(max(SERVO_MIN, min(SERVO_MAX, target_pos)))
        self.controller_servo_float = float(target_pos)
        self.manual_servo_var.set(target_pos)
        self.manual_servo_label.configure(text=f"{target_pos}°")
        if self.controller_last_servo_sent == target_pos:
            return
        self.controller_last_servo_sent = target_pos
        self._send_cmd(f"CMD:SERVO:{target_pos}")

    def _normalize_controller_axis(self, raw_value):
        if abs(raw_value) <= CONTROLLER_DEADZONE:
            return 0.0
        magnitude = (abs(raw_value) - CONTROLLER_DEADZONE) / (1.0 - CONTROLLER_DEADZONE)
        return math.copysign(magnitude, raw_value)

    def _toggle_pin11_from_controller(self):
        next_on = not self.pin11_var.get()
        self.pin11_override_var.set(True)
        self.pin11_toggle.configure(state=tk.NORMAL)
        self.pin11_control_label.configure(text=f"Manual override aktif: {'ON' if next_on else 'OFF'}")
        self.pin11_var.set(next_on)
        self._send_cmd("CMD:PIN11:ON" if next_on else "CMD:PIN11:OFF")
        self._log(f"Pin 11 toggle dari controller -> {'ON' if next_on else 'OFF'}", "info")

    def _poll_controller(self):
        if not self.running:
            return
        if pygame is None:
            return

        try:
            pygame.event.pump()
            now = time.time()
            dt = max(0.0, min(now - self.controller_last_poll_time, 0.12))
            self.controller_last_poll_time = now

            if not self.controller_ready:
                self._refresh_controller_device(log_event=False)
            elif self.controller_joystick is None or not self.controller_joystick.get_init():
                self._refresh_controller_device(log_event=True)

            if self.controller_ready and self.controller_joystick is not None:
                axis_value = self.controller_joystick.get_axis(XBOX_LEFT_AXIS)
                normalized = self._normalize_controller_axis(axis_value)
                self.controller_axis_filtered = (
                    (1.0 - CONTROLLER_AXIS_SMOOTHING) * self.controller_axis_filtered
                    + (CONTROLLER_AXIS_SMOOTHING * normalized)
                )

                if abs(self.controller_axis_filtered) < CONTROLLER_IDLE_SYNC_EPSILON:
                    self.controller_axis_filtered = 0.0
                    self.controller_servo_float = float(self.data["pos"])
                    self.controller_last_servo_sent = self.data["pos"]
                else:
                    direction = math.copysign(abs(self.controller_axis_filtered) ** 1.6, self.controller_axis_filtered)
                    self.controller_servo_float += direction * CONTROLLER_SERVO_SPEED_DPS * dt
                    self.controller_servo_float = max(SERVO_MIN, min(SERVO_MAX, self.controller_servo_float))
                    target_pos = round(self.controller_servo_float)
                    self._set_servo_from_controller(target_pos)

                button_a = bool(self.controller_joystick.get_button(XBOX_A_BUTTON))
                if button_a and not self.controller_last_button_a:
                    self._toggle_pin11_from_controller()
                self.controller_last_button_a = button_a
        except Exception:
            self._refresh_controller_device(log_event=True)

        self.root.after(CONTROLLER_POLL_MS, self._poll_controller)

    def _on_sim_toggle(self):
        if self.sim_var.get():
            self._send_cmd("CMD:SIM_ON")
            self._log("🧪 Simulation Mode: ON", "info")
        else:
            self._send_cmd("CMD:SIM_OFF")
            self._log("🧪 Simulation Mode: OFF", "info")

    def _on_sim_slider(self, side):
        if side == "L":
            val = int(self.sim_l_var.get())
            self.sim_l_label.configure(text=str(val))
            self._send_cmd(f"CMD:SIM_L:{val}")
        else:
            val = int(self.sim_r_var.get())
            self.sim_r_label.configure(text=str(val))
            self._send_cmd(f"CMD:SIM_R:{val}")

    def _force_state(self, state):
        self._send_cmd(f"CMD:STATE:{state}")
        self._log(f"⚡ Force state -> {STATE_NAMES[state]}", "info")

    def _on_servo_slider(self):
        val = int(self.manual_servo_var.get())
        self.manual_servo_label.configure(text=f"{val}°")
        self.controller_last_servo_sent = val
        self._send_cmd(f"CMD:SERVO:{val}")

    def _servo_attach(self):
        self._send_cmd("CMD:ATTACH")
        self._log("🔌 Servo ATTACH command sent", "info")

    def _servo_detach(self):
        self._send_cmd("CMD:DETACH")
        self._log("⛔ Servo DETACH command sent", "info")

    def _on_pin11_override_toggle(self):
        if self.pin11_override_var.get():
            cmd = "CMD:PIN11:ON" if self.pin11_var.get() else "CMD:PIN11:OFF"
            self.pin11_toggle.configure(state=tk.NORMAL)
            self.pin11_control_label.configure(text="Manual override aktif")
            self._send_cmd(cmd)
            self._log(f"Pin 11 override -> {'ON' if self.pin11_var.get() else 'OFF'}", "info")
        else:
            self.pin11_toggle.configure(state=tk.DISABLED)
            self.pin11_control_label.configure(text="Mode AUTO mengikuti state alat")
            self._send_cmd("CMD:PIN11:AUTO")
            self._log("Pin 11 kembali ke AUTO", "info")

    def _on_pin11_toggle(self):
        if not self.pin11_override_var.get():
            return
        cmd = "CMD:PIN11:ON" if self.pin11_var.get() else "CMD:PIN11:OFF"
        self._send_cmd(cmd)
        self._log(f"Pin 11 -> {'ON' if self.pin11_var.get() else 'OFF'}", "info")

    def _heartbeat_loop(self):
        if not self.running:
            return
        now = time.time()
        if self.connected and (now - self.last_heartbeat_time) * 1000 >= HEARTBEAT_INTERVAL_MS:
            self._send_cmd("CMD:PING")
            self.last_heartbeat_time = now
            self.awaiting_pong = True
        self.root.after(250, self._heartbeat_loop)

    # ==================== UPDATE LOOP ====================

    def _start_update_loop(self):
        self._update_visuals()
        self._poll_controller()
        self._heartbeat_loop()

    def _update_visuals(self):
        if not self.running:
            return

        d = self.data

        # Servo
        self._draw_servo(d["pos"])
        self.servo_label.configure(text=f"Position: {d['pos']}°")
        if d["attached"]:
            self.servo_attach_label.configure(text="🟢 ATTACHED", foreground="#22c55e")
        else:
            self.servo_attach_label.configure(text="🔴 DETACHED", foreground="#ef4444")

        # State
        self._update_state_indicators(d["state"])
        uptime = d["millis"] // 1000
        detail = f"verify: {d['verifyCount']}/3 | uptime: {uptime}s"
        if d["simMode"]:
            detail += " | 🧪 SIM"
        self.state_detail_label.configure(text=detail)

        pin11_mode = d["pin11Mode"]
        pin11_on = bool(d["pin11State"])
        pin11_status = "ON" if pin11_on else "OFF"
        pin11_mode_text = PIN11_MODE_NAMES.get(pin11_mode, "UNKNOWN")
        self.pin11_status_label.configure(
            text=f"Pin 11: {pin11_status} | {pin11_mode_text}",
            foreground=("#22c55e" if pin11_on else "#ef4444")
        )
        self.pin11_override_var.set(pin11_mode != 0)
        self.pin11_var.set(pin11_on)
        self.pin11_toggle.configure(state=(tk.NORMAL if pin11_mode != 0 else tk.DISABLED))
        if pin11_mode == 0:
            self.pin11_control_label.configure(text="Mode AUTO mengikuti state alat")
        else:
            self.pin11_control_label.configure(text=f"Manual override aktif: {pin11_status}")

        self.sleep_counter_label.configure(
            text=(
                f"sleep-dark {d['darkConfirmCount']}/{DARK_CONFIRM_TARGET} | "
                f"wake-bright {d['brightConfirmCount']}/{WAKE_CONFIRM_TARGET}"
            )
        )
        self.sleep_threshold_label.configure(
            text=f"wake<{WAKE_THRESHOLD} | sleep>{SLEEP_THRESHOLD}"
        )
        run_mode_text = RUN_MODE_NAMES.get(d["runMode"], "UNKNOWN")
        sleep_kind_text = SLEEP_KIND_NAMES.get(d["sleepKind"], "SOFT")
        self.runtime_mode_label.configure(
            text=f"mode: {run_mode_text} | sleep: {sleep_kind_text}"
        )
        if self.connected and self.last_pong_time > 0:
            heartbeat_age_ms = int((time.time() - self.last_pong_time) * 1000)
            heartbeat_text = "OK" if heartbeat_age_ms <= HEARTBEAT_STALE_MS else "STALE"
        elif self.connected:
            heartbeat_text = "WAITING"
        else:
            heartbeat_text = "OFFLINE"
        self.sensor_age_label.configure(
            text=f"sensor age: {d['sensorAgeMs']}ms | heartbeat: {heartbeat_text}"
        )

        # LDR
        self._draw_ldr(d["valL"], d["valR"])
        self.ldr_info_label.configure(
            text=f"L={d['valL']}  R={d['valR']}  |  Sel: {d['selisih']}  |  Avg: {d['rataRata']}"
        )

        # Schedule next update
        self.root.after(100, self._update_visuals)

    # ==================== CLEANUP ====================

    def destroy(self):
        self.running = False
        if self.pres_window and self.pres_window.alive:
            self.pres_window._on_close()
        if pygame is not None:
            try:
                pygame.joystick.quit()
                pygame.quit()
            except Exception:
                pass
        self._disconnect()


# ======================== MAIN ========================

def main():
    run_console_intro()
    run_console_loading()
    root = tk.Tk()
    app = SolarDebugger(root)
    terminal_serial_log(f"{_cli_tag('ok')} Screen ready")

    def on_close():
        app.destroy()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
