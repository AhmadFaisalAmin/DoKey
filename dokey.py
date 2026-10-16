#!/usr/bin/env python3
"""
DoKey — دو کلید — BUILD v13
Two-player keyboard splitter with live controller preview.
"""

import sys, os, subprocess, importlib, importlib.util, threading, time, tempfile
import ctypes
from ctypes import wintypes
import queue, traceback, gc, json, struct, zipfile, shutil, urllib.request
import tkinter as tk

# =====================================================================
#  PACKAGES
# =====================================================================

REQUIRED_PACKAGES = {
    "customtkinter":   "customtkinter",
    "vgamepad":        "vgamepad",
    "arabic_reshaper": "arabic-reshaper",
    "bidi":            "python-bidi",
}

def _pip_ok():
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "--version"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=30)
        return True
    except Exception:
        return False

def _bootstrap_pip():
    if _pip_ok(): return True
    for args in (["--default-pip"], ["--user"], []):
        try:
            subprocess.check_call([sys.executable, "-m", "ensurepip"] + args,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
                                  timeout=180)
            if _pip_ok(): return True
        except Exception:
            continue
    return False

def _pip_run(args, timeout=600):
    try:
        subprocess.check_call([sys.executable, "-m", "pip"] + list(args),
                              stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
                              timeout=timeout)
        return True
    except Exception:
        return False

def _install_pkg(name):
    for s in (
        ["install", "--upgrade", "--disable-pip-version-check", name],
        ["install", "--upgrade", "--user", "--disable-pip-version-check", name],
        ["install", "--upgrade", "--no-cache-dir", "--disable-pip-version-check", name],
        ["install", "--disable-pip-version-check", name],
        ["install", "--user", "--disable-pip-version-check", name],
        ["install", "--no-cache-dir", "--disable-pip-version-check", name],
        ["install", name],
    ):
        if _pip_run(s): return True
    return False

def _verify(mod):
    try: importlib.invalidate_caches()
    except Exception: pass
    try: return importlib.util.find_spec(mod) is not None
    except Exception: return False

def ensure_packages():
    if not _bootstrap_pip():
        print("[DoKey] ERROR: pip unavailable.")
        input("Press Enter to exit...")
        sys.exit(1)
    print("[DoKey] Updating pip ...")
    _pip_run(["install", "--upgrade", "--disable-pip-version-check", "pip"])
    missing = [(m, p) for m, p in REQUIRED_PACKAGES.items() if not _verify(m)]
    if not missing:
        print("[DoKey] All packages present.")
        return
    print(f"[DoKey] Installing: {[p for _, p in missing]}")
    failed = []
    for mod, pkg in missing:
        print(f"[DoKey]   {pkg} ...", end=" ", flush=True)
        _install_pkg(pkg)
        print("OK" if _verify(mod) else "FAILED")
        if not _verify(mod): failed.append(pkg)
    fatal = [p for p in failed if p in ("customtkinter", "vgamepad")]
    if fatal:
        print(f"[DoKey] ERROR: could not install {fatal}")
        input("Press Enter to exit...")
        sys.exit(1)

# =====================================================================
#  RAW INPUT — ENUMERATION
# =====================================================================

_user32_ri = ctypes.WinDLL("user32", use_last_error=True)
RIDI_DEVICENAME  = 0x20000007
RIM_TYPEKEYBOARD = 1

class RAWINPUTDEVICELIST(ctypes.Structure):
    _fields_ = [("hDevice", ctypes.c_void_p),
                ("dwType",  ctypes.c_uint)]

_user32_ri.GetRawInputDeviceList.restype = ctypes.c_uint
_user32_ri.GetRawInputDeviceList.argtypes = [
    ctypes.POINTER(RAWINPUTDEVICELIST),
    ctypes.POINTER(ctypes.c_uint), ctypes.c_uint]
_user32_ri.GetRawInputDeviceInfoW.restype = ctypes.c_uint
_user32_ri.GetRawInputDeviceInfoW.argtypes = [
    ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_uint)]

def _ri_device_name(hdevice):
    if not hdevice: return ""
    size = ctypes.c_uint(0)
    _user32_ri.GetRawInputDeviceInfoW(hdevice, RIDI_DEVICENAME, None,
                                      ctypes.byref(size))
    if size.value == 0: return ""
    buf = ctypes.create_unicode_buffer(size.value + 1)
    ret = _user32_ri.GetRawInputDeviceInfoW(hdevice, RIDI_DEVICENAME, buf,
                                            ctypes.byref(size))
    if ret == 0xFFFFFFFF: return ""
    return buf.value

def enumerate_keyboard_paths():
    count = ctypes.c_uint(0)
    _user32_ri.GetRawInputDeviceList(None, ctypes.byref(count),
                                     ctypes.sizeof(RAWINPUTDEVICELIST))
    if count.value == 0: return []
    buf = (RAWINPUTDEVICELIST * count.value)()
    ret = _user32_ri.GetRawInputDeviceList(buf, ctypes.byref(count),
                                           ctypes.sizeof(RAWINPUTDEVICELIST))
    if ret == 0xFFFFFFFF or ret == 0: return []
    out = []
    for i in range(ret):
        d = buf[i]
        if d.dwType != RIM_TYPEKEYBOARD: continue
        p = _ri_device_name(d.hDevice)
        if p: out.append(p)
    return out

def _is_builtin_path(path):
    pu = path.upper()
    return ("ACPI" in pu) or ("PNP0303" in pu) or ("PNP03" in pu)

def count_keyboards():
    paths = enumerate_keyboard_paths()
    b = sum(1 for p in paths if _is_builtin_path(p))
    return len(paths), b, len(paths) - b

# =====================================================================
#  INTERCEPTION
# =====================================================================

IC_KEY_DOWN = 0x00
IC_KEY_UP   = 0x01
IC_KEY_E0   = 0x02
IC_KEY_E1   = 0x04
INTERCEPTION_FILTER_KEY_ALL = 0xFFFF
MAPVK_VSC_TO_VK_EX = 3

_user32_map = ctypes.windll.user32
_user32_map.MapVirtualKeyW.restype = ctypes.c_uint
_user32_map.MapVirtualKeyW.argtypes = [ctypes.c_uint, ctypes.c_uint]

def scancode_to_vk(sc, extended):
    scan = (sc | 0xE000) if extended else sc
    try: vk = int(_user32_map.MapVirtualKeyW(scan, MAPVK_VSC_TO_VK_EX))
    except Exception: vk = 0
    return vk if vk else sc

class InterceptionKeyStroke(ctypes.Structure):
    _fields_ = [("code",        ctypes.c_ushort),
                ("state",       ctypes.c_ushort),
                ("information", ctypes.c_uint)]

class InterceptionDLL:
    def __init__(self, dll_path):
        self.lib = ctypes.CDLL(dll_path)
        L = self.lib
        L.interception_create_context.restype = ctypes.c_void_p
        L.interception_create_context.argtypes = []
        L.interception_destroy_context.restype = None
        L.interception_destroy_context.argtypes = [ctypes.c_void_p]
        L.interception_wait_with_timeout.restype = ctypes.c_int
        L.interception_wait_with_timeout.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        L.interception_send.restype = ctypes.c_int
        L.interception_send.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                        ctypes.POINTER(InterceptionKeyStroke), ctypes.c_uint]
        L.interception_receive.restype = ctypes.c_int
        L.interception_receive.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                           ctypes.POINTER(InterceptionKeyStroke), ctypes.c_uint]
        L.interception_is_keyboard.restype = ctypes.c_int
        L.interception_is_keyboard.argtypes = [ctypes.c_int]
        L.interception_is_mouse.restype = ctypes.c_int
        L.interception_is_mouse.argtypes = [ctypes.c_int]
        L.interception_is_invalid.restype = ctypes.c_int
        L.interception_is_invalid.argtypes = [ctypes.c_int]
        L.interception_set_filter.restype = None
        L.interception_set_filter.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ushort]
    def create_context(self): return self.lib.interception_create_context()
    def destroy_context(self, ctx):
        try: self.lib.interception_destroy_context(ctx)
        except Exception: pass
    def set_filter(self, ctx, pred_ptr, mask): self.lib.interception_set_filter(ctx, pred_ptr, mask)
    def keyboard_predicate_ptr(self): return ctypes.cast(self.lib.interception_is_keyboard, ctypes.c_void_p)
    def wait_with_timeout(self, ctx, ms): return self.lib.interception_wait_with_timeout(ctx, ms)
    def is_keyboard(self, device): return self.lib.interception_is_keyboard(device) != 0
    def is_invalid(self, device): return self.lib.interception_is_invalid(device) != 0
    def send(self, ctx, device, stroke): return self.lib.interception_send(ctx, device, ctypes.byref(stroke), 1)
    def receive(self, ctx, device):
        stroke = InterceptionKeyStroke()
        n = self.lib.interception_receive(ctx, device, ctypes.byref(stroke), 1)
        if n < 1: return None
        return stroke

INTERCEPTION_URL = "https://github.com/oblitum/Interception/releases/download/v1.0.1/Interception.zip"
LOCALAPPDATA = os.environ.get("LOCALAPPDATA", tempfile.gettempdir())
INTERCEPTION_DIR = os.path.join(LOCALAPPDATA, "DoKey", "interception")

def _find_dll_in(folder):
    bits = struct.calcsize("P") * 8
    arch = "x64" if bits == 64 else "x86"
    for cand in [
        os.path.join(folder, "library", arch, "interception.dll"),
        os.path.join(folder, "library", arch, "Interception.dll"),
        os.path.join(folder, arch, "interception.dll"),
        os.path.join(folder, "interception.dll"),
    ]:
        if os.path.isfile(cand): return cand
    for root, _, files in os.walk(folder):
        for f in files:
            if f.lower() == "interception.dll":
                if os.path.basename(root).lower() == arch:
                    return os.path.join(root, f)
    return None

def _locate_dll():
    bits = struct.calcsize("P") * 8
    arch = "x64" if bits == 64 else "x86"
    for cand in [
        os.path.join(INTERCEPTION_DIR, arch, "interception.dll"),
        os.path.join(INTERCEPTION_DIR, "interception.dll"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "interception.dll"),
    ]:
        if os.path.isfile(cand): return cand
    return None

def _run_elevated(exe, args, wait_seconds=0):
    try:
        r = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, args, None, 1)
        if int(r) <= 32: return False
        if wait_seconds > 0: time.sleep(wait_seconds)
        return True
    except Exception:
        return False

def _driver_present():
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return (os.path.isfile(os.path.join(root, "System32", "drivers", "keyboard.sys"))
            and os.path.isfile(os.path.join(root, "System32", "drivers", "mouse.sys")))

def ensure_interception():
    existing = _locate_dll()
    if existing and _driver_present():
        print("[DoKey] Interception already installed.")
        return existing
    print("[DoKey] Setting up Interception ...")
    tmp = tempfile.gettempdir()
    zip_path = os.path.join(tmp, "Interception.zip")
    extract_dir = os.path.join(tmp, "Interception_extract")
    if not existing:
        try:
            urllib.request.urlretrieve(INTERCEPTION_URL, zip_path)
            if os.path.isdir(extract_dir):
                shutil.rmtree(extract_dir, ignore_errors=True)
            with zipfile.ZipFile(zip_path) as z:
                z.extractall(extract_dir)
        except Exception as e:
            print(f"[DoKey] Download failed: {e}")
            return None
        dll_in_zip = _find_dll_in(extract_dir)
        if not dll_in_zip:
            print("[DoKey] interception.dll not found in archive.")
            return None
        bits = struct.calcsize("P") * 8
        arch = "x64" if bits == 64 else "x86"
        dest_dir = os.path.join(INTERCEPTION_DIR, arch)
        os.makedirs(dest_dir, exist_ok=True)
        dest_dll = os.path.join(dest_dir, "interception.dll")
        try: shutil.copy2(dll_in_zip, dest_dll)
        except Exception: dest_dll = dll_in_zip
        existing = dest_dll
    else:
        extract_dir = os.path.dirname(existing)
    installer = None
    for root, _, files in os.walk(extract_dir):
        for f in files:
            if f.lower() == "install-interception.exe":
                installer = os.path.join(root, f); break
        if installer: break
    if _driver_present(): return existing
    if installer:
        print("[DoKey] Installing Interception driver (accept UAC) ...")
        if _run_elevated(installer, "/install", wait_seconds=8):
            if _driver_present():
                print("[DoKey] Driver installed. A reboot may be required.")
            else:
                print("[DoKey] Driver install may need a reboot to take effect.")
        else:
            print("[DoKey] Installer cancelled or failed. Install manually:")
            print(f"[DoKey]    {installer}  /install")
    return existing

def _test_interception(dll_path):
    if not dll_path: return False
    try:
        dll = InterceptionDLL(dll_path)
        ctx = dll.create_context()
        if not ctx: return False
        dll.destroy_context(ctx)
        return True
    except Exception:
        return False

class InterceptionListener(threading.Thread):
    def __init__(self, dll_path, on_event):
        super().__init__(daemon=True, name="InterceptionListener")
        self.dll_path = dll_path
        self.on_event = on_event
        self.stop_flag = threading.Event()
        self.ready = threading.Event()
        self.started_ok = False
        self.error = None
        self._dll = None
        self._ctx = None
    def run(self):
        try:
            self._dll = InterceptionDLL(self.dll_path)
        except Exception as e:
            self.error = f"Load failed: {e}"; self.ready.set(); return
        try:
            ctx = self._dll.create_context()
        except Exception as e:
            self.error = f"create_context: {e}"; self.ready.set(); return
        if not ctx:
            self.error = "Driver not loaded (reboot required?)"
            self.ready.set(); return
        self._ctx = ctx
        try:
            self._dll.set_filter(ctx, self._dll.keyboard_predicate_ptr(),
                                 INTERCEPTION_FILTER_KEY_ALL)
        except Exception as e:
            self.error = f"set_filter: {e}"
            try: self._dll.destroy_context(ctx)
            except Exception: pass
            self.ready.set(); return
        self.started_ok = True
        self.ready.set()
        print("[DoKey] Interception listener ready.")
        try:
            while not self.stop_flag.is_set():
                try: dev = self._dll.wait_with_timeout(ctx, 100)
                except Exception: time.sleep(0.01); continue
                if dev == 0: continue
                if self._dll.is_invalid(dev): continue
                if not self._dll.is_keyboard(dev):
                    stroke = self._dll.receive(ctx, dev)
                    if stroke is not None:
                        try: self._dll.send(ctx, dev, stroke)
                        except Exception: pass
                    continue
                stroke = self._dll.receive(ctx, dev)
                if stroke is None: continue
                raw_state = stroke.state
                is_down = (raw_state & IC_KEY_UP) == IC_KEY_DOWN
                extended = bool(raw_state & (IC_KEY_E0 | IC_KEY_E1))
                vk = scancode_to_vk(stroke.code, extended)
                block = False
                try: block = bool(self.on_event(dev, vk, is_down))
                except Exception: traceback.print_exc()
                if not block:
                    try: self._dll.send(ctx, dev, stroke)
                    except Exception: pass
        finally:
            try: self._dll.destroy_context(ctx)
            except Exception: pass
    def stop(self): self.stop_flag.set()

# =====================================================================
#  CONTROLLER PREVIEW
# =====================================================================

class ControllerPreview:
    CANVAS_W = 440
    CANVAS_H = 420

    BASE_FILL    = "#262c42"
    ACTIVE_FILL  = "#5b8def"
    OUTLINE      = "#3a4258"
    BODY_FILL    = "#1a2030"
    BODY_OUTLINE = "#3a4863"
    LABEL_FILL   = "#c8cdd8"
    ACTIVE_LABEL = "#ffffff"

    def __init__(self, parent):
        self.canvas = tk.Canvas(parent, width=self.CANVAS_W, height=self.CANVAS_H,
                                bg=P["panel"], highlightthickness=0, bd=0)
        self.canvas.pack(padx=8, pady=(4, 12))

        self.button_items = {}
        self.stick_dots = {}
        self._draw()

    def _draw(self):
        c = self.canvas
        BF = self.BASE_FILL
        OL = self.OUTLINE
        LF = self.LABEL_FILL

        # ---- LT / RT (triggers) ----
        lt_rect = c.create_rectangle(65, 8, 165, 32, fill=BF, outline=OL, width=1)
        rt_rect = c.create_rectangle(275, 8, 375, 32, fill=BF, outline=OL, width=1)
        lt_txt  = c.create_text(115, 20, text="LT", fill=LF, font=("Segoe UI", 11, "bold"))
        rt_txt  = c.create_text(325, 20, text="RT", fill=LF, font=("Segoe UI", 11, "bold"))
        self.button_items["LT"] = (lt_rect, lt_txt)
        self.button_items["RT"] = (rt_rect, rt_txt)

        # ---- LB / RB (bumpers) ----
        lb_rect = c.create_rectangle(65, 40, 165, 64, fill=BF, outline=OL, width=1)
        rb_rect = c.create_rectangle(275, 40, 375, 64, fill=BF, outline=OL, width=1)
        lb_txt  = c.create_text(115, 52, text="LB", fill=LF, font=("Segoe UI", 11, "bold"))
        rb_txt  = c.create_text(325, 52, text="RB", fill=LF, font=("Segoe UI", 11, "bold"))
        self.button_items["LB"] = (lb_rect, lb_txt)
        self.button_items["RB"] = (rb_rect, rb_txt)

        # ---- Body silhouette (moved up 18px) ----
        body_pts = [
            65, 112,
            120, 77,
            220, 67,
            320, 77,
            375, 112,
            405, 162,
            412, 222,
            405, 282,
            385, 327,
            350, 362,
            315, 370,
            295, 332,
            270, 297,
            225, 287,
            215, 287,
            170, 297,
            145, 332,
            125, 370,
            90, 362,
            55, 327,
            35, 282,
            28, 222,
            35, 162,
        ]
        c.create_polygon(body_pts, fill=self.BODY_FILL, outline=self.BODY_OUTLINE,
                         width=2, smooth=True)

        # ---- Left stick (moved up 18px) ----
        ls_circle = c.create_oval(78, 135, 122, 179, fill=BF, outline=OL, width=2)
        ls_dot    = c.create_oval(92, 149, 108, 165, fill=OL, outline="")
        self.button_items["LS Click"] = (ls_circle, None)
        self.stick_dots["L"] = ls_dot

        # ---- Right stick (moved up 18px) ----
        rs_circle = c.create_oval(288, 250, 332, 294, fill=BF, outline=OL, width=2)
        rs_dot    = c.create_oval(302, 264, 318, 280, fill=OL, outline="")
        self.button_items["RS Click"] = (rs_circle, None)
        self.stick_dots["R"] = rs_dot

        # ---- D-Pad (moved up 18px) ----
        dp_up    = c.create_rectangle(91, 250, 109, 272, fill=BF, outline="")
        dp_down  = c.create_rectangle(91, 272, 109, 294, fill=BF, outline="")
        dp_left  = c.create_rectangle(78, 263, 91, 281, fill=BF, outline="")
        dp_right = c.create_rectangle(109, 263, 122, 281, fill=BF, outline="")
        c.create_rectangle(91, 263, 109, 281, fill=BF, outline="")
        self.button_items["D-Pad Up"]    = (dp_up, None)
        self.button_items["D-Pad Down"]  = (dp_down, None)
        self.button_items["D-Pad Left"]  = (dp_left, None)
        self.button_items["D-Pad Right"] = (dp_right, None)

        # ---- Face buttons (moved up 18px) ----
        y_circle = c.create_oval(293, 110, 327, 144, fill=BF, outline=OL, width=2)
        x_circle = c.create_oval(263, 140, 297, 174, fill=BF, outline=OL, width=2)
        b_circle = c.create_oval(323, 140, 357, 174, fill=BF, outline=OL, width=2)
        a_circle = c.create_oval(293, 170, 327, 204, fill=BF, outline=OL, width=2)
        y_txt = c.create_text(310, 127, text="Y", fill=LF, font=("Segoe UI", 13, "bold"))
        x_txt = c.create_text(280, 157, text="X", fill=LF, font=("Segoe UI", 13, "bold"))
        b_txt = c.create_text(340, 157, text="B", fill=LF, font=("Segoe UI", 13, "bold"))
        a_txt = c.create_text(310, 187, text="A", fill=LF, font=("Segoe UI", 13, "bold"))
        self.button_items["Y"] = (y_circle, y_txt)
        self.button_items["X"] = (x_circle, x_txt)
        self.button_items["B"] = (b_circle, b_txt)
        self.button_items["A"] = (a_circle, a_txt)

        # ---- Back / Start (moved up 18px) ----
        back_id  = c.create_oval(183, 210, 207, 234, fill=BF, outline=OL, width=1)
        start_id = c.create_oval(213, 210, 237, 234, fill=BF, outline=OL, width=1)
        c.create_text(195, 222, text="⏴", fill=LF, font=("Segoe UI", 10, "bold"))
        c.create_text(225, 222, text="⏵", fill=LF, font=("Segoe UI", 10, "bold"))
        self.button_items["Back"]  = (back_id, None)
        self.button_items["Start"] = (start_id, None)

    def clear(self):
        self.update_from_set(set())

    def update_from_set(self, active_set):
        for control, (shape_id, label_id) in self.button_items.items():
            try:
                if control in active_set:
                    self.canvas.itemconfig(shape_id, fill=self.ACTIVE_FILL)
                    if label_id is not None:
                        self.canvas.itemconfig(label_id, fill=self.ACTIVE_LABEL)
                else:
                    self.canvas.itemconfig(shape_id, fill=self.BASE_FILL)
                    if label_id is not None:
                        self.canvas.itemconfig(label_id, fill=self.LABEL_FILL)
            except Exception:
                pass

        # Left stick dot — moved up 18px
        dx = 0; dy = 0
        if "LS Left"  in active_set: dx -= 10
        if "LS Right" in active_set: dx += 10
        if "LS Up"    in active_set: dy -= 10
        if "LS Down"  in active_set: dy += 10
        try:
            cx, cy = 100, 157
            r = 8
            self.canvas.coords(self.stick_dots["L"],
                               cx - r + dx, cy - r + dy,
                               cx + r + dx, cy + r + dy)
        except Exception:
            pass

        # Right stick dot — moved up 18px
        dx = 0; dy = 0
        if "RS Left"  in active_set: dx -= 10
        if "RS Right" in active_set: dx += 10
        if "RS Up"    in active_set: dy -= 10
        if "RS Down"  in active_set: dy += 10
        try:
            cx, cy = 310, 272
            r = 8
            self.canvas.coords(self.stick_dots["R"],
                               cx - r + dx, cy - r + dy,
                               cx + r + dx, cy + r + dy)
        except Exception:
            pass        
# =====================================================================
#  LOCALIZATION
# =====================================================================

STRINGS = {
    "en": {
        "app_title":  "DoKey — دو کلید",
        "app_name":   "⚡ DoKey",
        "app_subtitle": "Two-Player Keyboard Splitter",
        "tab_p1": "Player 1", "tab_p2": "Player 2",
        "btn_start": "▶  Start", "btn_stop": "■  Stop",
        "badge_checking": "●  CHECKING…",
        "badge_ready":    "●  PROTECTED",
        "badge_failed":   "●  NOT READY",
        "badge_running":  "●  RUNNING",
        "p1_title": "Player 1 — Built-in Keyboard",
        "p1_body": ("Player 1 uses your built-in laptop keyboard directly.\n"
                    "No virtual controller, no key remapping — the game\n"
                    "sees normal keyboard input for this player.\n\n"
                    "Configure this player in-game to 'Keyboard'.\n"
                    "Nothing to set up here."),
        "p1_note": "ℹ This tab is informational only. All configuration happens on Player 2.",
        "ext_keyboard": "⌨  External Keyboard",
        "not_detected": "— not detected yet —",
        "detect_btn":   "🎯 Detect Keyboard",
        "listening_btn":"🎯 Listening…",
        "no_kb_title":  "No keyboard detected",
        "no_kb_body":   ("Click  🎯 Detect Keyboard  above, then press any key\n"
                         "on the EXTERNAL keyboard you want Player 2 to use.\n\n"
                         "You must detect a keyboard before you can bind or\n"
                         "clear controls. The built-in laptop keyboard is\n"
                         "reserved for Player 1 and cannot be used here."),
        "no_kb_tip":    "Tip: don't press a key on your laptop keyboard during detection.",
        "layout_title": "💾  Layout", "no_layout": "— no layout —",
        "btn_save": "💾  Save", "save_as": "💾  Save As…", "btn_delete": "🗑  Delete",
        "bindings_title": "🎮  Control Bindings",
        "preview_title":  "🎮  Live Controller",
        "preview_hint":   "Lights up when a bound key is pressed",
        "btn_clear_all": "Clear All",
        "col_control": "Control", "col_bound_key": "Bound Key",
        "btn_bind": "Bind", "btn_clear": "Clear", "btn_cancel": "Cancel",
        "press_any_key": "⌨ press any key…",
        "status_ready": "Ready.",
        "status_kb_set": "✓ Player 2 keyboard detected (device #{id})",
        "status_bound": "✓ Player 2: '{key}' → {control}",
        "status_cleared": "Cleared '{control}'.",
        "status_cleared_all": "Cleared all bindings.",
        "status_layout_load_error": "Could not load layout '{name}'.",
        "status_layout_loaded": "✓ Loaded '{name}' — {n} keys | 🎯 Detect your external keyboard",
        "status_layout_unloaded": "Layout unloaded.",
        "status_layout_saved": "✓ Saved '{name}' — {n} keys",
        "status_layout_save_failed": "Failed to save layout '{name}'.",
        "status_layout_deleted": "✓ Deleted layout '{name}'.",
        "status_layout_delete_failed": "Could not delete layout '{name}'.",
        "status_no_layout_sel": "No layout selected.",
        "status_nothing_to_save": "Nothing to save — bind some keys first.",
        "status_stop_first_detect": "Stop first, then detect.",
        "status_stop_first_bind": "Stop first, then bind.",
        "status_detect_first": "🎯 Detect the external keyboard first.",
        "status_press_key_bind": "🎮 Press a key on the DETECTED external keyboard to bind '{control}' (Esc to cancel)",
        "status_press_key_bind_wrong": "⚠ Wrong keyboard — use the detected external keyboard, not the built-in one.",
        "status_detect_prompt": "🎯 Press a key on the EXTERNAL keyboard (not the built-in one)…",
        "status_builtin_detected": "⚠ That is your built-in keyboard. It is reserved for Player 1. Press a key on your EXTERNAL keyboard instead.",
        "status_only_one_keyboard": "⚠ Only one keyboard detected. Connect an external keyboard first.",
        "status_no_kb": "Detect an external keyboard first.",
        "status_no_bindings": "Bind at least one control before starting.",
        "status_ctrl_failed": "Controller creation failed: {err}",
        "status_running": "Running — virtual controller active.",
        "status_stopped": "Stopped.",
        "status_driver_missing": "⚠ Protection is not active. Please restart Windows after the driver finishes installing.",
        "device_not_connected": "⚠ Saved keyboard not connected — click 🎯 Detect",
        "dlg_save_title": "Save Layout", "dlg_save_text": "Name this layout (e.g. Football Game):",
        "dlg_overwrite_title": "Overwrite Layout?",
        "dlg_overwrite_text": "A layout named '{name}' already exists.\nOverwrite it?",
        "dlg_delete_title": "Delete Layout?",
        "dlg_delete_text": "Delete layout '{name}'?\nThis cannot be undone.",
        "dlg_ok": "OK", "dlg_yes": "Yes", "dlg_no": "No",
    },
    "fa": {
        "app_title":  "DoKey — دو کلید",
        "app_name":   "⚡ دو کلید",
        "app_subtitle": "تقسیم کننده کیبود دو نفره",
        "tab_p1": "بازیکن ۱", "tab_p2": "بازیکن ۲",
        "btn_start": "▶  شروع", "btn_stop": "■  توقف",
        "badge_checking": "●  در حال بررسی…",
        "badge_ready":    "●  محافظت شده",
        "badge_failed":   "●  آماده نیست",
        "badge_running":  "●  در حال اجرا",
        "p1_title": "بازیکن ۱ — کیبورد داخلی",
        "p1_body": ("بازیکن ۱ مستقیماً از کیبورد داخلی لپ‌تاپ شما استفاده می‌کند.\n"
                    "بدون کنترولر مجازی و بدون تغییر کلیدها.\n\n"
                    "این بازیکن را در بازی روی حالت 'Keyboard' تنظیم کنید.\n"
                    "چیزی برای تنظیم در اینجا وجود ندارد."),
        "p1_note": "ℹ این برگه فقط اطلاعاتی است. همه تنظیمات در برگه بازیکن ۲ انجام می‌شود.",
        "ext_keyboard": "⌨  کیبورد خارجی",
        "not_detected": "— هنوز شناسایی نشده —",
        "detect_btn":   "🎯 شناسایی کیبورد",
        "listening_btn":"🎯 در حال گوش دادن…",
        "no_kb_title":  "کیبوردی شناسایی نشد",
        "no_kb_body":   ("روی 🎯 شناسایی کیبورد در بالا کلیک کنید، سپس کلیدی روی\n"
                         "کیبورد خارجی مورد نظر برای بازیکن ۲ فشار دهید.\n\n"
                         "قبل از شناسایی نمی‌توانید کلیدها را متصل یا پاک کنید.\n"
                         "کیبورد داخلی لپ‌تاپ مخصوص بازیکن ۱ است."),
        "no_kb_tip":    "نکته: هنگام شناسایی، کلیدی روی کیبورد لپ‌تاپ فشار ندهید.",
        "layout_title": "💾  چیدمان", "no_layout": "— بدون چیدمان —",
        "btn_save": "💾  ذخیره", "save_as": "💾  ذخیره به نام…", "btn_delete": "🗑  حذف",
        "bindings_title": "🎮  تنظیمات کنترول",
        "preview_title":  "🎮  کنترولر زنده",
        "preview_hint":   "وقتی کلید متصل فشار داده شود روشن می‌شود",
        "btn_clear_all": "پاک کردن همه",
        "col_control": "کنترول", "col_bound_key": "کلید متصل",
        "btn_bind": "اتصال", "btn_clear": "پاک کردن", "btn_cancel": "لغو",
        "press_any_key": "⌨ هر کلیدی را فشار دهید…",
        "status_ready": "آماده.",
        "status_kb_set": "✓ کیبورد بازیکن ۲ شناسایی شد (دستگاه #{id})",
        "status_bound": "✓ بازیکن ۲: '{key}' به {control} متصل شد",
        "status_cleared": "'{control}' پاک شد.",
        "status_cleared_all": "همه اتصالات پاک شد.",
        "status_layout_load_error": "بارگذاری چیدمان '{name}' ممکن نشد.",
        "status_layout_loaded": "✓ '{name}' بارگذاری شد — {n} کلید | 🎯 کیبورد خارجی را شناسایی کنید",
        "status_layout_unloaded": "چیدمان بارگذاری نشد.",
        "status_layout_saved": "✓ '{name}' ذخیره شد — {n} کلید",
        "status_layout_save_failed": "ذخیره چیدمان '{name}' ناموفق بود.",
        "status_layout_deleted": "✓ چیدمان '{name}' حذف شد.",
        "status_layout_delete_failed": "حذف چیدمان '{name}' ممکن نشد.",
        "status_no_layout_sel": "چیدمانی انتخاب نشده.",
        "status_nothing_to_save": "چیزی برای ذخیره نیست — ابتدا کلیدها را متصل کنید.",
        "status_stop_first_detect": "ابتدا متوقف کنید، سپس شناسایی کنید.",
        "status_stop_first_bind": "ابتدا متوقف کنید، سپس اتصال دهید.",
        "status_detect_first": "🎯 ابتدا کیبورد خارجی را شناسایی کنید.",
        "status_press_key_bind": "🎮 کلیدی روی کیبورد خارجی شناسایی‌شده فشار دهید تا به '{control}' متصل شود",
        "status_press_key_bind_wrong": "⚠ کیبورد اشتباه — از کیبورد خارجی شناسایی‌شده استفاده کنید.",
        "status_detect_prompt": "🎯 کلیدی روی کیبورد خارجی فشار دهید (نه کیبورد داخلی)…",
        "status_builtin_detected": "⚠ این کیبورد داخلی شماست و مخصوص بازیکن ۱ است. کلیدی روی کیبورد خارجی فشار دهید.",
        "status_only_one_keyboard": "⚠ فقط یک کیبورد شناسایی شد. ابتدا یک کیبورد خارجی وصل کنید.",
        "status_no_kb": "ابتدا یک کیبورد خارجی شناسایی کنید.",
        "status_no_bindings": "قبل از شروع، حداقل یک کنترول متصل کنید.",
        "status_ctrl_failed": "ایجاد کنترولر ناموفق بود: {err}",
        "status_running": "در حال اجرا — کنترولر مجازی فعال است.",
        "status_stopped": "متوقف شد.",
        "status_driver_missing": "⚠ محافظت فعال نیست. پس از نصب درایور، ویندوز را ری‌استارت کنید.",
        "device_not_connected": "⚠ کیبورد ذخیره شده وصل نیست — روی 🎯 شناسایی کلیک کنید",
        "dlg_save_title": "ذخیره چیدمان", "dlg_save_text": "نام این چیدمان:",
        "dlg_overwrite_title": "بازنویسی چیدمان؟",
        "dlg_overwrite_text": "چیدمانی با نام '{name}' وجود دارد. بازنویسی شود؟",
        "dlg_delete_title": "حذف چیدمان؟",
        "dlg_delete_text": "چیدمان '{name}' حذف شود؟",
        "dlg_ok": "تأیید", "dlg_yes": "بله", "dlg_no": "خیر",
    },
}

_current_lang = "en"
_ar_reshaper = None
_bidi_get_display = None
try:
    import arabic_reshaper as _ar
    from bidi.algorithm import get_display as _gd
    _ar_reshaper = _ar; _bidi_get_display = _gd
except Exception: pass

def _reshape_rtl(s):
    if not s or _current_lang != "fa": return s
    if _ar_reshaper is None or _bidi_get_display is None: return s
    try: return _bidi_get_display(_ar_reshaper.reshape(s))
    except Exception: return s

def _translate_raw(key, **kw):
    table = STRINGS.get(_current_lang, STRINGS["en"])
    s = table.get(key) or STRINGS["en"].get(key, key)
    if kw:
        try: s = s.format(**kw)
        except Exception: pass
    return s

def t(key, **kw):     return _reshape_rtl(_translate_raw(key, **kw))
def t_raw(key, **kw): return _translate_raw(key, **kw)
def set_lang(lang):
    global _current_lang
    if lang in STRINGS: _current_lang = lang

# =====================================================================
#  PATHS, CONFIG, LAYOUTS
# =====================================================================

LOCAL_APP_DIR = os.path.join(LOCALAPPDATA, "DoKey")
LAYOUTS_DIR   = os.path.join(LOCAL_APP_DIR, "layouts")
CONFIG_FILE   = os.path.join(LOCAL_APP_DIR, "config.json")

def _read_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            v = json.load(f)
            return v if isinstance(v, dict) else {}
    except Exception: return {}

def _write_config(cfg):
    try:
        os.makedirs(LOCAL_APP_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception: pass

def get_config(key, default=None): return _read_config().get(key, default)
def set_config(key, value):
    cfg = _read_config(); cfg[key] = value; _write_config(cfg)

def _ensure_layouts_dir():
    try: os.makedirs(LAYOUTS_DIR, exist_ok=True)
    except Exception: pass

def _safe_name(name):
    for c in '<>:"/\\|?*': name = name.replace(c, "_")
    return name.strip()[:64] or "layout"

def list_layouts():
    _ensure_layouts_dir()
    try: files = os.listdir(LAYOUTS_DIR)
    except Exception: return []
    return sorted((os.path.splitext(f)[0] for f in files
                   if f.lower().endswith(".json")), key=lambda s: s.lower())

def save_layout(name, device_id, key_map):
    _ensure_layouts_dir()
    path = os.path.join(LAYOUTS_DIR, _safe_name(name) + ".json")
    data = {"name": name, "version": 4,
            "device_id": device_id, "key_to_control": key_map}
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception:
        return False

def _normalize_key_map(raw):
    if not isinstance(raw, dict): return {}
    out = {}
    for k, v in raw.items():
        ks = str(k)
        if ks.isdigit():
            ks = VK_TO_NAME.get(int(ks), f"vk_{ks}")
        out[ks] = str(v)
    return out

def load_layout(name):
    _ensure_layouts_dir()
    path = os.path.join(LAYOUTS_DIR, _safe_name(name) + ".json")
    if not os.path.isfile(path): return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception: return None
    try: version = int(data.get("version", 1))
    except Exception: version = 1
    display_name = data.get("name", name)
    if version >= 4:
        dev = data.get("device_id")
        try: dev = int(dev) if dev is not None else None
        except Exception: dev = None
        return {"name": display_name, "device_id": dev,
                "key_map": _normalize_key_map(data.get("key_to_control") or {})}
    raw_map = (data.get("p2_key_to_control") or data.get("p2_vk_to_control") or {})
    km = _normalize_key_map(raw_map)
    dev = data.get("p2_device_id")
    try: dev = int(dev) if dev is not None else None
    except Exception: dev = None
    return {"name": display_name, "device_id": dev, "key_map": km}

def delete_layout(name):
    _ensure_layouts_dir()
    path = os.path.join(LAYOUTS_DIR, _safe_name(name) + ".json")
    try:
        if os.path.isfile(path):
            os.remove(path); return True
    except Exception: pass
    return False

# =====================================================================
#  VK <-> NAME
# =====================================================================

VK_TO_NAME = {}
for i, ch in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"): VK_TO_NAME[0x41 + i] = ch
for i, ch in enumerate("0123456789"):                 VK_TO_NAME[0x30 + i] = ch
for i in range(10):                                    VK_TO_NAME[0x60 + i] = f"Numpad{i}"
for i in range(1, 25):                                 VK_TO_NAME[0x70 + (i - 1)] = f"F{i}"
VK_TO_NAME.update({
    0x08: "Backspace", 0x09: "Tab", 0x0D: "Enter", 0x10: "Shift",
    0x11: "Ctrl", 0x12: "Alt", 0x13: "Pause", 0x14: "CapsLock",
    0x1B: "Esc", 0x20: "Space", 0x21: "PageUp", 0x22: "PageDown",
    0x23: "End", 0x24: "Home", 0x25: "Left", 0x26: "Up",
    0x27: "Right", 0x28: "Down", 0x2C: "PrintScreen", 0x2D: "Insert",
    0x2E: "Delete", 0x5B: "LWin", 0x5C: "RWin", 0x5D: "Menu",
    0x6A: "NumpadMultiply", 0x6B: "NumpadAdd", 0x6D: "NumpadSubtract",
    0x6E: "NumpadDecimal", 0x6F: "NumpadDivide", 0x90: "NumLock",
    0x91: "ScrollLock", 0xA0: "LShift", 0xA1: "RShift",
    0xA2: "LCtrl", 0xA3: "RCtrl", 0xA4: "LAlt", 0xA5: "RAlt",
    0xBA: "Semicolon", 0xBB: "Equal", 0xBC: "Comma", 0xBD: "Minus",
    0xBE: "Period", 0xBF: "Slash", 0xC0: "Backquote",
    0xDB: "BracketLeft", 0xDC: "Backslash", 0xDD: "BracketRight",
    0xDE: "Quote",
})
NAME_TO_VK = {}
for code, name in VK_TO_NAME.items():
    NAME_TO_VK.setdefault(name, code)
    NAME_TO_VK.setdefault(name.lower(), code)

# =====================================================================
#  LOAD PACKAGES
# =====================================================================

ensure_packages()

import customtkinter as ctk
try: import vgamepad as vg
except Exception as e:
    print(f"[DoKey] vgamepad import failed: {e}"); sys.exit(1)

DEBUG = "--debug" in sys.argv
set_lang(get_config("language", "en") or "en")

# =====================================================================
#  XBOX BUTTONS + VIRTUAL CONTROLLER
# =====================================================================

XBOX_BUTTON_NAMES = [
    "A", "B", "X", "Y", "LB", "RB", "Start", "Back",
    "LS Click", "RS Click", "D-Pad Up", "D-Pad Down", "D-Pad Left", "D-Pad Right",
]
ANALOG_NAMES = ["LT","RT","LS Up","LS Down","LS Left","LS Right",
                "RS Up","RS Down","RS Left","RS Right"]
ALL_CONTROLS = XBOX_BUTTON_NAMES + ANALOG_NAMES

def _build_xbox_buttons():
    X = vg.XUSB_BUTTON
    return {
        "A": X.XUSB_GAMEPAD_A,  "B": X.XUSB_GAMEPAD_B,
        "X": X.XUSB_GAMEPAD_X,  "Y": X.XUSB_GAMEPAD_Y,
        "LB": X.XUSB_GAMEPAD_LEFT_SHOULDER,
        "RB": X.XUSB_GAMEPAD_RIGHT_SHOULDER,
        "Start": X.XUSB_GAMEPAD_START, "Back": X.XUSB_GAMEPAD_BACK,
        "LS Click": X.XUSB_GAMEPAD_LEFT_THUMB, "RS Click": X.XUSB_GAMEPAD_RIGHT_THUMB,
        "D-Pad Up": X.XUSB_GAMEPAD_DPAD_UP, "D-Pad Down": X.XUSB_GAMEPAD_DPAD_DOWN,
        "D-Pad Left": X.XUSB_GAMEPAD_DPAD_LEFT, "D-Pad Right": X.XUSB_GAMEPAD_DPAD_RIGHT,
    }
XBOX_BUTTONS = _build_xbox_buttons()

class VirtualController:
    def __init__(self):
        self.gamepad = vg.VX360Gamepad()
        self._buttons_down = set()
        self._analog_down = set()
        self._lock = threading.Lock()
    def button_down(self, name):
        with self._lock:
            btn = XBOX_BUTTONS.get(name)
            if btn is None or btn in self._buttons_down: return
            self._buttons_down.add(btn)
            self.gamepad.press_button(button=btn); self.gamepad.update()
    def button_up(self, name):
        with self._lock:
            btn = XBOX_BUTTONS.get(name)
            if btn is None or btn not in self._buttons_down: return
            self._buttons_down.discard(btn)
            self.gamepad.release_button(button=btn); self.gamepad.update()
    def analog_down(self, name):
        with self._lock:
            if name in self._analog_down: return
            self._analog_down.add(name); self._apply_analog()
    def analog_up(self, name):
        with self._lock:
            if name not in self._analog_down: return
            self._analog_down.discard(name); self._apply_analog()
    def _apply_analog(self):
        p = self._analog_down
        lt = 1.0 if "LT" in p else 0.0
        rt = 1.0 if "RT" in p else 0.0
        lx = (1.0 if "LS Right" in p else 0.0) - (1.0 if "LS Left" in p else 0.0)
        ly = (1.0 if "LS Up"    in p else 0.0) - (1.0 if "LS Down" in p else 0.0)
        rx = (1.0 if "RS Right" in p else 0.0) - (1.0 if "RS Left" in p else 0.0)
        ry = (1.0 if "RS Up"    in p else 0.0) - (1.0 if "RS Down" in p else 0.0)
        self.gamepad.left_trigger_float(lt); self.gamepad.right_trigger_float(rt)
        self.gamepad.left_joystick_float(x_value_float=lx, y_value_float=ly)
        self.gamepad.right_joystick_float(x_value_float=rx, y_value_float=ry)
        self.gamepad.update()
    def reset(self):
        with self._lock:
            for btn in list(self._buttons_down):
                try: self.gamepad.release_button(button=btn)
                except Exception: pass
            self._buttons_down.clear(); self._analog_down.clear()
            try:
                self.gamepad.left_joystick_float(0.0, 0.0)
                self.gamepad.right_joystick_float(0.0, 0.0)
                self.gamepad.left_trigger_float(0.0); self.gamepad.right_trigger_float(0.0)
                self.gamepad.update()
            except Exception: pass

def _dispatch(ctrl, control_name, is_down):
    if control_name in XBOX_BUTTONS:
        if is_down: ctrl.button_down(control_name)
        else:       ctrl.button_up(control_name)
    elif control_name in ANALOG_NAMES:
        if is_down: ctrl.analog_down(control_name)
        else:       ctrl.analog_up(control_name)

# =====================================================================
#  PALETTE / FONTS
# =====================================================================

P = {
    "bg": "#0d0f1a", "panel": "#161a29", "card": "#1d2233",
    "card_hover": "#242a3e", "border": "#2a3145",
    "text": "#e8eaf2", "text_dim": "#8b93a7",
    "accent": "#5b8def", "accent_hover": "#4a7ade",
    "success": "#2ecc71", "success_hover": "#27ae60",
    "danger": "#e74c3c", "danger_hover": "#c0392b",
    "binding": "#f5b041", "binding_hover": "#e59a30",
    "warn": "#f39c12",
}
FS = {
    "app_name": 30, "app_subtitle": 13, "badge": 14, "lang": 14,
    "tab": 15, "btn_main": 16, "card_title": 15, "card_text": 14,
    "card_small": 13, "section_big": 22, "body": 15, "col_header": 13,
    "control": 15, "bound_key": 14, "row_btn": 14, "status": 13,
    "dialog": 15, "dialog_btn": 14,
}

# =====================================================================
#  DIALOGS
# =====================================================================

class NameDialog(ctk.CTkToplevel):
    def __init__(self, parent, title, prompt, initial=""):
        super().__init__(parent)
        self.result = None
        self.title(title); self.geometry("500x220"); self.resizable(False, False)
        self.configure(fg_color=P["bg"])
        try: self.transient(parent); self.grab_set()
        except Exception: pass
        wrap = ctk.CTkFrame(self, fg_color=P["bg"])
        wrap.pack(fill="both", expand=True, padx=24, pady=24)
        ctk.CTkLabel(wrap, text=prompt, font=ctk.CTkFont(size=FS["dialog"]),
                     text_color=P["text"], justify="center").pack(pady=(0, 14))
        self.entry = ctk.CTkEntry(wrap, height=40, font=ctk.CTkFont(size=FS["dialog"]),
                                  fg_color=P["panel"], border_color=P["border"],
                                  text_color=P["text"])
        self.entry.pack(fill="x")
        if initial: self.entry.insert(0, initial)
        self.entry.focus_set()
        btns = ctk.CTkFrame(wrap, fg_color="transparent")
        btns.pack(fill="x", pady=(18, 0))
        ctk.CTkButton(btns, text=t("btn_cancel"), command=self._cancel,
                      fg_color=P["panel"], hover_color=P["card_hover"],
                      text_color=P["text"], font=ctk.CTkFont(size=FS["dialog_btn"]),
                      width=110, height=40).pack(side="right", padx=(8, 0))
        ctk.CTkButton(btns, text=t("dlg_ok"), command=self._ok,
                      fg_color=P["accent"], hover_color=P["accent_hover"],
                      text_color=P["text"], font=ctk.CTkFont(size=FS["dialog_btn"]),
                      width=110, height=40).pack(side="right")
        self.entry.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self._cancel())
        try: self.after(10, self.entry.focus_set); self.wait_window()
        except Exception: pass
    def _ok(self): self.result = self.entry.get().strip(); self.destroy()
    def _cancel(self): self.result = None; self.destroy()

class ConfirmDialog(ctk.CTkToplevel):
    def __init__(self, parent, title, message):
        super().__init__(parent)
        self.result = False
        self.title(title); self.geometry("500x220"); self.resizable(False, False)
        self.configure(fg_color=P["bg"])
        try: self.transient(parent); self.grab_set()
        except Exception: pass
        wrap = ctk.CTkFrame(self, fg_color=P["bg"])
        wrap.pack(fill="both", expand=True, padx=24, pady=24)
        ctk.CTkLabel(wrap, text=message, font=ctk.CTkFont(size=FS["dialog"]),
                     text_color=P["text"], justify="center",
                     wraplength=420).pack(expand=True)
        btns = ctk.CTkFrame(wrap, fg_color="transparent")
        btns.pack(fill="x", pady=(14, 0))
        ctk.CTkButton(btns, text=t("dlg_no"), command=self._no,
                      fg_color=P["panel"], hover_color=P["card_hover"],
                      text_color=P["text"], font=ctk.CTkFont(size=FS["dialog_btn"]),
                      width=120, height=40).pack(side="right", padx=(8, 0))
        ctk.CTkButton(btns, text=t("dlg_yes"), command=self._yes,
                      fg_color=P["accent"], hover_color=P["accent_hover"],
                      text_color=P["text"], font=ctk.CTkFont(size=FS["dialog_btn"]),
                      width=120, height=40).pack(side="right")
        self.bind("<Escape>", lambda e: self._no())
        try: self.wait_window()
        except Exception: pass
    def _yes(self): self.result = True; self.destroy()
    def _no(self):  self.result = False; self.destroy()

# =====================================================================
#  MAIN APP
# =====================================================================

class DoKeyApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self._alive = True
        self.selected_tab = 1

        self._state_lock = threading.Lock()
        self._state = {
            "device_id": None,
            "key_to_control": {},
            "controller": None,
            "running": False,
        }

        self.listener = None
        self._event_queue = queue.Queue()
        self._driver_ok = False
        self._dll_path = None

        try:
            total, builtin, external = count_keyboards()
            self._has_builtin = builtin > 0
            print(f"[DoKey] Keyboards: total={total}, builtin={builtin}, "
                  f"external={external}")
        except Exception as e:
            print(f"[DoKey] Keyboard enumeration failed: {e}")
            self._has_builtin = False

        self.detecting = False
        self.binding_active = False
        self.binding_control = None
        self._binding_timeout_id = None
        self._binding_timeout_ms = 15000

        self.current_layout_name = None
        self._layout_display_map = {}

        self.control_labels = {}
        self.control_rows = {}
        self.detect_btn = None

        self.layout_menu = None
        self.layout_var = None
        self.delete_layout_btn = None

        self.controller_preview = None
        self._active_controls = set()

        self.title(t_raw("app_title"))
        self.geometry("1500x1080")
        self.after(0, lambda: self.state('zoomed'))
        self.minsize(1340, 940)
        self.configure(fg_color=P["bg"])

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self._build_ui()
        self._load_last_layout_into_state()

        threading.Thread(target=self._setup_interception, daemon=True).start()
        self.after(20, self._poll_events)

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.bind("<Escape>", lambda e: self._cancel_all())

        self._rebuild_top_card()
        self._rebuild_tab_content()
        self._update_badge()

    # =================================================================
    #  Helpers
    # =================================================================

    def _player_device(self):
        with self._state_lock: return self._state["device_id"]
    def _player_key_map(self):
        with self._state_lock: return dict(self._state["key_to_control"])
    def _set_device(self, dev):
        with self._state_lock: self._state["device_id"] = dev
    def _set_key_map(self, km):
        with self._state_lock: self._state["key_to_control"] = dict(km)

    # =================================================================
    #  Interception
    # =================================================================

    def _setup_interception(self):
        dll_path = ensure_interception()
        if not dll_path:
            print("[DoKey] Interception unavailable.")
            self._driver_ok = False
            self.after(0, self._update_badge)
            self.after(0, lambda: self._set_status(t("status_driver_missing")))
            return
        if not _test_interception(dll_path):
            print("[DoKey] Interception driver not loaded (reboot may be required).")
            self._driver_ok = False
            self._dll_path = dll_path
            self.after(0, self._update_badge)
            self.after(0, lambda: self._set_status(t("status_driver_missing")))
            return
        self._dll_path = dll_path
        self._driver_ok = True
        listener = InterceptionListener(dll_path, self._on_interception_event)
        listener.start()
        listener.ready.wait(2.0)
        if not listener.started_ok:
            print(f"[DoKey] Listener failed: {listener.error}")
            self._driver_ok = False
        else:
            self.listener = listener
        self.after(0, self._update_badge)

    def _update_badge(self):
        try:
            with self._state_lock: running = self._state["running"]
            if running:
                self.driver_badge.configure(text=t("badge_running"),
                                            text_color=P["success"])
            elif self._driver_ok:
                self.driver_badge.configure(text=t("badge_ready"),
                                            text_color=P["success"])
            else:
                self.driver_badge.configure(text=t("badge_failed"),
                                            text_color=P["danger"])
        except Exception: pass

    # =================================================================
    #  Event callback (listener thread)
    # =================================================================

    def _on_interception_event(self, device_id, vk, is_down):
        try:
            self._event_queue.put_nowait((device_id, vk, is_down))
        except Exception:
            pass

        with self._state_lock:
            if not self._state["running"]:
                return False
            if self._state["device_id"] != device_id:
                return False
            kmap = self._state["key_to_control"]
        key_name = VK_TO_NAME.get(int(vk), f"vk_{int(vk)}")
        return key_name in kmap

    # =================================================================
    #  Main thread event pump
    # =================================================================

    def _poll_events(self):
        if not self._alive: return
        try:
            n = 0
            while n < 500:
                try: device_id, vk, is_down = self._event_queue.get_nowait()
                except queue.Empty: break
                n += 1
                try: self._handle_event(device_id, vk, is_down)
                except Exception: traceback.print_exc()
        finally:
            if self._alive: self.after(20, self._poll_events)

    def _handle_event(self, device_id, vk, is_down):
        key_name = VK_TO_NAME.get(int(vk), f"vk_{int(vk)}")

        # Detection
        if self.detecting and is_down:
            if self._has_builtin and device_id == 1:
                self._set_status(t("status_builtin_detected"))
                return
            self.detecting = False
            self._set_device(device_id)
            self._rebuild_tab_content()
            self._set_status(t("status_kb_set", id=device_id))
            return

        # Binding
        if self.binding_active and is_down:
            control = self.binding_control
            detected = self._player_device()
            if detected is None or device_id != detected:
                self._set_status(t("status_press_key_bind_wrong"))
                return
            if self._binding_timeout_id is not None:
                try: self.after_cancel(self._binding_timeout_id)
                except Exception: pass
                self._binding_timeout_id = None
            self.binding_active = False
            self.binding_control = None
            if control is None:
                return
            try:
                with self._state_lock:
                    cur = dict(self._state["key_to_control"])
                    cur = {k: v for k, v in cur.items() if v != control}
                    cur.pop(key_name, None)
                    cur[key_name] = control
                    self._state["key_to_control"] = cur
                    total = len(cur)
                print(f"[DoKey] BIND: '{key_name}' -> '{control}' (total {total})")
            except Exception: traceback.print_exc()
            try:
                lbl = self.control_labels.get(control)
                if lbl:
                    lbl.configure(text=key_name,
                                  fg_color=P["accent"], text_color=P["text"])
                row = self.control_rows.get(control)
                if row:
                    _, bind_btn, _ = row
                    bind_btn.configure(text=t("btn_bind"),
                                       fg_color=P["panel"],
                                       hover_color=P["card_hover"],
                                       text_color=P["text"],
                                       command=lambda c=control: self._start_binding(c))
            except Exception: traceback.print_exc()
            self._set_status(t("status_bound", key=key_name, control=control))
            return

        # Live preview + virtual controller dispatch
        with self._state_lock:
            if self._state["device_id"] != device_id: return
            kmap = dict(self._state["key_to_control"])
            running = self._state["running"]
            ctrl = self._state["controller"]

        control = kmap.get(key_name)
        if control is None:
            return

        if is_down:
            self._active_controls.add(control)
        else:
            self._active_controls.discard(control)
        if self.controller_preview is not None:
            try:
                self.controller_preview.update_from_set(self._active_controls)
            except Exception: traceback.print_exc()

        if running and ctrl is not None:
            try: _dispatch(ctrl, control, is_down)
            except Exception: traceback.print_exc()

    # =================================================================
    #  Language
    # =================================================================

    def _on_lang_toggle(self, value):
        lang = "fa" if value == "دری" else "en"
        if lang == _current_lang: return
        set_lang(lang); set_config("language", lang)
        try:
            self.title(t_raw("app_title"))
            self.title_label.configure(text=t("app_name"))
            self.subtitle_label.configure(text=t("app_subtitle"))
            tabs = [t("tab_p1"), t("tab_p2")]
            self.tabs_bar.configure(values=tabs)
            self.tabs_bar.set(tabs[self.selected_tab])
            self.start_btn.configure(text=t("btn_start"))
            self.stop_btn.configure(text=t("btn_stop"))
        except Exception: traceback.print_exc()
        self._update_badge()
        self._rebuild_top_card()
        self._rebuild_tab_content()
        self._set_status(t("status_ready"))

    # =================================================================
    #  Layout
    # =================================================================

    def _load_last_layout_into_state(self):
        name = get_config("last_layout")
        if not name: return
        layout = load_layout(name)
        if layout is None: return
        self._set_device(None)
        self._set_key_map(layout["key_map"])
        self.current_layout_name = layout["name"]

    def _do_layout_selected(self, value):
        self._cancel_all()
        real_name = self._layout_display_map.get(value, None)
        if real_name is None:
            self.current_layout_name = None
            set_config("last_layout", None)
            self._set_device(None)
            self._set_key_map({})
            self._active_controls.clear()
            self._rebuild_top_card()
            self._rebuild_tab_content()
            self._set_status(t("status_layout_unloaded"))
            return
        layout = load_layout(real_name)
        if layout is None:
            self._set_status(t("status_layout_load_error", name=real_name))
            return
        self._set_device(None)
        self._set_key_map(layout["key_map"])
        self._active_controls.clear()
        self.current_layout_name = layout["name"]
        set_config("last_layout", layout["name"])
        self._rebuild_top_card()
        self._rebuild_tab_content()
        n = len(layout["key_map"])
        self._set_status(t("status_layout_loaded",
                           name=_reshape_rtl(layout["name"]), n=n))

    def _on_save_current(self):
        self._cancel_all()
        if not self.current_layout_name:
            self._on_save_layout(); return
        km = self._player_key_map()
        if not km:
            self._set_status(t("status_nothing_to_save")); return
        if not save_layout(self.current_layout_name, self._player_device(), km):
            self._set_status(t("status_layout_save_failed",
                               name=self.current_layout_name)); return
        self._rebuild_top_card()
        self._set_status(t("status_layout_saved",
                           name=_reshape_rtl(self.current_layout_name), n=len(km)))

    def _on_save_layout(self):
        self._cancel_all()
        km = self._player_key_map()
        if not km:
            self._set_status(t("status_nothing_to_save")); return
        dlg = NameDialog(self, t("dlg_save_title"), t("dlg_save_text"),
                         initial=self.current_layout_name or "")
        name = dlg.result
        if not name: return
        existing = list_layouts()
        match = next((n for n in existing if n.lower() == name.lower()), None)
        if match is not None and match != self.current_layout_name:
            confirm = ConfirmDialog(self, t("dlg_overwrite_title"),
                                    t("dlg_overwrite_text", name=match))
            if not confirm.result: return
            name = match
        if not save_layout(name, self._player_device(), km):
            self._set_status(t("status_layout_save_failed", name=name)); return
        self.current_layout_name = name
        set_config("last_layout", name)
        self._rebuild_top_card()
        self._set_status(t("status_layout_saved",
                           name=_reshape_rtl(name), n=len(km)))

    def _on_delete_layout(self):
        self._cancel_all()
        if not self.current_layout_name:
            self._set_status(t("status_no_layout_sel")); return
        name = self.current_layout_name
        confirm = ConfirmDialog(self, t("dlg_delete_title"),
                                t("dlg_delete_text", name=_reshape_rtl(name)))
        if not confirm.result: return
        if delete_layout(name):
            self.current_layout_name = None
            if get_config("last_layout") == name: set_config("last_layout", None)
            self._rebuild_top_card()
            self._set_status(t("status_layout_deleted", name=_reshape_rtl(name)))
        else:
            self._set_status(t("status_layout_delete_failed",
                               name=_reshape_rtl(name)))

    # =================================================================
    #  UI
    # =================================================================

    def _build_ui(self):
        header = ctk.CTkFrame(self, fg_color=P["panel"], corner_radius=0, height=84)
        header.pack(fill="x"); header.pack_propagate(False)
        tb = ctk.CTkFrame(header, fg_color="transparent")
        tb.pack(side="left", padx=28, pady=14)
        self.title_label = ctk.CTkLabel(tb, text=t("app_name"),
                                        font=ctk.CTkFont(size=FS["app_name"], weight="bold"),
                                        text_color=P["text"])
        self.title_label.pack(anchor="w")
        self.subtitle_label = ctk.CTkLabel(tb, text=t("app_subtitle"),
                                           font=ctk.CTkFont(size=FS["app_subtitle"]),
                                           text_color=P["text_dim"])
        self.subtitle_label.pack(anchor="w", pady=(2, 0))
        self.driver_badge = ctk.CTkLabel(header, text=t("badge_checking"),
                                         font=ctk.CTkFont(size=FS["badge"], weight="bold"),
                                         text_color=P["text_dim"])
        self.driver_badge.pack(side="right", padx=(8, 28))
        self.lang_seg = ctk.CTkSegmentedButton(
            header, values=["EN", "دری"], command=self._on_lang_toggle,
            selected_color=P["accent"], selected_hover_color=P["accent_hover"],
            unselected_color=P["card"], unselected_hover_color=P["card_hover"],
            text_color=P["text"], font=ctk.CTkFont(size=FS["lang"], weight="bold"),
            width=130, height=38)
        self.lang_seg.pack(side="right", padx=(8, 0))
        self.lang_seg.set("دری" if _current_lang == "fa" else "EN")

        bar = ctk.CTkFrame(self, fg_color=P["panel"], corner_radius=0, height=76)
        bar.pack(fill="x"); bar.pack_propagate(False)
        inner = ctk.CTkFrame(bar, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=24, pady=14)

        self.tabs_bar = ctk.CTkSegmentedButton(
            inner, values=[t("tab_p1"), t("tab_p2")], command=self._on_tab_change,
            selected_color=P["accent"], selected_hover_color=P["accent_hover"],
            unselected_color=P["card"], unselected_hover_color=P["card_hover"],
            text_color=P["text"], font=ctk.CTkFont(size=FS["tab"], weight="bold"),
            height=42, width=320)
        self.tabs_bar.pack(side="left")
        self.tabs_bar.set(t("tab_p2"))

        self.start_btn = ctk.CTkButton(
            inner, text=t("btn_start"), command=self._on_start,
            fg_color=P["success"], hover_color=P["success_hover"],
            text_color="#0d0f1a", font=ctk.CTkFont(size=FS["btn_main"], weight="bold"),
            width=150, height=42)
        self.start_btn.pack(side="right", padx=(10, 0))
        self.stop_btn = ctk.CTkButton(
            inner, text=t("btn_stop"), command=self._on_stop,
            fg_color=P["danger"], hover_color=P["danger_hover"],
            text_color=P["text"], font=ctk.CTkFont(size=FS["btn_main"], weight="bold"),
            width=150, height=42, state="disabled")
        self.stop_btn.pack(side="right", padx=(10, 0))

        self.top_card_wrap = ctk.CTkFrame(self, fg_color="transparent", height=82)
        self.top_card_wrap.pack(fill="x", padx=24, pady=(14, 0))
        self.top_card_wrap.pack_propagate(False)

        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.pack(fill="both", expand=True, padx=24, pady=(14, 12))

        sb = ctk.CTkFrame(self, fg_color=P["panel"], corner_radius=0, height=38)
        sb.pack(fill="x", side="bottom"); sb.pack_propagate(False)
        self.status_label = ctk.CTkLabel(sb, text=t("status_ready"),
                                         font=ctk.CTkFont(size=FS["status"]),
                                         text_color=P["text_dim"])
        self.status_label.pack(side="left", padx=24)

    def _rebuild_top_card(self):
        for c in self.top_card_wrap.winfo_children():
            c.destroy()
        if self.selected_tab != 1:
            self.top_card_wrap.pack_forget()
            return
        if not self.top_card_wrap.winfo_manager():
            self.top_card_wrap.pack(fill="x", padx=24, pady=(14, 0),
                                    before=self.content)

        card = ctk.CTkFrame(self.top_card_wrap, fg_color=P["card"], corner_radius=14)
        card.pack(fill="both", expand=True)
        left = ctk.CTkFrame(card, fg_color="transparent")
        left.pack(side="left", fill="y", padx=(20, 20), pady=16)

        ctk.CTkLabel(left, text=t("layout_title"),
                     font=ctk.CTkFont(size=FS["card_title"], weight="bold"),
                     text_color=P["text"]).pack(side="left", padx=(0, 10))

        no_lbl = t("no_layout")
        names = list_layouts()
        disp = [no_lbl]; self._layout_display_map = {no_lbl: None}
        for real in names:
            d = _reshape_rtl(real); disp.append(d); self._layout_display_map[d] = real
        cur_disp = no_lbl
        if self.current_layout_name:
            for d, r in self._layout_display_map.items():
                if r == self.current_layout_name: cur_disp = d; break
        if cur_disp not in disp: cur_disp = no_lbl

        self.layout_var = ctk.StringVar(value=cur_disp)
        self.layout_menu = ctk.CTkOptionMenu(
            left, values=disp, variable=self.layout_var,
            width=260, height=40, fg_color=P["panel"],
            button_color=P["border"], button_hover_color=P["accent"],
            dropdown_fg_color=P["panel"], dropdown_hover_color=P["accent"],
            text_color=P["text"], font=ctk.CTkFont(size=FS["card_text"]),
            dropdown_font=ctk.CTkFont(size=FS["card_text"]),
            command=self._do_layout_selected)
        self.layout_menu.pack(side="left", padx=(0, 8))

        ctk.CTkButton(left, text=t("btn_save"), command=self._on_save_current,
                      fg_color=P["accent"], hover_color=P["accent_hover"],
                      text_color=P["text"], font=ctk.CTkFont(size=FS["card_text"], weight="bold"),
                      width=120, height=40).pack(side="left", padx=(0, 6))

        ctk.CTkButton(left, text=t("save_as"), command=self._on_save_layout,
                      fg_color=P["panel"], hover_color=P["card_hover"],
                      text_color=P["text"], font=ctk.CTkFont(size=FS["card_text"], weight="bold"),
                      width=150, height=40).pack(side="left", padx=(0, 6))

        self.delete_layout_btn = ctk.CTkButton(
            left, text=t("btn_delete"), command=self._on_delete_layout,
            fg_color=P["danger"], hover_color=P["danger_hover"],
            text_color=P["text"], font=ctk.CTkFont(size=FS["card_text"], weight="bold"),
            width=130, height=40)
        self.delete_layout_btn.pack(side="left")
        if cur_disp == no_lbl: self.delete_layout_btn.configure(state="disabled")

    def _rebuild_tab_content(self):
        for c in self.content.winfo_children():
            c.destroy()
        self.control_labels = {}; self.control_rows = {}; self.detect_btn = None
        self.controller_preview = None
        self._active_controls.clear()
        if self.selected_tab == 0:
            self._build_player1_info()
        else:
            self._build_player2_panel()

    def _show_tab(self, index):
        self.selected_tab = index
        self._rebuild_top_card()
        self._rebuild_tab_content()

    def _build_player1_info(self):
        card = ctk.CTkFrame(self.content, fg_color=P["card"], corner_radius=14)
        card.pack(fill="both", expand=True)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(expand=True)
        ctk.CTkLabel(inner, text="💻", font=ctk.CTkFont(size=52)).pack(pady=(50, 12))
        ctk.CTkLabel(inner, text=t("p1_title"),
                     font=ctk.CTkFont(size=FS["section_big"], weight="bold"),
                     text_color=P["text"]).pack(pady=(0, 16))
        ctk.CTkLabel(inner, text=t("p1_body"),
                     font=ctk.CTkFont(size=FS["body"]),
                     text_color=P["text_dim"], justify="center").pack(pady=(0, 24))
        ctk.CTkLabel(inner, text=t("p1_note"),
                     font=ctk.CTkFont(size=FS["card_small"]),
                     text_color=P["warn"], justify="center").pack(pady=(0, 50))

    def _build_player2_panel(self):
        dev_id = self._player_device()
        kmap = self._player_key_map()
        has_bindings = bool(kmap)
        can_edit = dev_id is not None

        kb_card = ctk.CTkFrame(self.content, fg_color=P["card"], corner_radius=14)
        kb_card.pack(fill="x", pady=(0, 14))
        kbi = ctk.CTkFrame(kb_card, fg_color="transparent")
        kbi.pack(fill="x", padx=24, pady=18)
        ctk.CTkLabel(kbi, text=t("ext_keyboard"),
                     font=ctk.CTkFont(size=FS["card_title"], weight="bold"),
                     text_color=P["text"]).pack(side="left", padx=(0, 18))
        if dev_id is None:
            if has_bindings:
                ctk.CTkLabel(kbi, text=t("device_not_connected"),
                             font=ctk.CTkFont(size=FS["card_text"], weight="bold"),
                             text_color=P["warn"]).pack(side="left")
            else:
                ctk.CTkLabel(kbi, text=t("not_detected"),
                             font=ctk.CTkFont(size=FS["card_text"]),
                             text_color=P["text_dim"]).pack(side="left")
        else:
            ctk.CTkLabel(kbi, text=f"Keyboard (device #{dev_id})",
                         font=ctk.CTkFont(size=FS["card_text"], weight="bold"),
                         text_color=P["success"]).pack(side="left")
        detect_btn = ctk.CTkButton(
            kbi, text=t("detect_btn"), command=self._start_detect,
            fg_color=P["accent"], hover_color=P["accent_hover"],
            text_color=P["text"], font=ctk.CTkFont(size=FS["card_title"], weight="bold"),
            width=210, height=42)
        detect_btn.pack(side="right")
        self.detect_btn = detect_btn

        if dev_id is None and not has_bindings:
            prompt = ctk.CTkFrame(self.content, fg_color=P["card"], corner_radius=14)
            prompt.pack(fill="both", expand=True)
            inner = ctk.CTkFrame(prompt, fg_color="transparent")
            inner.pack(expand=True)
            ctk.CTkLabel(inner, text="🎯", font=ctk.CTkFont(size=52)).pack(pady=(50, 12))
            ctk.CTkLabel(inner, text=t("no_kb_title"),
                         font=ctk.CTkFont(size=FS["section_big"], weight="bold"),
                         text_color=P["text"]).pack(pady=(0, 16))
            ctk.CTkLabel(inner, text=t("no_kb_body"),
                         font=ctk.CTkFont(size=FS["body"]),
                         text_color=P["text_dim"], justify="center").pack(pady=(0, 24))
            ctk.CTkLabel(inner, text=t("no_kb_tip"),
                         font=ctk.CTkFont(size=FS["card_small"]),
                         text_color=P["warn"], justify="center").pack(pady=(0, 50))
            return

        ctl_card = ctk.CTkFrame(self.content, fg_color=P["card"], corner_radius=14)
        ctl_card.pack(fill="both", expand=True)

        hdr = ctk.CTkFrame(ctl_card, fg_color="transparent")
        hdr.pack(fill="x", padx=24, pady=(18, 8))
        ctk.CTkLabel(hdr, text=t("bindings_title"),
                     font=ctk.CTkFont(size=FS["card_title"], weight="bold"),
                     text_color=P["text"]).pack(side="left")
        clear_all_btn = ctk.CTkButton(hdr, text=t("btn_clear_all"),
                                      command=self._clear_all_bindings,
                                      fg_color=P["panel"], hover_color=P["card_hover"],
                                      text_color=P["text"], font=ctk.CTkFont(size=FS["row_btn"]),
                                      width=130, height=34)
        clear_all_btn.pack(side="right")
        if not can_edit:
            clear_all_btn.configure(state="disabled")

        body = ctk.CTkFrame(ctl_card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=20, pady=(8, 20))

        preview_wrap = ctk.CTkFrame(body, fg_color=P["panel"], corner_radius=10,
                                    width=520, height=580)
        preview_wrap.pack(side="right", fill="y")
        preview_wrap.pack_propagate(False)

        ctk.CTkLabel(preview_wrap, text=t("preview_title"),
                     font=ctk.CTkFont(size=FS["card_title"], weight="bold"),
                     text_color=P["text"]).pack(pady=(14, 2))
        ctk.CTkLabel(preview_wrap, text=t("preview_hint"),
                     font=ctk.CTkFont(size=FS["card_small"]),
                     text_color=P["text_dim"]).pack(pady=(0, 2))

        self.controller_preview = ControllerPreview(preview_wrap)

        scroll = ctk.CTkScrollableFrame(body, fg_color=P["panel"], corner_radius=10)
        scroll.pack(side="left", fill="both", expand=True, padx=(0, 12))

        for col, text in enumerate([t("col_control"), t("col_bound_key"), "", ""]):
            ctk.CTkLabel(scroll, text=text, anchor="w",
                         font=ctk.CTkFont(size=FS["col_header"], weight="bold"),
                         text_color=P["text_dim"]).grid(
                row=0, column=col, padx=10, pady=(6, 10), sticky="w")
        for row, control in enumerate(ALL_CONTROLS, start=1):
            ctk.CTkLabel(scroll, text=control, anchor="w", width=130,
                         font=ctk.CTkFont(size=FS["control"]),
                         text_color=P["text"]).grid(
                row=row, column=0, padx=10, pady=6, sticky="w")
            key_lbl = ctk.CTkLabel(scroll, text="—", anchor="w",
                                   font=ctk.CTkFont(size=FS["bound_key"], weight="bold"),
                                   text_color=P["text_dim"], fg_color=P["card"],
                                   corner_radius=8, width=190, height=36)
            key_lbl.grid(row=row, column=1, padx=10, pady=6, sticky="w")
            bind_btn = ctk.CTkButton(
                scroll, text=t("btn_bind"), width=100, height=34,
                command=lambda c=control: self._start_binding(c),
                fg_color=P["panel"], hover_color=P["card_hover"],
                text_color=P["text"], font=ctk.CTkFont(size=FS["row_btn"]),
                border_width=1, border_color=P["border"])
            bind_btn.grid(row=row, column=2, padx=10, pady=6)
            clear_btn = ctk.CTkButton(
                scroll, text=t("btn_clear"), width=100, height=34,
                command=lambda c=control: self._clear_binding(c),
                fg_color=P["panel"], hover_color=P["card_hover"],
                text_color=P["text"], font=ctk.CTkFont(size=FS["row_btn"]),
                border_width=1, border_color=P["border"])
            clear_btn.grid(row=row, column=3, padx=10, pady=6)
            if not can_edit:
                bind_btn.configure(state="disabled")
                clear_btn.configure(state="disabled")
            self.control_labels[control] = key_lbl
            self.control_rows[control] = (key_lbl, bind_btn, clear_btn)
            for k, c in kmap.items():
                if c == control:
                    key_lbl.configure(text=k, fg_color=P["accent"], text_color=P["text"])
                    break

    # =================================================================
    #  Interaction
    # =================================================================

    def _on_tab_change(self, value):
        tabs = [t("tab_p1"), t("tab_p2")]
        try: idx = tabs.index(value)
        except ValueError: idx = 1
        self._cancel_all()
        self._show_tab(idx)

    def _start_detect(self):
        with self._state_lock: running = self._state["running"]
        if running: self._set_status(t("status_stop_first_detect")); return
        if not self._driver_ok:
            self._set_status(t("status_driver_missing")); return
        try:
            total, builtin, external = count_keyboards()
        except Exception:
            total, builtin, external = (0, 0, 0)
        if total < 2:
            self._set_status(t("status_only_one_keyboard")); return
        self._cancel_all()
        self.detecting = True
        if self.detect_btn is not None:
            try:
                self.detect_btn.configure(text=t("listening_btn"),
                                          fg_color=P["binding"],
                                          hover_color=P["binding_hover"],
                                          text_color="#0d0f1a")
            except Exception: pass
        self._set_status(t("status_detect_prompt"))

    def _start_binding(self, control):
        with self._state_lock: running = self._state["running"]
        if running: self._set_status(t("status_stop_first_bind")); return
        if self._player_device() is None:
            self._set_status(t("status_detect_first")); return
        self._cancel_all()
        self.binding_active = True
        self.binding_control = control
        try:
            self._binding_timeout_id = self.after(self._binding_timeout_ms,
                                                  self._binding_timeout)
        except Exception:
            self._binding_timeout_id = None
        row = self.control_rows.get(control)
        if row:
            key_lbl, bind_btn, _ = row
            try:
                key_lbl.configure(text=t("press_any_key"),
                                  fg_color=P["binding"], text_color="#0d0f1a")
                bind_btn.configure(text=t("btn_cancel"),
                                   fg_color=P["binding"],
                                   hover_color=P["binding_hover"],
                                   text_color="#0d0f1a",
                                   command=self._cancel_binding)
            except Exception: traceback.print_exc()
        self._set_status(t("status_press_key_bind", control=control))

    def _binding_timeout(self):
        self._binding_timeout_id = None
        if self.binding_active:
            print("[DoKey] Binding timed out.")
            self._cancel_binding()

    def _cancel_binding(self):
        if self._binding_timeout_id is not None:
            try: self.after_cancel(self._binding_timeout_id)
            except Exception: pass
            self._binding_timeout_id = None
        was_active = self.binding_active
        control = self.binding_control
        self.binding_active = False
        self.binding_control = None
        if not was_active or control is None:
            return
        row = self.control_rows.get(control)
        if not row: return
        key_lbl, bind_btn, _ = row
        try:
            kmap = self._player_key_map()
            existing = [k for k, c in kmap.items() if c == control]
            if existing:
                key_lbl.configure(text=existing[0], fg_color=P["accent"],
                                  text_color=P["text"])
            else:
                key_lbl.configure(text="—", fg_color=P["card"],
                                  text_color=P["text_dim"])
            bind_btn.configure(text=t("btn_bind"), fg_color=P["panel"],
                               hover_color=P["card_hover"], text_color=P["text"],
                               command=lambda c=control: self._start_binding(c))
        except Exception: traceback.print_exc()

    def _cancel_all(self):
        self._cancel_binding()
        if self.detecting:
            self.detecting = False
            if self.detect_btn is not None:
                try:
                    self.detect_btn.configure(text=t("detect_btn"),
                                              fg_color=P["accent"],
                                              hover_color=P["accent_hover"],
                                              text_color=P["text"])
                except Exception: pass

    def _clear_binding(self, control):
        if self._player_device() is None:
            self._set_status(t("status_detect_first")); return
        self._cancel_all()
        with self._state_lock:
            new_map = {k: v for k, v in self._state["key_to_control"].items()
                       if v != control}
            self._state["key_to_control"] = new_map
        self._active_controls.discard(control)
        if self.controller_preview is not None:
            try: self.controller_preview.update_from_set(self._active_controls)
            except Exception: pass
        lbl = self.control_labels.get(control)
        if lbl:
            try: lbl.configure(text="—", fg_color=P["card"], text_color=P["text_dim"])
            except Exception: pass
        self._set_status(t("status_cleared", control=control))

    def _clear_all_bindings(self):
        if self._player_device() is None:
            self._set_status(t("status_detect_first")); return
        self._cancel_all()
        with self._state_lock:
            self._state["key_to_control"] = {}
        self._active_controls.clear()
        if self.controller_preview is not None:
            try: self.controller_preview.clear()
            except Exception: pass
        for _, lbl in self.control_labels.items():
            try: lbl.configure(text="—", fg_color=P["card"], text_color=P["text_dim"])
            except Exception: pass
        self._set_status(t("status_cleared_all"))

    # =================================================================
    #  Start / Stop
    # =================================================================

    def _on_start(self):
        self._cancel_all()
        if not self._driver_ok or self.listener is None:
            self._set_status(t("status_driver_missing")); return
        dev = self._player_device()
        kmap = self._player_key_map()
        if dev is None:
            self._set_status(t("status_no_kb")); return
        if not kmap:
            self._set_status(t("status_no_bindings")); return

        with self._state_lock:
            old = self._state["controller"]
            self._state["controller"] = None
        if old is not None:
            try: old.reset()
            except Exception: pass
        gc.collect(); time.sleep(0.2)

        try:
            ctrl = VirtualController()
        except Exception as e:
            self._set_status(t("status_ctrl_failed", err=str(e))); return

        with self._state_lock:
            self._state["controller"] = ctrl
            self._state["running"] = True

        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self._update_badge()
        self._set_status(t("status_running"))

    def _on_stop(self):
        with self._state_lock:
            self._state["running"] = False
            ctrl = self._state["controller"]
            self._state["controller"] = None
        if ctrl is not None:
            try: ctrl.reset()
            except Exception: pass
        self._active_controls.clear()
        if self.controller_preview is not None:
            try: self.controller_preview.clear()
            except Exception: pass
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self._update_badge()
        self._set_status(t("status_stopped"))

    def _set_status(self, text):
        try: self.status_label.configure(text=text)
        except Exception: pass

    def on_close(self):
        self._alive = False
        try: self._cancel_all()
        except Exception: pass
        try: self._on_stop()
        except Exception: pass
        try:
            if self.listener is not None: self.listener.stop()
        except Exception: pass
        try: self.destroy()
        except Exception: pass

# =====================================================================
#  ENTRY
# =====================================================================

def main():
    print("[DoKey] BUILD v13 — starting.")
    try:
        test = vg.VX360Gamepad(); test.reset(); del test; gc.collect()
    except Exception as e:
        print(f"[DoKey] Cannot create virtual controller: {e}")
        print("[DoKey] Reboot after installing ViGEmBus and try again.")
        sys.exit(1)
    app = DoKeyApp()
    app.mainloop()

if __name__ == "__main__":
    try: main()
    except Exception:
        traceback.print_exc()
        input("Press Enter to exit...")