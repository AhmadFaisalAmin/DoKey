#!/usr/bin/env python3
"""
DoKey — دو کلید — BUILD v22
Two-player keyboard splitter with live controller preview.

Highlights (v22):
  - Built-in keyboard is reserved for Player 1 (rejected on Player 2 tab).
  - Persian p1_body now matches the English text exactly.
  - Live Controller panel no longer clips the bottom of the gamepad.
  - Layout card auto-sizes; Link-to-Game hint uses a larger font.

Highlights (v21):
  - Any external keyboard can be detected and bound.
  - Robust plug/unplug handling driven by RawInput count changes.
  - No false "keyboard disconnected" warnings from silence alone.
  - "Forget Keyboard" button to re-detect without restarting.
  - Driver-restart flag so Start doesn't briefly look unavailable.
  - Link-to-Game countdown is 10 s consistently (EN + FA).
  - Layout files no longer store a stale device id.

Features:
  - Multiple keys per Xbox button
  - Global hotkey (Ctrl+Shift+F9 with fallbacks) to toggle Start/Stop
  - Process-based layout auto-switch with link UI
  - System tray icon and minimize-to-tray
  - Auto-reconnect when the assigned keyboard is unplugged and replugged
"""

import sys, os, subprocess, importlib, importlib.util, threading, time, tempfile
import ctypes
from ctypes import wintypes
import queue, traceback, gc, json, struct, zipfile, shutil, urllib.request
import tkinter as tk
from tkinter import messagebox

# =====================================================================
#  PACKAGES
# =====================================================================

REQUIRED_PACKAGES = {
    "customtkinter":   "customtkinter",
    "vgamepad":        "vgamepad",
    "arabic_reshaper": "arabic-reshaper",
    "bidi":            "python-bidi",
    "pystray":         "pystray",
    "PIL":             "pillow",
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
        _hard_exit("DoKey could not bootstrap pip.")
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
        _hard_exit(f"DoKey could not install: {fatal}")

def _hard_exit(msg):
    """Exit with a message; use a dialog if there's no console."""
    print(f"[DoKey] {msg}")
    try:
        import tkinter as _tk
        from tkinter import messagebox as _mb
        _r = _tk.Tk(); _r.withdraw()
        _mb.showerror("DoKey", msg)
        _r.destroy()
    except Exception:
        try: input("Press Enter to exit...")
        except Exception: pass
    sys.exit(1)

# =====================================================================
#  RAW INPUT
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

def _is_builtin_hw(hw_id):
    if not hw_id:
        return False
    hu = hw_id.upper()
    return ("ACPI" in hu) or ("PNP0303" in hu) or ("PNP03" in hu)

def count_keyboards():
    paths = enumerate_keyboard_paths()
    b = sum(1 for p in paths if _is_builtin_path(p))
    return len(paths), b, len(paths) - b

# =====================================================================
#  FOREGROUND PROCESS DETECTION
# =====================================================================

_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32_fg = ctypes.WinDLL("user32", use_last_error=True)

_user32_fg.GetForegroundWindow.restype = wintypes.HWND
_user32_fg.GetForegroundWindow.argtypes = []
_user32_fg.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32_fg.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                                 ctypes.POINTER(wintypes.DWORD)]
_kernel32.OpenProcess.restype = ctypes.c_void_p
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
_kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
_kernel32.QueryFullProcessImageNameW.argtypes = [
    ctypes.c_void_p, wintypes.DWORD, ctypes.c_wchar_p,
    ctypes.POINTER(wintypes.DWORD)]

def get_foreground_process_name():
    try:
        hwnd = _user32_fg.GetForegroundWindow()
        if not hwnd: return ""
        pid = wintypes.DWORD(0)
        _user32_fg.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value: return ""
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,
                                   False, pid.value)
        if not h: return ""
        try:
            buf = ctypes.create_unicode_buffer(512)
            size = wintypes.DWORD(512)
            if _kernel32.QueryFullProcessImageNameW(h, 0, buf,
                                                     ctypes.byref(size)):
                return os.path.basename(buf.value)
        finally:
            _kernel32.CloseHandle(h)
    except Exception:
        pass
    return ""

# =====================================================================
#  HOTKEY WINDOW
# =====================================================================

WM_HOTKEY  = 0x0312
WM_DESTROY = 0x0002
WM_CLOSE   = 0x0010
MOD_ALT     = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT   = 0x0004

WPARAM  = ctypes.c_size_t
LPARAM  = ctypes.c_ssize_t
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                             WPARAM, LPARAM)

class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style",          ctypes.c_uint),
                ("lpfnWndProc",    WNDPROC),
                ("cbClsExtra",     ctypes.c_int),
                ("cbWndExtra",     ctypes.c_int),
                ("hInstance",      ctypes.c_void_p),
                ("hIcon",          ctypes.c_void_p),
                ("hCursor",        ctypes.c_void_p),
                ("hbrBackground",  ctypes.c_void_p),
                ("lpszMenuName",   ctypes.c_wchar_p),
                ("lpszClassName",  ctypes.c_wchar_p)]

_hu = ctypes.WinDLL("user32", use_last_error=True)
_hu.RegisterClassW.restype = ctypes.c_ushort
_hu.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
_hu.CreateWindowExW.restype = wintypes.HWND
_hu.CreateWindowExW.argtypes = [
    wintypes.DWORD, ctypes.c_wchar_p, ctypes.c_wchar_p, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
_hu.DestroyWindow.restype = wintypes.BOOL
_hu.DestroyWindow.argtypes = [wintypes.HWND]
_hu.DefWindowProcW.restype = LRESULT
_hu.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
_hu.RegisterHotKey.restype = wintypes.BOOL
_hu.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int,
                               ctypes.c_uint, ctypes.c_uint]
_hu.UnregisterHotKey.restype = wintypes.BOOL
_hu.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]

_kernel32.GetModuleHandleW.restype = ctypes.c_void_p
_kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]

WS_POPUP         = 0x80000000
WS_EX_TOOLWINDOW = 0x00000080

class HotkeyWindow:
    _counter = 0
    CANDIDATES = [
        ("Ctrl+Shift+F9",    MOD_CONTROL | MOD_SHIFT,             0x78),
        ("Ctrl+Alt+Shift+K", MOD_CONTROL | MOD_ALT | MOD_SHIFT,   0x4B),
        ("Ctrl+Shift+F8",    MOD_CONTROL | MOD_SHIFT,             0x77),
        ("Ctrl+Alt+Shift+M", MOD_CONTROL | MOD_ALT | MOD_SHIFT,   0x4D),
        ("Ctrl+Shift+F7",    MOD_CONTROL | MOD_SHIFT,             0x76),
    ]

    def __init__(self, on_hotkey):
        self.on_hotkey = on_hotkey
        self._hwnd = None
        self._wndproc = None
        self.registered_name = None
        HotkeyWindow._counter += 1
        self._class_name = f"DoKeyHotkey_{os.getpid()}_{HotkeyWindow._counter}"

        self._wndproc = WNDPROC(self._wnd_proc)
        h_instance = _kernel32.GetModuleHandleW(None)

        wc = WNDCLASSW()
        wc.style = 0
        wc.lpfnWndProc = self._wndproc
        wc.cbClsExtra = 0
        wc.cbWndExtra = 0
        wc.hInstance = h_instance
        wc.hIcon = None; wc.hCursor = None; wc.hbrBackground = None
        wc.lpszMenuName = None
        wc.lpszClassName = self._class_name

        if not _hu.RegisterClassW(ctypes.byref(wc)):
            err = ctypes.get_last_error()
            if err != 1410:
                raise OSError(f"RegisterClassW failed: {err}")

        self._hwnd = _hu.CreateWindowExW(
            WS_EX_TOOLWINDOW, self._class_name, "DoKeyHotkey",
            WS_POPUP, 0, 0, 0, 0, None, None, h_instance, None)
        if not self._hwnd:
            err = ctypes.get_last_error()
            self._cleanup_window()
            raise OSError(f"CreateWindowExW failed: {err}")

        last_err = None
        for name, mods, vk in self.CANDIDATES:
            if _hu.RegisterHotKey(self._hwnd, 1, mods, vk):
                self.registered_name = name
                print(f"[DoKey] Hotkey registered: {name}")
                return
            last_err = ctypes.get_last_error()
            print(f"[DoKey] Hotkey combo '{name}' unavailable (err {last_err})")

        self._cleanup_window()
        raise OSError(f"All hotkey candidates in use (last error {last_err})")

    def _cleanup_window(self):
        if self._hwnd:
            try: _hu.UnregisterHotKey(self._hwnd, 1)
            except Exception: pass
            try: _hu.DestroyWindow(self._hwnd)
            except Exception: pass
            self._hwnd = None

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_HOTKEY:
                try: self.on_hotkey()
                except Exception: traceback.print_exc()
                return 0
            if msg in (WM_DESTROY, WM_CLOSE):
                return 0
            return _hu.DefWindowProcW(hwnd, msg, wparam, lparam)
        except Exception:
            return 0

    def destroy(self):
        self._cleanup_window()

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
        L.interception_get_hardware_id.restype = ctypes.c_uint
        L.interception_get_hardware_id.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint]
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

    def get_hardware_id(self, ctx, device):
        try:
            buf = ctypes.create_unicode_buffer(512)
            n = self.lib.interception_get_hardware_id(
                ctx, device, buf, ctypes.sizeof(buf))
            if n > 0:
                return buf.value
        except Exception:
            pass
        return ""

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
        self._refresh_event = threading.Event()
        self._recreate_event = threading.Event()
        self.started_ok = False
        self.error = None
        self._dll = None
        self._ctx = None
        self._hw_cache = {}

    def get_hw_id(self, device):
        return self._hw_cache.get(device, "")

    def _ensure_hw_id(self, ctx, device):
        if device in self._hw_cache:
            return self._hw_cache[device]
        try:
            hw = self._dll.get_hardware_id(ctx, device)
        except Exception:
            hw = ""
        self._hw_cache[device] = hw
        return hw

    def run(self):
        try:
            self._dll = InterceptionDLL(self.dll_path)
        except Exception as e:
            self.error = f"Load failed: {e}"; self.ready.set(); return

        ctx = None
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
        last_filter_refresh = time.time()

        try:
            while not self.stop_flag.is_set():
                if self._recreate_event.is_set():
                    self._recreate_event.clear()
                    self._hw_cache.clear()
                    try: self._dll.destroy_context(ctx)
                    except Exception: pass
                    ctx = None
                    try:
                        ctx = self._dll.create_context()
                        if ctx:
                            self._dll.set_filter(
                                ctx, self._dll.keyboard_predicate_ptr(),
                                INTERCEPTION_FILTER_KEY_ALL)
                            last_filter_refresh = time.time()
                            print("[DoKey] Listener context recreated.")
                        else:
                            print("[DoKey] Context recreate returned NULL.")
                    except Exception as e:
                        print(f"[DoKey] Context recreate failed: {e}")

                if self._refresh_event.is_set():
                    self._refresh_event.clear()
                    if ctx:
                        try:
                            self._dll.set_filter(
                                ctx, self._dll.keyboard_predicate_ptr(),
                                INTERCEPTION_FILTER_KEY_ALL)
                            last_filter_refresh = time.time()
                        except Exception:
                            pass

                now = time.time()
                if now - last_filter_refresh >= 3.0 and ctx:
                    try:
                        self._dll.set_filter(
                            ctx, self._dll.keyboard_predicate_ptr(),
                            INTERCEPTION_FILTER_KEY_ALL)
                    except Exception:
                        pass
                    last_filter_refresh = now

                if ctx is None:
                    time.sleep(0.05)
                    continue

                try: dev = self._dll.wait_with_timeout(ctx, 20)
                except Exception: time.sleep(0.005); continue
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
                self._ensure_hw_id(ctx, dev)
                block = False
                try: block = bool(self.on_event(dev, vk, is_down))
                except Exception: traceback.print_exc()
                if not block:
                    try: self._dll.send(ctx, dev, stroke)
                    except Exception: pass
        finally:
            if ctx:
                try: self._dll.destroy_context(ctx)
                except Exception: pass

    def stop(self):
        self.stop_flag.set()

    def request_filter_refresh(self):
        self._refresh_event.set()

    def request_context_recreate(self):
        self._recreate_event.set()

# =====================================================================
#  CONTROLLER PREVIEW
# =====================================================================

class ControllerPreview:
    CANVAS_W = 440
    CANVAS_H = 380          # v22: was 420 — smaller so the whole
                            # body fits without clipping the bottom.

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

        # --- top shoulder buttons (LT/RT, LB/RB) ---
        lt_rect = c.create_rectangle(65, 7, 165, 29, fill=BF, outline=OL, width=1)
        rt_rect = c.create_rectangle(275, 7, 375, 29, fill=BF, outline=OL, width=1)
        lt_txt  = c.create_text(115, 18, text="LT", fill=LF, font=("Segoe UI", 11, "bold"))
        rt_txt  = c.create_text(325, 18, text="RT", fill=LF, font=("Segoe UI", 11, "bold"))
        self.button_items["LT"] = (lt_rect, lt_txt)
        self.button_items["RT"] = (rt_rect, rt_txt)

        lb_rect = c.create_rectangle(65, 36, 165, 58, fill=BF, outline=OL, width=1)
        rb_rect = c.create_rectangle(275, 36, 375, 58, fill=BF, outline=OL, width=1)
        lb_txt  = c.create_text(115, 47, text="LB", fill=LF, font=("Segoe UI", 11, "bold"))
        rb_txt  = c.create_text(325, 47, text="RB", fill=LF, font=("Segoe UI", 11, "bold"))
        self.button_items["LB"] = (lb_rect, lb_txt)
        self.button_items["RB"] = (rb_rect, rb_txt)

        # --- body outline (y values scaled ×0.9 vs v21) ---
        body_pts = [
            65, 101, 120, 69, 220, 60, 320, 69, 375, 101,
            405, 146, 412, 200, 405, 254, 385, 294, 350, 326,
            315, 333, 295, 299, 270, 267, 225, 258, 215, 258,
            170, 267, 145, 299, 125, 333, 90, 326, 55, 294,
            35, 254, 28, 200, 35, 146,
        ]
        c.create_polygon(body_pts, fill=self.BODY_FILL, outline=self.BODY_OUTLINE,
                         width=2, smooth=True)

        # --- left analog stick ---
        ls_circle = c.create_oval(78, 122, 122, 161, fill=BF, outline=OL, width=2)
        ls_dot    = c.create_oval(92, 134, 108, 149, fill=OL, outline="")
        self.button_items["LS Click"] = (ls_circle, None)
        self.stick_dots["L"] = ls_dot

        # --- right analog stick ---
        rs_circle = c.create_oval(288, 225, 332, 265, fill=BF, outline=OL, width=2)
        rs_dot    = c.create_oval(302, 238, 318, 252, fill=OL, outline="")
        self.button_items["RS Click"] = (rs_circle, None)
        self.stick_dots["R"] = rs_dot

        # --- D-Pad ---
        dp_up    = c.create_rectangle(91, 225, 109, 245, fill=BF, outline="")
        dp_down  = c.create_rectangle(91, 245, 109, 265, fill=BF, outline="")
        dp_left  = c.create_rectangle(78, 237, 91, 253, fill=BF, outline="")
        dp_right = c.create_rectangle(109, 237, 122, 253, fill=BF, outline="")
        c.create_rectangle(91, 237, 109, 253, fill=BF, outline="")
        self.button_items["D-Pad Up"]    = (dp_up, None)
        self.button_items["D-Pad Down"]  = (dp_down, None)
        self.button_items["D-Pad Left"]  = (dp_left, None)
        self.button_items["D-Pad Right"] = (dp_right, None)

        # --- face buttons (Y X B A) ---
        y_circle = c.create_oval(293, 99, 327, 130, fill=BF, outline=OL, width=2)
        x_circle = c.create_oval(263, 126, 297, 157, fill=BF, outline=OL, width=2)
        b_circle = c.create_oval(323, 126, 357, 157, fill=BF, outline=OL, width=2)
        a_circle = c.create_oval(293, 153, 327, 184, fill=BF, outline=OL, width=2)
        y_txt = c.create_text(310, 114, text="Y", fill=LF, font=("Segoe UI", 13, "bold"))
        x_txt = c.create_text(280, 141, text="X", fill=LF, font=("Segoe UI", 13, "bold"))
        b_txt = c.create_text(340, 141, text="B", fill=LF, font=("Segoe UI", 13, "bold"))
        a_txt = c.create_text(310, 168, text="A", fill=LF, font=("Segoe UI", 13, "bold"))
        self.button_items["Y"] = (y_circle, y_txt)
        self.button_items["X"] = (x_circle, x_txt)
        self.button_items["B"] = (b_circle, b_txt)
        self.button_items["A"] = (a_circle, a_txt)

        # --- Back / Start ---
        back_id  = c.create_oval(183, 189, 207, 211, fill=BF, outline=OL, width=1)
        start_id = c.create_oval(213, 189, 237, 211, fill=BF, outline=OL, width=1)
        c.create_text(195, 200, text="⏴", fill=LF, font=("Segoe UI", 10, "bold"))
        c.create_text(225, 200, text="⏵", fill=LF, font=("Segoe UI", 10, "bold"))
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

        dx = 0; dy = 0
        if "LS Left"  in active_set: dx -= 10
        if "LS Right" in active_set: dx += 10
        if "LS Up"    in active_set: dy -= 10
        if "LS Down"  in active_set: dy += 10
        try:
            cx, cy = 100, 141          # v22: was 157
            r = 8
            self.canvas.coords(self.stick_dots["L"],
                               cx - r + dx, cy - r + dy,
                               cx + r + dx, cy + r + dy)
        except Exception: pass

        dx = 0; dy = 0
        if "RS Left"  in active_set: dx -= 10
        if "RS Right" in active_set: dx += 10
        if "RS Up"    in active_set: dy -= 10
        if "RS Down"  in active_set: dy += 10
        try:
            cx, cy = 310, 245          # v22: was 272
            r = 8
            self.canvas.coords(self.stick_dots["R"],
                               cx - r + dx, cy - r + dy,
                               cx + r + dx, cy + r + dy)
        except Exception: pass

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
                    "Nothing to set up here.\n\n"
                    "The built-in keyboard is reserved for Player 1 and cannot\n"
                    "be detected or bound on the Player 2 tab."),
        "p1_note": "ℹ This tab is informational only. All configuration happens on Player 2.",
        "ext_keyboard": "⌨  Player 2 Keyboard",
        "not_detected": "— not detected yet —",
        "detect_btn":   "🎯 Detect Keyboard",
        "forget_btn":   "↺ Forget Keyboard",
        "listening_btn":"🎯 Listening…",
        "no_kb_title":  "No keyboard detected",
        "no_kb_body":   ("Click  🎯 Detect Keyboard  above, then press any key\n"
                         "on the EXTERNAL keyboard you want Player 2 to use.\n\n"
                         "You must detect an external keyboard before you can\n"
                         "bind or clear controls. The built-in laptop keyboard\n"
                         "is reserved for Player 1 and cannot be used here."),
        "no_kb_tip":    "Tip: don't press a key on your laptop keyboard during detection.",
        "layout_title": "💾  Layout", "no_layout": "— no layout —",
        "btn_save": "💾  Save", "save_as": "💾  Save As…", "btn_delete": "🗑  Delete",
        "btn_link_game": "🎮  Link to Game",
        "link_hint": ("💡 Link to Game: click this, then alt-tab to your game within 10 seconds. "
                      "DoKey will remember the game and auto-switch to this layout every time you open it."),
        "bindings_title": "🎮  Control Bindings",
        "preview_title":  "🎮  Live Controller",
        "preview_hint":   "Lights up when a bound key is pressed",
        "btn_clear_all": "Clear All",
        "col_control": "Control", "col_bound_key": "Bound Keys",
        "btn_bind": "Bind", "btn_clear": "Clear", "btn_cancel": "Cancel",
        "press_any_key": "⌨ press any key…",
        "status_ready": "Ready.",
        "status_kb_set": "✓ Player 2 keyboard detected (device #{id})",
        "status_kb_lost": "⚠ Keyboard disconnected — plug it back in or press Detect.",
        "status_kb_reconnected": "✓ Keyboard reconnected (device #{id})",
        "status_kb_forgotten": "Keyboard unlinked — press Detect to choose another.",
        "status_bound": "✓ Player 2: '{key}' → {control}",
        "status_cleared": "Cleared all keys for '{control}'.",
        "status_cleared_all": "Cleared all bindings.",
        "status_layout_load_error": "Could not load layout '{name}'.",
        "status_layout_loaded": "✓ Loaded '{name}' — {n} keys",
        "status_layout_unloaded": "Layout unloaded — bindings cleared, keyboard still detected.",
        "status_layout_saved": "✓ Saved '{name}' — {n} keys",
        "status_layout_save_failed": "Failed to save layout '{name}'.",
        "status_layout_deleted": "✓ Deleted layout '{name}'.",
        "status_layout_delete_failed": "Could not delete layout '{name}'.",
        "status_no_layout_sel": "No layout selected.",
        "status_nothing_to_save": "Nothing to save — bind some keys first.",
        "status_stop_first_detect": "Stop first, then detect.",
        "status_stop_first_bind": "Stop first, then bind.",
        "status_detect_first": "🎯 Detect a keyboard first.",
        "status_press_key_bind": "🎮 Press a key on the detected keyboard to bind to '{control}' (Esc to cancel)",
        "status_press_key_bind_wrong": "⚠ Wrong keyboard — press a key on the detected keyboard.",
        "status_detect_prompt": "🎯 Press any key on the keyboard you want Player 2 to use…",
        "status_only_one_keyboard": "⚠ Only one keyboard detected. You can still bind it — click Detect and press a key.",
        "status_no_kb": "Detect a keyboard first.",
        "status_no_bindings": "Bind at least one control before starting.",
        "status_ctrl_failed": "Controller creation failed: {err}",
        "status_running": "Running — virtual controller active.",
        "status_stopped": "Stopped.",
        "status_driver_missing": "⚠ Protection is not active. Please restart Windows after the driver finishes installing.",
        "status_reboot_needed": "⚠ Restart Windows to activate the Interception driver.",
        "status_anticheat_cancelled": "Start cancelled — anti-cheat warning not accepted.",
        "device_not_connected": "— keyboard not detected yet —",
        "status_rebind_cancelled": "Binding cancelled — key already in use.",
        "status_hotkey_registered": "Hotkey ready — {name} to toggle Start/Stop.",
        "status_hotkey_failed": "⚠ Could not register a hotkey. Start/Stop button still works.",
        "status_hotkey_not_ready": "Press Start once to accept the anti-cheat warning, then the hotkey will work.",
        "status_tray_hidden": "DoKey is still running in the system tray.",
        "status_link_prompt": "🎮 Switch to your game now — capturing in 10 seconds…",
        "status_link_cancelled": "Link cancelled — no game detected. Click 🎮 Link to Game and alt-tab to your game within 10 seconds.",
        "status_link_done": "✓ Linked '{name}' to {proc}. DoKey will auto-load this layout when you switch to that game.",
        "status_auto_switched": "🔗 Auto-loaded '{name}' for {proc}",
        "status_link_no_layout": "Save a layout first, then click 🎮 Link to Game.",
        "status_driver_restarting": "Reconnecting to keyboards…",
        "status_kb_count_changed_plug": "Keyboard plugged in — refreshing devices.",
        "status_kb_count_changed_unplug": "Keyboard removed — refreshing devices.",

        "banner_reboot": "⚠  Interception driver installed. Please restart Windows to activate it.",
        "banner_not_installed": "⚠  Interception driver is not installed. Protection will not work.",

        "anticheat_title": "Anti-Cheat Warning",
        "anticheat_msg": (
            "DoKey loads two kernel-level drivers (Interception and ViGEmBus) "
            "to block keyboard input from reaching games.\n\n"
            "Some anti-cheat systems — Easy Anti-Cheat, BattlEye, Vanguard, "
            "Ricochet, and others — may detect these drivers and flag your "
            "account, which can result in a ban.\n\n"
            "DO NOT use DoKey with competitive online games that use "
            "anti-cheat. It is intended for offline and single-player games.\n\n"
            "Yes  →  Continue and hide this warning in future\n"
            "No   →  Continue but show this warning again next time\n"
            "Cancel →  Do not start"
        ),

        "dlg_link_success_title": "Layout Linked",
        "dlg_link_success_msg": ("DoKey will now automatically load the '{name}' layout "
                                  "whenever you switch to '{proc}'.\n\n"
                                  "Just alt-tab into the game — no need to click anything in DoKey.\n\n"
                                  "To unlink, load a different layout and link it to the same game, "
                                  "or edit the layout file to remove the process name."),

        "dlg_save_title": "Save Layout", "dlg_save_text": "Name this layout (e.g. Football Game):",
        "dlg_overwrite_title": "Overwrite Layout?",
        "dlg_overwrite_text": "A layout named '{name}' already exists.\nOverwrite it?",
        "dlg_delete_title": "Delete Layout?",
        "dlg_delete_text": "Delete layout '{name}'?\nThis cannot be undone.",
        "dlg_ok": "OK", "dlg_yes": "Re-bind", "dlg_no": "Cancel",
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
                    "بدون کنترولر مجازی و بدون تغییر کلیدها — بازی\n"
                    "ورودی کیبورد عادی را برای این بازیکن می‌بیند.\n\n"
                    "این بازیکن را در بازی روی حالت 'Keyboard' تنظیم کنید.\n"
                    "چیزی برای تنظیم در اینجا وجود ندارد.\n\n"
                    "کیبورد داخلی مخصوص بازیکن ۱ است و در برگه بازیکن ۲\n"
                    "قابل شناسایی یا اتصال نیست."),
        "p1_note": "ℹ این برگه فقط اطلاعاتی است. همه تنظیمات در برگه بازیکن ۲ انجام می‌شود.",
        "ext_keyboard": "⌨  کیبورد بازیکن ۲",
        "not_detected": "— هنوز شناسایی نشده —",
        "detect_btn":   "🎯 شناسایی کیبورد",
        "forget_btn":   "↺ فراموش کردن کیبورد",
        "listening_btn":"🎯 در حال گوش دادن…",
        "no_kb_title":  "کیبوردی شناسایی نشد",
        "no_kb_body":   ("روی 🎯 شناسایی کیبورد در بالا کلیک کنید، سپس کلیدی روی\n"
                         "کیبورد خارجی مورد نظر برای بازیکن ۲ فشار دهید.\n\n"
                         "قبل از شناسایی نمی‌توانید کلیدها را متصل یا پاک کنید.\n"
                         "کیبورد داخلی لپ‌تاپ مخصوص بازیکن ۱ است و در اینجا\n"
                         "قابل استفاده نیست."),
        "no_kb_tip":    "نکته: هنگام شناسایی، کلیدی روی کیبورد لپ‌تاپ فشار ندهید.",
        "layout_title": "💾  چیدمان", "no_layout": "— بدون چیدمان —",
        "btn_save": "💾  ذخیره", "save_as": "💾  ذخیره به نام…", "btn_delete": "🗑  حذف",
        "btn_link_game": "🎮  اتصال به بازی",
        "link_hint": ("💡 اتصال به بازی: روی این کلیک کنید، سپس ظرف ۱۰ ثانیه به بازی خود بروید. "
                      "DoKey بازی را به خاطر می‌سپارد و هر بار آن بازی را باز کنید به صورت خودکار به این چیدمان سوییچ می‌کند."),
        "bindings_title": "🎮  تنظیمات کنترول",
        "preview_title":  "🎮  کنترولر زنده",
        "preview_hint":   "وقتی کلید متصل فشار داده شود روشن می‌شود",
        "btn_clear_all": "پاک کردن همه",
        "col_control": "کنترول", "col_bound_key": "کلیدهای متصل",
        "btn_bind": "اتصال", "btn_clear": "پاک کردن", "btn_cancel": "لغو",
        "press_any_key": "⌨ هر کلیدی را فشار دهید…",
        "status_ready": "آماده.",
        "status_kb_set": "✓ کیبورد بازیکن ۲ شناسایی شد (دستگاه #{id})",
        "status_kb_lost": "⚠ کیبورد قطع شد — دوباره وصل کنید یا شناسایی را بزنید.",
        "status_kb_reconnected": "✓ کیبورد دوباره وصل شد (دستگاه #{id})",
        "status_kb_forgotten": "کیبورد جدا شد — برای انتخاب کیبورد دیگر شناسایی را بزنید.",
        "status_bound": "✓ بازیکن ۲: '{key}' به {control} متصل شد",
        "status_cleared": "همه کلیدهای '{control}' پاک شد.",
        "status_cleared_all": "همه اتصالات پاک شد.",
        "status_layout_load_error": "بارگذاری چیدمان '{name}' ممکن نشد.",
        "status_layout_loaded": "✓ '{name}' بارگذاری شد — {n} کلید",
        "status_layout_unloaded": "چیدمان بارگذاری نشد — اتصالات پاک شد، کیبورد هنوز شناسایی است.",
        "status_layout_saved": "✓ '{name}' ذخیره شد — {n} کلید",
        "status_layout_save_failed": "ذخیره چیدمان '{name}' ناموفق بود.",
        "status_layout_deleted": "✓ چیدمان '{name}' حذف شد.",
        "status_layout_delete_failed": "حذف چیدمان '{name}' ممکن نشد.",
        "status_no_layout_sel": "چیدمانی انتخاب نشده.",
        "status_nothing_to_save": "چیزی برای ذخیره نیست.",
        "status_stop_first_detect": "ابتدا متوقف کنید، سپس شناسایی کنید.",
        "status_stop_first_bind": "ابتدا متوقف کنید، سپس اتصال دهید.",
        "status_detect_first": "🎯 ابتدا یک کیبورد شناسایی کنید.",
        "status_press_key_bind": "🎮 کلیدی روی کیبورد شناسایی‌شده فشار دهید تا به '{control}' متصل شود",
        "status_press_key_bind_wrong": "⚠ کیبورد اشتباه — روی کیبورد شناسایی‌شده کلید بزنید.",
        "status_detect_prompt": "🎯 هر کلیدی روی کیبوردی که می‌خواهید بازیکن ۲ استفاده کند فشار دهید…",
        "status_only_one_keyboard": "⚠ فقط یک کیبورد شناسایی شد. باز هم می‌توانید آن را متصل کنید — شناسایی را بزنید.",
        "status_no_kb": "ابتدا یک کیبورد شناسایی کنید.",
        "status_no_bindings": "قبل از شروع، حداقل یک کنترول متصل کنید.",
        "status_ctrl_failed": "ایجاد کنترولر ناموفق بود: {err}",
        "status_running": "در حال اجرا — کنترولر مجازی فعال است.",
        "status_stopped": "متوقف شد.",
        "status_driver_missing": "⚠ محافظت فعال نیست. پس از نصب درایور، ویندوز را ری‌استارت کنید.",
        "status_reboot_needed": "⚠ ویندوز را ری‌استارت کنید تا درایور فعال شود.",
        "status_anticheat_cancelled": "شروع لغو شد — هشدار ضد تقلب پذیرفته نشد.",
        "device_not_connected": "— هنوز شناسایی نشده —",
        "status_rebind_cancelled": "اتصال لغو شد — کلید قبلاً استفاده شده است.",
        "status_hotkey_registered": "میانبر آماده — {name} برای شروع/توقف.",
        "status_hotkey_failed": "⚠ ثبت میانبر ممکن نشد. دکمه Start/Stop هنوز کار می‌کند.",
        "status_hotkey_not_ready": "یک بار Start را بزنید تا هشدار ضد تقلب پذیرفته شود.",
        "status_tray_hidden": "DoKey در سینی سیستم در حال اجراست.",
        "status_link_prompt": "🎮 حالا به بازی بروید — ظرف ۱۰ ثانیه ثبت می‌شود…",
        "status_link_cancelled": "اتصال لغو شد — بازی‌ای شناسایی نشد. دوباره تلاش کنید.",
        "status_link_done": "✓ '{name}' به {proc} متصل شد. DoKey به صورت خودکار این چیدمان را بارگذاری می‌کند.",
        "status_auto_switched": "🔗 '{name}' به صورت خودکار برای {proc} بارگذاری شد",
        "status_link_no_layout": "ابتدا چیدمان را ذخیره کنید.",
        "status_driver_restarting": "در حال اتصال مجدد به کیبوردها…",
        "status_kb_count_changed_plug": "کیبورد وصل شد — دستگاه‌ها بازخوانی می‌شوند.",
        "status_kb_count_changed_unplug": "کیبورد جدا شد — دستگاه‌ها بازخوانی می‌شوند.",

        "banner_reboot": "⚠  درایور Interception نصب شد. ویندوز را ری‌استارت کنید.",
        "banner_not_installed": "⚠  درایور Interception نصب نشده است.",

        "anticheat_title": "هشدار ضد تقلب",
        "anticheat_msg": (
            "DoKey دو درایور کرنل بارگذاری می‌کند (Interception و ViGEmBus).\n\n"
            "برخی سیستم‌های ضد تقلب — Easy Anti-Cheat، BattlEye، Vanguard، "
            "Ricochet و دیگران — ممکن است این درایورها را شناسایی کنند.\n\n"
            "DoKey را با بازی‌های آنلاین رقابتی استفاده نکنید.\n\n"
            "بله   →  ادامه و پنهان کردن\n"
            "خیر   →  ادامه اما نمایش مجدد\n"
            "لغو   →  شروع نکن"
        ),

        "dlg_link_success_title": "چیدمان متصل شد",
        "dlg_link_success_msg": ("DoKey حالا چیدمان '{name}' را به صورت خودکار برای '{proc}' "
                                  "بارگذاری می‌کند.\n\n"
                                  "فقط به بازی سوئیچ کنید — نیازی به کلیک کردن در DoKey نیست."),

        "dlg_save_title": "ذخیره چیدمان", "dlg_save_text": "نام این چیدمان:",
        "dlg_overwrite_title": "بازنویسی چیدمان؟",
        "dlg_overwrite_text": "چیدمانی با نام '{name}' وجود دارد. بازنویسی شود؟",
        "dlg_delete_title": "حذف چیدمان؟",
        "dlg_delete_text": "چیدمان '{name}' حذف شود؟",
        "dlg_ok": "تأیید", "dlg_yes": "اتصال مجدد", "dlg_no": "لغو",
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

def save_layout(name, key_map, process=""):
    _ensure_layouts_dir()
    path = os.path.join(LAYOUTS_DIR, _safe_name(name) + ".json")
    data = {"name": name, "version": 6,
            "key_to_control": key_map,
            "process": process}
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
    process = data.get("process", "") or ""
    if version >= 4:
        return {"name": display_name,
                "key_map": _normalize_key_map(data.get("key_to_control") or {}),
                "process": process}
    raw_map = (data.get("p2_key_to_control") or data.get("p2_vk_to_control") or {})
    km = _normalize_key_map(raw_map)
    return {"name": display_name, "key_map": km, "process": process}

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
    print(f"[DoKey] vgamepad import failed: {e}"); _hard_exit(f"vgamepad import failed: {e}")
try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont
    HAS_TRAY = True
except Exception as e:
    print(f"[DoKey] pystray not available: {e}")
    HAS_TRAY = False

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
    "dialog": 15, "dialog_btn": 14, "banner": 13,
    "hint": 13,          # v22: was 11 — Link-to-Game hint text
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
        try:
            self.after(10, lambda: (self.lift(), self.focus_force()))
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
        self._driver_restarting = False
        self._dll_path = None

        self._driver_setup_done = False
        self._driver_setup_error = None

        self._header_frame = None
        self._reboot_banner = None

        self.hotkey_window = None
        self._hotkey_ok = False

        self.tray_icon = None
        self._tray_queue = queue.Queue()

        self._last_foreground_proc = ""
        self._current_process_name = ""

        self._anticheat_handled = bool(get_config("anticheat_ack", False))

        self._p2_last_activity = 0.0
        self._link_countdown_remaining = 0
        self._link_countdown_label = None
        self._last_kb_total = 0
        self._context_recreated_at = 0.0

        try:
            total, builtin, external = count_keyboards()
            self._has_builtin = builtin > 0
            self._last_kb_total = total
            print(f"[DoKey] Keyboards: total={total}, builtin={builtin}, "
                  f"external={external}")
        except Exception as e:
            print(f"[DoKey] Keyboard enumeration failed: {e}")
            self._has_builtin = False
            self._last_kb_total = 0

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
        self.forget_btn = None

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

        threading.Thread(target=self._setup_interception_worker,
                         daemon=True, name="DriverSetup").start()

        self.after(5, self._poll_events)
        self.after(200, self._poll_driver_setup)
        self.after(500, self._setup_hotkey)
        self.after(1000, self._poll_foreground_process)
        self.after(100, self._create_tray)
        self.after(200, self._poll_tray_queue)
        self.after(1000, self._check_kb_count)

        self.protocol("WM_DELETE_WINDOW", self._on_window_close)
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
    #  Banner
    # =================================================================

    def _show_banner(self, text):
        if self._reboot_banner is not None:
            try:
                for child in self._reboot_banner.winfo_children():
                    try: child.configure(text=text)
                    except Exception: pass
            except Exception: pass
            return
        try:
            banner = ctk.CTkFrame(self, fg_color="#c0392b", corner_radius=0, height=52)
            if self._header_frame is not None:
                banner.pack(fill="x", after=self._header_frame)
            else:
                banner.pack(fill="x")
            banner.pack_propagate(False)
            ctk.CTkLabel(
                banner, text=text,
                font=ctk.CTkFont(size=FS["banner"], weight="bold"),
                text_color="#ffffff",
            ).pack(side="left", padx=24, pady=14)
            self._reboot_banner = banner
        except Exception:
            traceback.print_exc()

    def _hide_banner(self):
        if self._reboot_banner is None: return
        try: self._reboot_banner.destroy()
        except Exception: pass
        self._reboot_banner = None

    # =================================================================
    #  Interception
    # =================================================================

    def _setup_interception_worker(self):
        try:
            dll_path = ensure_interception()
            if not dll_path:
                print("[DoKey] Interception unavailable.")
                self._driver_ok = False
                self._driver_setup_error = "unavailable"
                return
            if not _test_interception(dll_path):
                print("[DoKey] Interception driver not loaded (reboot may be required).")
                self._driver_ok = False
                self._dll_path = dll_path
                self._driver_setup_error = "not_loaded"
                return
            self._dll_path = dll_path
            listener = InterceptionListener(dll_path, self._on_interception_event)
            listener.start()
            listener.ready.wait(2.0)
            if not listener.started_ok:
                print(f"[DoKey] Listener failed: {listener.error}")
                self._driver_ok = False
                self._driver_setup_error = f"listener_failed: {listener.error}"
            else:
                self.listener = listener
                self._driver_ok = True
                self._driver_setup_error = None
        except Exception as e:
            print(f"[DoKey] Driver setup exception: {e}")
            self._driver_ok = False
            self._driver_setup_error = f"exception: {e}"
        finally:
            self._driver_setup_done = True

    def _poll_driver_setup(self):
        if not self._alive: return
        if not self._driver_setup_done:
            self.after(200, self._poll_driver_setup)
            return
        self._update_badge()
        if self._driver_ok:
            self._hide_banner()
            print("[DoKey] Driver setup complete — running.")
            return
        if _driver_present():
            self._show_banner(t("banner_reboot"))
            self._set_status(t("status_reboot_needed"))
        else:
            self._show_banner(t("banner_not_installed"))
            self._set_status(t("status_driver_missing"))

    def _restart_listener(self):
        if self._driver_restarting:
            return
        print("[DoKey] Restarting Interception listener ...")
        self._driver_restarting = True
        if self.listener is not None:
            try: self.listener.stop()
            except Exception: pass
            try: self.listener.join(timeout=2.0)
            except Exception: pass
            self.listener = None
        self._driver_ok = False
        self._set_status(t("status_driver_restarting"))
        threading.Thread(target=self._restart_listener_worker,
                         daemon=True, name="ListenerRestart").start()

    def _restart_listener_worker(self):
        try:
            if not self._dll_path:
                return
            listener = InterceptionListener(self._dll_path,
                                             self._on_interception_event)
            listener.start()
            listener.ready.wait(3.0)
            if listener.started_ok:
                self.listener = listener
                self._driver_ok = True
                self._p2_last_activity = time.time()
                print("[DoKey] Interception listener restarted.")
            else:
                print(f"[DoKey] Listener restart failed: {listener.error}")
        except Exception as e:
            print(f"[DoKey] Restart exception: {e}")
            traceback.print_exc()
        finally:
            self._driver_restarting = False

    def _update_badge(self):
        try:
            with self._state_lock: running = self._state["running"]
            if running:
                self.driver_badge.configure(text=t("badge_running"),
                                            text_color=P["success"])
            elif self._driver_restarting:
                self.driver_badge.configure(text=t("badge_checking"),
                                            text_color=P["warn"])
            elif self._driver_ok:
                self.driver_badge.configure(text=t("badge_ready"),
                                            text_color=P["success"])
            else:
                self.driver_badge.configure(text=t("badge_failed"),
                                            text_color=P["danger"])
        except Exception: pass

    # =================================================================
    #  Hotkey
    # =================================================================

    def _setup_hotkey(self):
        try:
            self.hotkey_window = HotkeyWindow(self._on_hotkey_pressed)
            self._hotkey_ok = True
            name = self.hotkey_window.registered_name or "hotkey"
            self._set_status(t("status_hotkey_registered", name=name))
        except OSError as e:
            self._hotkey_ok = False
            print(f"[DoKey] Hotkey registration failed: {e}")
            self._set_status(t("status_hotkey_failed"))
        except Exception:
            self._hotkey_ok = False
            traceback.print_exc()

    def _on_hotkey_pressed(self):
        try:
            self._tray_queue.put("toggle_startstop")
        except Exception:
            pass

    def _do_toggle_startstop(self):
        if not self._anticheat_handled:
            self._set_status(t("status_hotkey_not_ready"))
            return
        with self._state_lock:
            running = self._state["running"]
        if running:
            self._on_stop()
        else:
            self._on_start()

    # =================================================================
    #  System tray
    # =================================================================

    def _create_tray(self):
        if not HAS_TRAY: return
        try:
            img = Image.new("RGBA", (64, 64), (13, 15, 26, 255))
            d = ImageDraw.Draw(img)
            d.rounded_rectangle([4, 4, 60, 60], radius=12,
                                fill=(29, 34, 51, 255),
                                outline=(91, 141, 239, 255), width=3)
            try:
                font = ImageFont.truetype("arial.ttf", 28)
            except Exception:
                font = ImageFont.load_default()
            d.text((14, 16), "DK", fill=(232, 234, 242, 255), font=font)

            menu = pystray.Menu(
                pystray.MenuItem("Show / Hide",
                                 lambda *a: self._tray_queue.put("toggle"),
                                 default=True),
                pystray.MenuItem("Start",
                                 lambda *a: self._tray_queue.put("start")),
                pystray.MenuItem("Stop",
                                 lambda *a: self._tray_queue.put("stop")),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit",
                                 lambda *a: self._tray_queue.put("quit")),
            )
            self.tray_icon = pystray.Icon("DoKey", img, "DoKey", menu)
            threading.Thread(target=self.tray_icon.run,
                             daemon=True, name="TrayIcon").start()
            print("[DoKey] System tray icon created.")
        except Exception:
            traceback.print_exc()
            self.tray_icon = None

    def _poll_tray_queue(self):
        if not self._alive: return
        try:
            n = 0
            while n < 50:
                try: action = self._tray_queue.get_nowait()
                except queue.Empty: break
                n += 1
                try:
                    if action == "toggle":
                        self._toggle_window_visibility()
                    elif action == "start":
                        if self._anticheat_handled:
                            self._on_start()
                        else:
                            self._set_status(t("status_hotkey_not_ready"))
                    elif action == "stop":
                        self._on_stop()
                    elif action == "toggle_startstop":
                        self._do_toggle_startstop()
                    elif action == "quit":
                        self._real_quit()
                        return
                except Exception:
                    traceback.print_exc()
        finally:
            if self._alive:
                self.after(200, self._poll_tray_queue)

    def _toggle_window_visibility(self):
        try:
            if self.state() == "withdrawn":
                self.deiconify()
                self.lift()
                self.focus_force()
            else:
                self.withdraw()
                self._set_status(t("status_tray_hidden"))
        except Exception:
            traceback.print_exc()

    def _on_window_close(self):
        try:
            self.withdraw()
            print("[DoKey] Window hidden to tray.")
        except Exception:
            traceback.print_exc()

    def _real_quit(self):
        print("[DoKey] Shutting down ...")
        self._alive = False
        try:
            if self.tray_icon:
                self.tray_icon.stop()
        except Exception: pass
        try: self._cancel_all()
        except Exception: pass
        try: self._on_stop()
        except Exception: pass
        try:
            if self.listener is not None:
                self.listener.stop()
                self.listener.join(timeout=3.0)
        except Exception:
            pass
        try:
            if self.hotkey_window is not None: self.hotkey_window.destroy()
        except Exception: pass
        try: self.destroy()
        except Exception: pass

    # =================================================================
    #  Foreground process polling
    # =================================================================

    def _poll_foreground_process(self):
        if not self._alive: return
        try:
            proc = get_foreground_process_name()
            proc_lower = proc.lower() if proc else ""
            if proc_lower != self._last_foreground_proc:
                self._last_foreground_proc = proc_lower
                if proc and "python" not in proc_lower and "dokey" not in proc_lower:
                    self._maybe_auto_switch(proc_lower, proc)
        except Exception:
            pass
        if self._alive:
            self.after(1000, self._poll_foreground_process)

    def _maybe_auto_switch(self, proc_lower, proc_orig):
        if self._link_countdown_remaining > 0:
            return
        for name in list_layouts():
            layout = load_layout(name)
            if not layout: continue
            stored = (layout.get("process") or "").lower()
            if stored and stored == proc_lower:
                if self.current_layout_name != name:
                    self._auto_load_layout(name, proc_orig)
                return

    def _auto_load_layout(self, name, proc):
        layout = load_layout(name)
        if not layout: return
        self._set_key_map(layout["key_map"])
        self.current_layout_name = name
        self._current_process_name = proc
        set_config("last_layout", name)
        self._rebuild_top_card()
        self._rebuild_tab_content()
        self._set_status(t("status_auto_switched", name=_reshape_rtl(name),
                           proc=proc))
        print(f"[DoKey] Auto-switched to layout '{name}' (process {proc}).")

    # =================================================================
    #  Keyboard count watcher (plug / unplug)
    # =================================================================

    def _check_kb_count(self):
        if not self._alive: return
        try:
            try:
                total, builtin, external = count_keyboards()
            except Exception:
                total = self._last_kb_total

            if total != self._last_kb_total:
                old = self._last_kb_total
                self._last_kb_total = total
                if total > old:
                    print(f"[DoKey] Keyboard plugged in ({old} -> {total}).")
                    self._set_status(t("status_kb_count_changed_plug"))
                else:
                    print(f"[DoKey] Keyboard removed ({old} -> {total}).")
                    self._set_status(t("status_kb_count_changed_unplug"))

                self._context_recreated_at = time.time()
                if self.listener is not None and self._driver_ok:
                    try:
                        self.listener.request_context_recreate()
                    except Exception:
                        self._restart_listener()
                else:
                    self._restart_listener()
        except Exception:
            traceback.print_exc()
        if self._alive:
            self.after(250, self._check_kb_count)

    # =================================================================
    #  Event callback (listener thread)
    # =================================================================

    def _on_interception_event(self, device_id, vk, is_down):
        key_name = VK_TO_NAME.get(int(vk), f"vk_{int(vk)}")

        with self._state_lock:
            running = self._state["running"]
            match = running and self._state["device_id"] == device_id
            if match:
                kmap = self._state["key_to_control"]
                ctrl = self._state["controller"]
                control = kmap.get(key_name)
            else:
                control = None
                ctrl = None

        if match and control is not None and ctrl is not None:
            try:
                _dispatch(ctrl, control, is_down)
            except Exception:
                traceback.print_exc()

        try:
            self._event_queue.put_nowait((device_id, vk, is_down))
        except Exception:
            pass

        return bool(match and control is not None)

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
            if self._alive: self.after(5, self._poll_events)

    def _handle_event(self, device_id, vk, is_down):
        key_name = VK_TO_NAME.get(int(vk), f"vk_{int(vk)}")

        # ---- Detection mode ----
        if self.detecting and is_down:
            hw_id = self.listener.get_hw_id(device_id) if self.listener else ""
            if self._has_builtin and _is_builtin_hw(hw_id):
                self._set_status(t("status_builtin_detected"))
                return
            self.detecting = False
            self._set_device(device_id)
            self._p2_last_activity = time.time()
            self._rebuild_tab_content()
            self._set_status(t("status_kb_set", id=device_id))
            print(f"[DoKey] Detected keyboard on device {device_id}.")
            return

        detected = self._player_device()

        # ---- Auto-reconnect ----
        if (detected is not None and device_id != detected and is_down
                and not self.binding_active):
            hw_id = self.listener.get_hw_id(device_id) if self.listener else ""
            if self._has_builtin and _is_builtin_hw(hw_id):
                return
            silent_for = time.time() - self._p2_last_activity
            recent_recreate = (time.time() - self._context_recreated_at) < 3.0
            if recent_recreate or (self._p2_last_activity > 0
                                   and silent_for >= 0.3):
                print(f"[DoKey] Auto-reconnect: P2 device {detected} -> "
                      f"{device_id}.")
                self._set_device(device_id)
                self._p2_last_activity = time.time()
                self._rebuild_tab_content()
                self._set_status(t("status_kb_reconnected", id=device_id))
                detected = device_id

        if detected is not None and device_id == detected:
            self._p2_last_activity = time.time()

        # ---- Binding mode ----
        if self.binding_active and is_down:
            self._process_binding(key_name, device_id)
            return

        # ---- UI-only: preview highlight ----
        with self._state_lock:
            if self._state["device_id"] != device_id: return
            kmap = dict(self._state["key_to_control"])

        control = kmap.get(key_name)
        if control is None:
            return

        if is_down:
            self._active_controls.add(control)
        else:
            self._active_controls.discard(control)
        if self.controller_preview is not None:
            try: self.controller_preview.update_from_set(self._active_controls)
            except Exception: traceback.print_exc()

    # =================================================================
    #  Binding
    # =================================================================

    def _process_binding(self, key_name, device_id):
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

        with self._state_lock:
            old_control = None
            for k, v in self._state["key_to_control"].items():
                if k == key_name and v != control:
                    old_control = v
                    break

        if old_control is None:
            self._apply_binding(control, key_name)
            return

        self._confirm_rebind(key_name, control, old_control)

    def _confirm_rebind(self, key_name, new_control, old_control):
        try:
            msg = (f"The key '{key_name}' is already bound to '{old_control}'.\n\n"
                   f"Do you want to re-bind it to '{new_control}'?\n"
                   f"The old binding will be removed.")
            result = messagebox.askyesno(
                title="Key Already Bound",
                message=msg,
                parent=self,
            )
            if result:
                self._apply_binding(new_control, key_name,
                                    old_control=old_control)
            else:
                self._reset_control_row(new_control)
                self._set_status(t("status_rebind_cancelled"))
        except Exception:
            traceback.print_exc()

    def _apply_binding(self, control, key_name, old_control=None):
        try:
            with self._state_lock:
                cur = dict(self._state["key_to_control"])
                cur.pop(key_name, None)
                cur[key_name] = control
                self._state["key_to_control"] = cur
                total = len(cur)
            print(f"[DoKey] BIND: '{key_name}' -> '{control}' (total {total} keys)")
        except Exception:
            traceback.print_exc()
            return
        self._reset_control_row(control)
        if old_control and old_control != control:
            self._reset_control_row(old_control)
        self._set_status(t("status_bound", key=key_name, control=control))

    def _reset_control_row(self, control):
        row = self.control_rows.get(control)
        if not row: return
        key_lbl, bind_btn, _ = row
        try:
            kmap = self._player_key_map()
            existing = sorted(k for k, c in kmap.items() if c == control)
            if existing:
                display = ", ".join(existing)
                if len(display) > 26: display = display[:24] + "…"
                key_lbl.configure(text=display, fg_color=P["accent"],
                                  text_color=P["text"])
            else:
                key_lbl.configure(text="—", fg_color=P["card"],
                                  text_color=P["text_dim"])
            bind_btn.configure(text=t("btn_bind"), fg_color=P["panel"],
                               hover_color=P["card_hover"], text_color=P["text"],
                               command=lambda c=control: self._start_binding(c))
        except Exception:
            traceback.print_exc()

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
            if self._reboot_banner is not None:
                self._hide_banner()
                if self._driver_ok:
                    pass
                elif _driver_present():
                    self._show_banner(t("banner_reboot"))
                else:
                    self._show_banner(t("banner_not_installed"))
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
        self._set_key_map(layout["key_map"])
        self.current_layout_name = layout["name"]

    def _do_layout_selected(self, value):
        self._cancel_all()
        real_name = self._layout_display_map.get(value, None)
        if real_name is None:
            self.current_layout_name = None
            set_config("last_layout", None)
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
        self._set_key_map(layout["key_map"])
        self._active_controls.clear()
        self.current_layout_name = layout["name"]
        self._current_process_name = layout.get("process", "")
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
        existing = load_layout(self.current_layout_name)
        proc = existing.get("process", "") if existing else ""
        if not save_layout(self.current_layout_name, km, process=proc):
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
            result = messagebox.askyesno(
                title=t("dlg_overwrite_title"),
                message=t("dlg_overwrite_text", name=match),
                parent=self)
            if not result: return
            name = match
        proc = ""
        existing_layout = load_layout(name)
        if existing_layout:
            proc = existing_layout.get("process", "")
        if not save_layout(name, km, process=proc):
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
        result = messagebox.askyesno(
            title=t("dlg_delete_title"),
            message=t("dlg_delete_text", name=_reshape_rtl(name)),
            parent=self)
        if not result: return
        if delete_layout(name):
            self.current_layout_name = None
            if get_config("last_layout") == name: set_config("last_layout", None)
            self._rebuild_top_card()
            self._set_status(t("status_layout_deleted", name=_reshape_rtl(name)))
        else:
            self._set_status(t("status_layout_delete_failed",
                               name=_reshape_rtl(name)))

    # =================================================================
    #  Link to Game (process-based auto-switch)
    # =================================================================

    def _on_link_game(self):
        self._cancel_all()
        if not self.current_layout_name:
            self._set_status(t("status_link_no_layout"))
            messagebox.showinfo(
                title="No Layout Selected",
                message=t("status_link_no_layout"),
                parent=self,
            )
            return
        self._link_countdown_remaining = 10
        self._set_status(t("status_link_prompt"))
        self._show_link_countdown()
        self.after(1000, self._tick_link_countdown)
        print("[DoKey] Link to Game: switch to your game in the next "
              "10 seconds...")

    def _show_link_countdown(self):
        try:
            if self._link_countdown_label is None:
                return
            self._link_countdown_label.configure(
                text=f"⏱  Alt-tab to game: {self._link_countdown_remaining}s")
            self._link_countdown_label.pack(side="right", padx=(12, 0))
        except Exception:
            pass

    def _hide_link_countdown(self):
        try:
            if self._link_countdown_label is not None:
                self._link_countdown_label.pack_forget()
        except Exception:
            pass

    def _tick_link_countdown(self):
        if not self._alive:
            return
        self._link_countdown_remaining -= 1
        if self._link_countdown_remaining > 0:
            self._show_link_countdown()
            self.after(1000, self._tick_link_countdown)
        else:
            self._hide_link_countdown()
            self._capture_game_for_layout()

    def _capture_game_for_layout(self):
        self._hide_link_countdown()
        self._link_countdown_remaining = 0
        proc = get_foreground_process_name()
        if (not proc
                or "dokey" in proc.lower()
                or "python" in proc.lower()
                or proc.lower() == "explorer.exe"):
            self._set_status(t("status_link_cancelled"))
            print(f"[DoKey] Link cancelled — captured process was '{proc}'.")
            return
        layout = load_layout(self.current_layout_name)
        if not layout: return
        save_layout(self.current_layout_name,
                    layout["key_map"], process=proc)
        self._current_process_name = proc
        self._rebuild_top_card()
        self._set_status(t("status_link_done",
                           name=_reshape_rtl(self.current_layout_name),
                           proc=proc))
        print(f"[DoKey] Linked '{self.current_layout_name}' to {proc}")
        try:
            messagebox.showinfo(
                title=t("dlg_link_success_title"),
                message=t("dlg_link_success_msg",
                          name=self.current_layout_name,
                          proc=proc),
                parent=self,
            )
        except Exception:
            pass

    # =================================================================
    #  UI
    # =================================================================

    def _build_ui(self):
        header = ctk.CTkFrame(self, fg_color=P["panel"], corner_radius=0, height=84)
        header.pack(fill="x"); header.pack_propagate(False)
        self._header_frame = header

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

        # v22: Layout card now auto-sizes to its content, so it takes
        # less vertical room and the Live Controller below has more.
        self.top_card_wrap = ctk.CTkFrame(self, fg_color="transparent")
        self.top_card_wrap.pack(fill="x", padx=24, pady=(14, 0))

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

        # Row 1: buttons
        row1 = ctk.CTkFrame(card, fg_color="transparent")
        row1.pack(fill="x", padx=20, pady=(10, 4))

        ctk.CTkLabel(row1, text=t("layout_title"),
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
            row1, values=disp, variable=self.layout_var,
            width=240, height=38, fg_color=P["panel"],
            button_color=P["border"], button_hover_color=P["accent"],
            dropdown_fg_color=P["panel"], dropdown_hover_color=P["accent"],
            text_color=P["text"], font=ctk.CTkFont(size=FS["card_text"]),
            dropdown_font=ctk.CTkFont(size=FS["card_text"]),
            command=self._do_layout_selected)
        self.layout_menu.pack(side="left", padx=(0, 8))

        ctk.CTkButton(row1, text=t("btn_save"), command=self._on_save_current,
                      fg_color=P["accent"], hover_color=P["accent_hover"],
                      text_color=P["text"], font=ctk.CTkFont(size=FS["card_text"], weight="bold"),
                      width=110, height=38).pack(side="left", padx=(0, 6))

        ctk.CTkButton(row1, text=t("save_as"), command=self._on_save_layout,
                      fg_color=P["panel"], hover_color=P["card_hover"],
                      text_color=P["text"], font=ctk.CTkFont(size=FS["card_text"], weight="bold"),
                      width=130, height=38).pack(side="left", padx=(0, 6))

        ctk.CTkButton(row1, text=t("btn_link_game"), command=self._on_link_game,
                      fg_color="#8e44ad", hover_color="#7d3c98",
                      text_color="#ffffff",
                      font=ctk.CTkFont(size=FS["card_text"], weight="bold"),
                      width=160, height=38).pack(side="left", padx=(0, 6))

        self.delete_layout_btn = ctk.CTkButton(
            row1, text=t("btn_delete"), command=self._on_delete_layout,
            fg_color=P["danger"], hover_color=P["danger_hover"],
            text_color=P["text"], font=ctk.CTkFont(size=FS["card_text"], weight="bold"),
            width=110, height=38)
        self.delete_layout_btn.pack(side="left")
        if cur_disp == no_lbl: self.delete_layout_btn.configure(state="disabled")

        # Row 2: hint + countdown label
        row2 = ctk.CTkFrame(card, fg_color="transparent")
        row2.pack(fill="x", padx=20, pady=(0, 10))
        ctk.CTkLabel(
            row2, text=t("link_hint"),
            font=ctk.CTkFont(size=FS["hint"]),
            text_color=P["text_dim"],
            anchor="w", justify="left", wraplength=1500,
        ).pack(side="left", fill="x", expand=True)

        self._link_countdown_label = ctk.CTkLabel(
            row2, text="",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#ffffff",
            fg_color="#8e44ad",
            corner_radius=8,
            width=190, height=32,
        )
        if self._link_countdown_remaining > 0:
            self._link_countdown_label.configure(
                text=f"⏱  Alt-tab to game: {self._link_countdown_remaining}s")
            self._link_countdown_label.pack(side="right", padx=(12, 0))

    def _rebuild_tab_content(self):
        for c in self.content.winfo_children():
            c.destroy()
        self.control_labels = {}; self.control_rows = {}; self.detect_btn = None
        self.forget_btn = None
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

        if dev_id is not None:
            forget_btn = ctk.CTkButton(
                kbi, text=t("forget_btn"), command=self._forget_detect,
                fg_color=P["panel"], hover_color=P["card_hover"],
                text_color=P["text"], font=ctk.CTkFont(size=FS["card_text"], weight="bold"),
                width=180, height=42,
                border_width=1, border_color=P["border"])
            forget_btn.pack(side="right", padx=(0, 10))
            self.forget_btn = forget_btn

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

        # v22: preview panel is 500x500 (was 520x580) so the canvas
        # and everything below fits without clipping the gamepad.
        preview_wrap = ctk.CTkFrame(body, fg_color=P["panel"], corner_radius=10,
                                    width=500, height=500)
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
                                   corner_radius=8, width=240, height=36)
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
            existing = sorted(k for k, c in kmap.items() if c == control)
            if existing:
                display = ", ".join(existing)
                if len(display) > 26: display = display[:24] + "…"
                key_lbl.configure(text=display, fg_color=P["accent"],
                                  text_color=P["text"])

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
        if total < 1:
            self._set_status(t("status_only_one_keyboard")); return
        if external < 1:
            self._set_status(t("status_only_one_keyboard")); return
        if total < 2:
            self._set_status(t("status_only_one_keyboard"))

        self._cancel_all()
        self.detecting = True
        if self.detect_btn is not None:
            try:
                self.detect_btn.configure(text=t("listening_btn"),
                                          fg_color=P["binding"],
                                          hover_color=P["binding_hover"],
                                          text_color="#0d0f1a")
            except Exception: pass
        if total >= 2:
            self._set_status(t("status_detect_prompt"))

    def _forget_detect(self):
        with self._state_lock: running = self._state["running"]
        if running:
            self._set_status(t("status_stop_first_detect")); return
        self._cancel_all()
        self._set_device(None)
        self._rebuild_tab_content()
        self._set_status(t("status_kb_forgotten"))

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
        if not was_active or control is None: return
        self._reset_control_row(control)

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
        try:
            self._on_start_impl()
        except Exception:
            print("[DoKey] Unexpected error in _on_start:")
            traceback.print_exc()
            try: self._set_status("⚠ Unexpected error — check terminal.")
            except Exception: pass

    def _on_start_impl(self):
        self._cancel_all()
        if self._driver_restarting:
            self._set_status(t("status_driver_restarting"))
            return
        if not self._driver_ok or self.listener is None:
            self._set_status(t("status_driver_missing")); return
        dev = self._player_device()
        kmap = self._player_key_map()
        if dev is None:
            self._set_status(t("status_no_kb")); return
        if not kmap:
            self._set_status(t("status_no_bindings")); return

        if not get_config("anticheat_ack", False) and not self._anticheat_handled:
            resp = messagebox.askyesnocancel(
                title=t("anticheat_title"),
                message=t("anticheat_msg"),
                parent=self)
            try:
                if self.state() == "withdrawn":
                    self.deiconify()
                    self.lift()
            except Exception:
                pass
            if resp is None:
                self._set_status(t("status_anticheat_cancelled"))
                return
            self._anticheat_handled = True
            if resp:
                set_config("anticheat_ack", True)
                print("[DoKey] Anti-cheat warning accepted (will not show again).")
            else:
                print("[DoKey] Anti-cheat warning acknowledged (will show again).")

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

        self._p2_last_activity = time.time()
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
        self._real_quit()

# =====================================================================
#  ENTRY
# =====================================================================

def main():
    print("[DoKey] BUILD v22 — starting.")
    try:
        test = vg.VX360Gamepad(); test.reset(); del test; gc.collect()
    except Exception as e:
        print(f"[DoKey] Cannot create virtual controller: {e}")
        print("[DoKey] Reboot after installing ViGEmBus and try again.")
        _hard_exit(
            "DoKey could not create a virtual Xbox controller.\n\n"
            "This usually means the ViGEmBus driver is missing or hasn't "
            "been activated yet. Install it and reboot if needed.\n\n"
            f"Details: {e}"
        )
    app = DoKeyApp()
    app.mainloop()

if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        _hard_exit("DoKey hit an unexpected error. See the traceback above.")