#!/usr/bin/env python3
"""
DoKey — دو کلید
Two-player keyboard-to-Xbox-controller mapper.
Supports English and Dari (دری) with proper RTL rendering.
"""

import sys, os, subprocess, importlib, importlib.util, threading, time, tempfile
import urllib.request, ctypes, queue, traceback, gc, zipfile, shutil, struct
import json

# =====================================================================
#  STAGE 1 — Python packages (bullet-proof auto-installer)
# =====================================================================

REQUIRED_PACKAGES = {
    "customtkinter":   "customtkinter",
    "vgamepad":        "vgamepad",
    "arabic_reshaper": "arabic-reshaper",
    "bidi":            "python-bidi",
}

# Packages that MUST be present for DoKey to function at all.
CORE_PACKAGES = {"customtkinter", "vgamepad"}
# Packages that are only needed for right-to-left Dari text rendering.
# If these fail, DoKey still works, just without RTL shaping.
RTL_PACKAGES  = {"arabic_reshaper", "bidi"}

def _pip_ok():
    """Return True if `python -m pip --version` responds."""
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "--version"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=30)
        return True
    except Exception:
        return False

def _bootstrap_pip_if_needed():
    """If pip is missing entirely, try to install it via ensurepip."""
    if _pip_ok():
        return True
    print("[DoKey] pip is not available — attempting to bootstrap it ...")
    for args in (["--default-pip"], ["--user"], []):
        try:
            subprocess.check_call(
                [sys.executable, "-m", "ensurepip"] + args,
                stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
                timeout=180)
            if _pip_ok():
                print("[DoKey] pip bootstrapped successfully.")
                return True
        except Exception:
            continue
    print("[DoKey] Could not bootstrap pip.")
    return False

def _pip_run(args, timeout=600):
    """Run `python -m pip <args>`. Returns True on success."""
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip"] + list(args),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            timeout=timeout)
        return True
    except Exception:
        return False

def _upgrade_pip():
    """Upgrade pip (and setuptools/wheel) using several strategies."""
    strategies = [
        ["install", "--upgrade", "--disable-pip-version-check",
         "pip", "setuptools", "wheel"],
        ["install", "--upgrade", "--user", "--disable-pip-version-check",
         "pip", "setuptools", "wheel"],
        ["install", "--upgrade", "--no-cache-dir", "--disable-pip-version-check",
         "pip", "setuptools", "wheel"],
        ["install", "--upgrade", "--user", "--no-cache-dir",
         "--disable-pip-version-check", "pip", "setuptools", "wheel"],
        # Minimal fallbacks in case setuptools/wheel cannot install
        ["install", "--upgrade", "--disable-pip-version-check", "pip"],
        ["install", "--upgrade", "--user",
         "--disable-pip-version-check", "pip"],
    ]
    for s in strategies:
        if _pip_run(s):
            return True
    return False

def _pip_install_package(pip_name):
    """Try many pip strategies. Returns True if pip returned success."""
    strategies = [
        ["install", "--upgrade", "--disable-pip-version-check", pip_name],
        ["install", "--upgrade", "--user", "--disable-pip-version-check", pip_name],
        ["install", "--upgrade", "--no-cache-dir", "--disable-pip-version-check", pip_name],
        ["install", "--upgrade", "--user", "--no-cache-dir",
         "--disable-pip-version-check", pip_name],
        ["install", "--disable-pip-version-check", pip_name],
        ["install", "--user", "--disable-pip-version-check", pip_name],
        ["install", "--no-cache-dir", "--disable-pip-version-check", pip_name],
        ["install", pip_name],
    ]
    for s in strategies:
        if _pip_run(s):
            return True
    return False

def _verify_import(module_name):
    """Clear caches and re-check whether the module is importable."""
    try:
        importlib.invalidate_caches()
    except Exception:
        pass
    try:
        return importlib.util.find_spec(module_name) is not None
    except Exception:
        return False

def ensure_python_packages():
    # ---- 1. Make sure pip exists (bootstrap if missing) ----
    if not _bootstrap_pip_if_needed():
        print()
        print("=" * 70)
        print("[DoKey] ERROR: pip is not available and could not be installed.")
        print("[DoKey] Please reinstall Python from python.org and make sure")
        print("[DoKey] 'pip' is checked in the installer.")
        print("=" * 70)
        input("Press Enter to exit...")
        sys.exit(1)

    # ---- 2. Upgrade pip / setuptools / wheel (best effort) ----
    print("[DoKey] Updating pip, setuptools, wheel ...")
    if _upgrade_pip():
        print("[DoKey]   pip is up to date.")
    else:
        print("[DoKey]   pip upgrade failed (continuing with current pip).")

    # ---- 3. Figure out which packages are missing ----
    missing = [(m, p) for m, p in REQUIRED_PACKAGES.items()
               if not _verify_import(m)]
    if not missing:
        print("[DoKey] All required packages are already installed.")
        return

    print(f"[DoKey] Missing packages: {[p for _, p in missing]}")
    print("[DoKey] Installing them now — this may take a minute ...")

    # ---- 4. Install each missing package ----
    failed = []
    for module_name, pip_name in missing:
        print(f"[DoKey]   Installing {pip_name} ...", end=" ", flush=True)
        ok_pip = _pip_install_package(pip_name)
        # Verify both: pip returned success AND import actually resolves
        if ok_pip and _verify_import(module_name):
            print("OK")
        else:
            # Even if pip said OK, double-check import one more time
            if _verify_import(module_name):
                print("OK")
            else:
                print("FAILED")
                failed.append((module_name, pip_name))

    # ---- 5. Decide whether it's fatal ----
    core_failed = [(m, p) for m, p in failed if m in CORE_PACKAGES]
    rtl_failed  = [(m, p) for m, p in failed if m in RTL_PACKAGES]

    if rtl_failed and not core_failed:
        # DoKey still runs — only RTL shaping is lost
        print()
        print("=" * 70)
        print("[DoKey] WARNING: RTL support packages could not be installed:")
        for _, p in rtl_failed:
            print(f"   - {p}")
        print("[DoKey] DoKey will still run, but Dari text may display")
        print("[DoKey] in the wrong order. Install them manually with:")
        print(f"   {sys.executable} -m pip install "
              f"{' '.join(p for _, p in rtl_failed)}")
        print("=" * 70)

    if core_failed:
        print()
        print("=" * 70)
        print("[DoKey] ERROR: Required packages could not be installed:")
        for _, p in core_failed:
            print(f"   - {p}")
        print()
        print("[DoKey] Please install them manually by running:")
        print(f"   {sys.executable} -m pip install "
              f"{' '.join(p for _, p in core_failed)}")
        print()
        print("[DoKey] If you are on Python 3.14+, try Python 3.12 or 3.13.")
        print("=" * 70)
        input("Press Enter to exit...")
        sys.exit(1)

# =====================================================================
#  Localization
# =====================================================================

STRINGS = {
    "en": {
        "app_title":       "DoKey — دو کلید",
        "app_name":        "⚡ DoKey",
        "app_subtitle":    "Two-Player Keyboard Splitter for Xbox Controllers",

        "tab_p1":          "Player 1",
        "tab_p2":          "Player 2",

        "btn_start":       "▶  Start",
        "btn_stop":        "■  Stop",

        "badge_checking":      "●  CHECKING…",
        "badge_driver_ok":     "●  DRIVER OK",
        "badge_driver_failed": "●  DRIVER FAILED",
        "badge_running":       "●  RUNNING",

        "layout_title":  "💾  Layout",
        "no_layout":     "— no layout —",
        "save_as":       "💾  Save As…",
        "btn_delete":    "🗑  Delete",

        "ext_keyboard":       "⌨  External Keyboard",
        "not_detected":       "— not detected yet —",
        "detect_btn":         "🎯 Detect Keyboard",
        "listening_btn":      "🎯 Listening…",
        "keyboard_device_fmt": "Keyboard (device #{id})",

        "p1_title": "Player 1 — Built-in Keyboard",
        "p1_body":  ("Player 1 uses your built-in laptop keyboard directly.\n"
                     "No virtual controller, no key remapping — everything\n"
                     "works exactly as the game expects.\n\n"
                     "Just configure the game's Controller 1 to 'Keyboard'\n"
                     "and you're done. Leave this tab alone."),
        "p1_note":  "ℹ This slot is fixed. External keyboards are set up on the Player 2 tab.",

        "no_kb_title": "No keyboard detected",
        "no_kb_body":  ("Click  🎯 Detect Keyboard  above, then press any key\n"
                        "on the EXTERNAL keyboard you want Player 2 to use.\n\n"
                        "You can also pick a saved layout from the top of this tab\n"
                        "to restore your bindings for a specific game."),
        "no_kb_tip":   "Tip: don't press a key on your laptop keyboard during detection.",

        "bindings_title": "🎮  Control Bindings",
        "btn_clear_all":  "Clear All",
        "col_control":    "Control",
        "col_bound_key":  "Bound Key",
        "btn_bind":       "Bind",
        "btn_clear":      "Clear",
        "btn_cancel":     "Cancel",
        "press_any_key":  "⌨ press any key…",

        "status_ready":             "Ready.",
        "status_new_kb":            "New keyboard detected (device #{id}).",
        "status_p2_kb_set":         "✓ Player 2 keyboard: {name}",
        "status_bound":             "✓ Bound '{key}' → {control}",
        "status_cleared":           "Cleared '{control}'.",
        "status_cleared_all":       "Cleared all bindings for Player 2.",
        "status_layout_cleared":    "Layout cleared. Bindings emptied.",
        "status_layout_load_error": "Could not load layout '{name}'.",
        "status_layout_loaded":     "✓ Loaded layout '{name}' — {n} bindings{dev_note}.",
        "status_layout_no_kb":      " (no keyboard — click 🎯 Detect)",
        "status_layout_saved":      "✓ Saved layout '{name}' — {n} bindings.",
        "status_layout_save_failed":"Failed to save layout '{name}'.",
        "status_layout_deleted":    "✓ Deleted layout '{name}'.",
        "status_layout_delete_failed": "Could not delete layout '{name}'.",
        "status_no_layout_sel":     "No layout selected to delete.",
        "status_nothing_to_save":   "Nothing to save — detect a keyboard and bind some keys first.",
        "status_stop_first_detect": "Stop first, then detect.",
        "status_stop_first_bind":   "Stop first, then bind.",
        "status_detect_first":      "Detect a keyboard first.",
        "status_press_key_bind":    "🎮 Press a key on the Player 2 keyboard to bind '{control}' (Esc to cancel)",
        "status_detect_prompt":     "🎯 Press any key on the EXTERNAL keyboard you want for Player 2 …",
        "status_cant_start":        "⚠ Cannot start — Interception driver is not ready. Reboot Windows and reopen DoKey.",
        "status_no_p2_kb":          "Detect a keyboard for Player 2 first.",
        "status_no_bindings":       "Bind at least one control for Player 2 before starting.",
        "status_ctrl_failed":       "Controller creation failed: {err}",
        "status_running":           "Running — Player 2 virtual controller active. {n} keys blocked.",
        "status_stopped":           "Stopped. All keyboards released.",

        "dlg_save_title":      "Save Layout",
        "dlg_save_text":       "Name this layout (e.g. PES 2017):",
        "dlg_overwrite_title": "Overwrite Layout?",
        "dlg_overwrite_text":  "A layout named '{name}' already exists.\nOverwrite it?",
        "dlg_delete_title":    "Delete Layout?",
        "dlg_delete_text":     "Delete layout '{name}'?\nThis cannot be undone.",
        "dlg_ok":              "OK",
        "dlg_yes":             "Yes",
        "dlg_no":              "No",
    },
    "fa": {
        "app_title":       "DoKey — دو کلید",
        "app_name":        "⚡ دو کلید",
        "app_subtitle":    "Xbox تقسیم کننده کیبود دو نفره برای کنترولر ",

        "tab_p1":          "بازیکن ۱",
        "tab_p2":          "بازیکن ۲",

        "btn_start":       "▶  شروع",
        "btn_stop":        "■  توقف",

        "badge_checking":      "●  در حال بررسی…",
        "badge_driver_ok":     "●  درایور فعال",
        "badge_driver_failed": "●  درایور غیرفعال",
        "badge_running":       "●  در حال اجرا",

        "layout_title":  "💾  چیدمان",
        "no_layout":     "— بدون چیدمان —",
        "save_as":       "💾  ذخیره به نام…",
        "btn_delete":    "🗑  حذف",

        "ext_keyboard":       "⌨  کیبورد خارجی",
        "not_detected":       "— هنوز شناسایی نشده —",
        "detect_btn":         "🎯 شناسایی کیبورد",
        "listening_btn":      "🎯 در حال گوش دادن…",
        "keyboard_device_fmt":"کیبورد (دستگاه #{id})",

        "p1_title": "بازیکن ۱ — کیبورد داخلی",
        "p1_body":  ("بازیکن ۱ مستقیماً از کیبورد داخلی لپ‌تاپ شما استفاده می‌کند.\n"
                     "بدون کنترولر مجازی، بدون تغییر کلیدها — همه چیز\n"
                     "دقیقاً همانطور که بازی انتظار دارد کار می‌کند.\n\n"
                     "فقط کنترولر ۱ بازی را روی 'Keyboard' تنظیم کنید\n"
                     "و تمام. این برگه را دست نزنید."),
        "p1_note":  "ℹ این جایگاه ثابت است. کیبوردهای خارجی در برگه بازیکن ۲ تنظیم می‌شوند.",

        "no_kb_title": "کیبوردی شناسایی نشد",
        "no_kb_body":  ("روی  🎯 شناسایی کیبورد  در بالا کلیک کنید، سپس هر کلیدی را\n"
                        "روی کیبورد خارجی که می‌خواهید بازیکن ۲ از آن استفاده کند فشار دهید.\n\n"
                        "همچنین می‌توانید یک چیدمان ذخیره شده را از بالای این برگه\n"
                        "انتخاب کنید تا کلیدهای خود را برای یک بازی خاص بازیابی کنید."),
        "no_kb_tip":   "نکته: هنگام شناسایی، کلیدی روی کیبورد لپ‌تاپ فشار ندهید.",

        "bindings_title": "🎮  تنظیمات کنترول",
        "btn_clear_all":  "پاک کردن همه",
        "col_control":    "کنترول",
        "col_bound_key":  "کلید متصل",
        "btn_bind":       "اتصال",
        "btn_clear":      "پاک کردن",
        "btn_cancel":     "لغو",
        "press_any_key":  "⌨ هر کلیدی را فشار دهید…",

        "status_ready":             "آماده.",
        "status_new_kb":            "کیبورد جدید شناسایی شد (دستگاه #{id}).",
        "status_p2_kb_set":         "✓ کیبورد بازیکن ۲: {name}",
        "status_bound":             "✓ '{key}' به {control} متصل شد",
        "status_cleared":           "'{control}' پاک شد.",
        "status_cleared_all":       "همه اتصالات بازیکن ۲ پاک شد.",
        "status_layout_cleared":    "چیدمان پاک شد. اتصالات خالی شدند.",
        "status_layout_load_error": "بارگذاری چیدمان '{name}' ممکن نشد.",
        "status_layout_loaded":     "✓ چیدمان '{name}' بارگذاری شد — {n} اتصال{dev_note}.",
        "status_layout_no_kb":      " (بدون کیبورد — روی 🎯 شناسایی کلیک کنید)",
        "status_layout_saved":      "✓ چیدمان '{name}' ذخیره شد — {n} اتصال.",
        "status_layout_save_failed":"ذخیره چیدمان '{name}' ناموفق بود.",
        "status_layout_deleted":    "✓ چیدمان '{name}' حذف شد.",
        "status_layout_delete_failed": "حذف چیدمان '{name}' ممکن نشد.",
        "status_no_layout_sel":     "چیدمانی برای حذف انتخاب نشده.",
        "status_nothing_to_save":   "چیزی برای ذخیره نیست — ابتدا کیبورد شناسایی و کلیدها را متصل کنید.",
        "status_stop_first_detect": "ابتدا متوقف کنید، سپس شناسایی کنید.",
        "status_stop_first_bind":   "ابتدا متوقف کنید، سپس اتصال دهید.",
        "status_detect_first":      "ابتدا یک کیبورد شناسایی کنید.",
        "status_press_key_bind":    "🎮 کلیدی روی کیبورد بازیکن ۲ فشار دهید تا به '{control}' متصل شود (Esc برای لغو)",
        "status_detect_prompt":     "🎯 هر کلیدی روی کیبورد خارجی مورد نظر برای بازیکن ۲ فشار دهید…",
        "status_cant_start":        "⚠ نمی‌توان شروع کرد — درایور Interception آماده نیست. ویندوز را ری‌استارت کنید و DoKey را دوباره باز کنید.",
        "status_no_p2_kb":          "ابتدا یک کیبورد برای بازیکن ۲ شناسایی کنید.",
        "status_no_bindings":       "حداقل یک کنترول برای بازیکن ۲ قبل از شروع متصل کنید.",
        "status_ctrl_failed":       "ایجاد کنترولر ناموفق بود: {err}",
        "status_running":           "در حال اجرا — کنترولر مجازی بازیکن ۲ فعال است. {n} کلید مسدود شد.",
        "status_stopped":           "متوقف شد. همه کیبوردها آزاد شدند.",

        "dlg_save_title":      "ذخیره چیدمان",
        "dlg_save_text":       "نام این چیدمان (مثلاً PES 2017):",
        "dlg_overwrite_title": "بازنویسی چیدمان؟",
        "dlg_overwrite_text":  "چیدمانی با نام '{name}' قبلاً وجود دارد.\nبازنویسی شود؟",
        "dlg_delete_title":    "حذف چیدمان؟",
        "dlg_delete_text":     "چیدمان '{name}' حذف شود؟\nاین عمل قابل بازگشت نیست.",
        "dlg_ok":              "تأیید",
        "dlg_yes":             "بله",
        "dlg_no":              "خیر",
    },
}

_current_lang = "en"

# =====================================================================
#  RTL reshaping (arabic_reshaper + python-bidi)
# =====================================================================

_ar_reshaper = None
_bidi_get_display = None
try:
    import arabic_reshaper as _ar
    from bidi.algorithm import get_display as _gd
    _ar_reshaper = _ar
    _bidi_get_display = _gd
except Exception as _e:
    print(f"[DoKey] RTL libraries not available: {_e}")

def _reshape_rtl(s):
    """
    Transform a Persian/Arabic string so Tk displays it with correct
    right-to-left ordering. Returns s unchanged for other languages
    or if the RTL libraries aren't loaded.
    """
    if not s or _current_lang != "fa":
        return s
    if _ar_reshaper is None or _bidi_get_display is None:
        return s
    try:
        reshaped = _ar_reshaper.reshape(s)
        return _bidi_get_display(reshaped)
    except Exception:
        return s

def _translate_raw(key, **kwargs):
    """Translate without RTL reshaping (for OS-native contexts)."""
    table = STRINGS.get(_current_lang, STRINGS["en"])
    s = table.get(key)
    if s is None:
        s = STRINGS["en"].get(key, key)
    if kwargs:
        try:
            s = s.format(**kwargs)
        except Exception:
            pass
    return s

def t(key, **kwargs):
    """Translate and reshape for display in Tk widgets."""
    return _reshape_rtl(_translate_raw(key, **kwargs))

def t_raw(key, **kwargs):
    """Translate without reshaping (window title, file paths, etc.)."""
    return _translate_raw(key, **kwargs)

def set_lang(lang):
    global _current_lang
    if lang in STRINGS:
        _current_lang = lang

# =====================================================================
#  Paths, migration, config
# =====================================================================

LOCALAPPDATA  = os.environ.get("LOCALAPPDATA", tempfile.gettempdir())
OLD_APP_DIR   = os.path.join(LOCALAPPDATA, "KeyForge")
LOCAL_APP_DIR = os.path.join(LOCALAPPDATA, "DoKey")
INTERCEPTION_DIR = os.path.join(LOCAL_APP_DIR, "interception")
LAYOUTS_DIR      = os.path.join(LOCAL_APP_DIR, "layouts")
CONFIG_FILE      = os.path.join(LOCAL_APP_DIR, "config.json")

try:
    if os.path.isdir(OLD_APP_DIR) and not os.path.isdir(LOCAL_APP_DIR):
        shutil.move(OLD_APP_DIR, LOCAL_APP_DIR)
        print(f"[DoKey] Migrated settings from {OLD_APP_DIR} to {LOCAL_APP_DIR}")
except Exception as e:
    print(f"[DoKey] Migration skipped: {e}")

VIGEMBUS_URL = ("https://github.com/nefarius/ViGEmBus/releases/download/"
                "v1.22.0/ViGEmBus_1.22.0_x64_x86_arm64.exe")
VIGEMBUS_EXE = "ViGEmBus_1.22.0_x64_x86_arm64.exe"

INTERCEPTION_URL = ("https://github.com/oblitum/Interception/releases/"
                    "download/v1.0.1/Interception.zip")

IC_KEY_DOWN = 0x00
IC_KEY_UP   = 0x01
IC_KEY_E0   = 0x02
IC_KEY_E1   = 0x04

INTERCEPTION_FILTER_KEY_ALL = 0xFFFF
MAPVK_VSC_TO_VK_EX = 3

_user32 = ctypes.windll.user32
_user32.MapVirtualKeyW.restype = ctypes.c_uint
_user32.MapVirtualKeyW.argtypes = [ctypes.c_uint, ctypes.c_uint]

def scancode_to_vk(scancode, extended):
    scan = (scancode | 0xE000) if extended else scancode
    try:
        vk = int(_user32.MapVirtualKeyW(scan, MAPVK_VSC_TO_VK_EX))
    except Exception:
        vk = 0
    return vk if vk else scancode

# ---- Config helpers -------------------------------------------------

def _read_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            v = json.load(f)
            return v if isinstance(v, dict) else {}
    except Exception:
        return {}

def _write_config(cfg):
    try:
        os.makedirs(LOCAL_APP_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[DoKey] write_config failed: {e}")

def get_config_value(key, default=None):
    return _read_config().get(key, default)

def set_config_value(key, value):
    cfg = _read_config()
    cfg[key] = value
    _write_config(cfg)

# ---- Layout helpers -------------------------------------------------

def _ensure_layouts_dir():
    try:
        os.makedirs(LAYOUTS_DIR, exist_ok=True)
    except Exception:
        pass

def _safe_layout_name(name):
    bad = '<>:"/\\|?*'
    for c in bad:
        name = name.replace(c, "_")
    return name.strip()[:64] or "layout"

def list_layouts():
    _ensure_layouts_dir()
    try:
        files = os.listdir(LAYOUTS_DIR)
    except Exception:
        return []
    out = []
    for f in files:
        if f.lower().endswith(".json"):
            out.append(os.path.splitext(f)[0])
    return sorted(out, key=lambda s: s.lower())

def save_layout(name, device_id, vk_map):
    _ensure_layouts_dir()
    fname = _safe_layout_name(name) + ".json"
    path = os.path.join(LAYOUTS_DIR, fname)
    data = {
        "name": name,
        "version": 1,
        "p2_device_id": device_id,
        "p2_vk_to_control": {str(int(k)): v for k, v in vk_map.items()},
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"[DoKey] save_layout failed: {e}")
        return False

def load_layout(name):
    _ensure_layouts_dir()
    fname = _safe_layout_name(name) + ".json"
    path = os.path.join(LAYOUTS_DIR, fname)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        dev = data.get("p2_device_id")
        if dev is not None:
            try: dev = int(dev)
            except Exception: dev = None
        raw = data.get("p2_vk_to_control", {})
        vk_map = {}
        for k, v in raw.items():
            try: vk_map[int(k)] = str(v)
            except Exception: continue
        return {"name": data.get("name", name),
                "device_id": dev,
                "vk_map": vk_map}
    except Exception as e:
        print(f"[DoKey] load_layout failed: {e}")
        return None

def delete_layout(name):
    _ensure_layouts_dir()
    fname = _safe_layout_name(name) + ".json"
    path = os.path.join(LAYOUTS_DIR, fname)
    try:
        if os.path.isfile(path):
            os.remove(path)
            return True
    except Exception as e:
        print(f"[DoKey] delete_layout failed: {e}")
    return False

# =====================================================================
#  STAGE 2 — Driver installation
# =====================================================================

def _vigembus_present():
    try:
        r = subprocess.run(["sc", "query", "ViGEmBus"],
                           capture_output=True, text=True, timeout=10)
        return "ViGEmBus" in (r.stdout + r.stderr)
    except Exception:
        return False

def _run_elevated(exe, args, wait_seconds=0):
    try:
        r = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", exe, args, None, 1)
        if int(r) <= 32:
            return False
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        return True
    except Exception as e:
        print(f"[DoKey] Elevation failed: {e}")
        return False

def ensure_vigembus(skip=False):
    if skip or _vigembus_present():
        return
    print("[DoKey] Downloading ViGEmBus installer ...")
    dest = os.path.join(tempfile.gettempdir(), VIGEMBUS_EXE)
    try:
        urllib.request.urlretrieve(VIGEMBUS_URL, dest)
    except Exception as e:
        print(f"[DoKey] Download failed: {e}")
        return
    print("[DoKey] Running ViGEmBus installer (accept UAC) ...")
    if not _run_elevated(dest, "/exenoui /qn /norestart"):
        print(f"[DoKey] Auto-install failed. Run manually: {dest}")
        return
    for _ in range(45):
        time.sleep(1)
        if _vigembus_present():
            print("[DoKey] ViGEmBus installed.")
            return
    print("[DoKey] ViGEmBus may still be installing.")

def _find_interception_dll_in(folder):
    bits = struct.calcsize("P") * 8
    arch = "x64" if bits == 64 else "x86"
    candidates = [
        os.path.join(folder, "library", arch, "interception.dll"),
        os.path.join(folder, "library", arch, "Interception.dll"),
        os.path.join(folder, arch, "interception.dll"),
        os.path.join(folder, "interception.dll"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    for root, _, files in os.walk(folder):
        for f in files:
            if f.lower() == "interception.dll":
                if os.path.basename(root).lower() == arch:
                    return os.path.join(root, f)
    return None

def _locate_persistent_dll():
    bits = struct.calcsize("P") * 8
    arch = "x64" if bits == 64 else "x86"
    candidates = [
        os.path.join(INTERCEPTION_DIR, arch, "interception.dll"),
        os.path.join(INTERCEPTION_DIR, "interception.dll"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "interception.dll"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None

def ensure_interception(skip=False):
    if skip:
        return _locate_persistent_dll()
    existing = _locate_persistent_dll()
    if existing:
        return existing

    print("=" * 70)
    print("[DoKey] Interception files not found. Downloading ...")
    print("=" * 70)

    tmp = tempfile.gettempdir()
    zip_path = os.path.join(tmp, "Interception.zip")
    extract_dir = os.path.join(tmp, "Interception_extract")

    try:
        urllib.request.urlretrieve(INTERCEPTION_URL, zip_path)
    except Exception as e:
        print(f"[DoKey] Download failed: {e}")
        return None

    try:
        if os.path.isdir(extract_dir):
            shutil.rmtree(extract_dir, ignore_errors=True)
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(extract_dir)
    except Exception as e:
        print(f"[DoKey] Extraction failed: {e}")
        return None

    installer = None
    for root, _, files in os.walk(extract_dir):
        for f in files:
            if f.lower() == "install-interception.exe":
                installer = os.path.join(root, f)
                break
        if installer:
            break

    dll_in_zip = _find_interception_dll_in(extract_dir)
    if not dll_in_zip:
        print("[DoKey] interception.dll not found in archive.")
        return None

    bits = struct.calcsize("P") * 8
    arch = "x64" if bits == 64 else "x86"
    dest_dir = os.path.join(INTERCEPTION_DIR, arch)
    os.makedirs(dest_dir, exist_ok=True)
    dest_dll = os.path.join(dest_dir, "interception.dll")
    try:
        shutil.copy2(dll_in_zip, dest_dll)
        print(f"[DoKey] Cached DLL at: {dest_dll}")
    except Exception as e:
        print(f"[DoKey] Could not cache DLL: {e}")
        dest_dll = dll_in_zip

    if installer:
        print("[DoKey] Launching Interception driver installer (UAC)...")
        if _run_elevated(installer, "/install", wait_seconds=8):
            print()
            print("=" * 70)
            print("[DoKey] Interception driver installed.")
            print("[DoKey] *** YOU MUST REBOOT WINDOWS NOW ***")
            print("=" * 70)
        else:
            print("[DoKey] Elevation denied. Run manually as Admin:")
            print(f"[DoKey]    {installer}  /install")
    return dest_dll

# =====================================================================
#  Installers, then imports
# =====================================================================

ensure_python_packages()
SKIP_DRIVER = "--skip-driver-install" in sys.argv
ensure_vigembus(skip=SKIP_DRIVER)
INTERCEPTION_DLL_PATH = ensure_interception(skip=SKIP_DRIVER)

import customtkinter as ctk

_vg = None
def _load_vgamepad():
    global _vg
    if _vg is not None:
        return _vg
    try:
        import vgamepad as vg
        _vg = vg
        return vg
    except Exception as e:
        print(f"[DoKey] vgamepad import failed: {e}")
        return None

vg = _load_vgamepad()
if vg is None:
    print("[DoKey] Install ViGEmBus, reboot, then restart DoKey.")
    sys.exit(1)

DEBUG = "--debug" in sys.argv

# Load saved language BEFORE building UI
set_lang(get_config_value("language", "en") or "en")

# =====================================================================
#  Interception ctypes bindings
# =====================================================================

class InterceptionKeyStroke(ctypes.Structure):
    _fields_ = [
        ("code",        ctypes.c_ushort),
        ("state",       ctypes.c_ushort),
        ("information", ctypes.c_uint),
    ]

class InterceptionDLL:
    def __init__(self, dll_path):
        self.lib = ctypes.CDLL(dll_path)
        L = self.lib

        L.interception_create_context.restype = ctypes.c_void_p
        L.interception_create_context.argtypes = []

        L.interception_destroy_context.restype = None
        L.interception_destroy_context.argtypes = [ctypes.c_void_p]

        L.interception_wait_with_timeout.restype = ctypes.c_int
        L.interception_wait_with_timeout.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong]

        L.interception_send.restype = ctypes.c_int
        L.interception_send.argtypes = [
            ctypes.c_void_p, ctypes.c_int,
            ctypes.POINTER(InterceptionKeyStroke), ctypes.c_uint]

        L.interception_receive.restype = ctypes.c_int
        L.interception_receive.argtypes = [
            ctypes.c_void_p, ctypes.c_int,
            ctypes.POINTER(InterceptionKeyStroke), ctypes.c_uint]

        L.interception_is_keyboard.restype = ctypes.c_int
        L.interception_is_keyboard.argtypes = [ctypes.c_int]

        L.interception_is_mouse.restype = ctypes.c_int
        L.interception_is_mouse.argtypes = [ctypes.c_int]

        L.interception_is_invalid.restype = ctypes.c_int
        L.interception_is_invalid.argtypes = [ctypes.c_int]

        L.interception_set_filter.restype = None
        L.interception_set_filter.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ushort]

    def create_context(self):
        return self.lib.interception_create_context()

    def destroy_context(self, ctx):
        try:
            self.lib.interception_destroy_context(ctx)
        except Exception:
            pass

    def set_filter(self, ctx, predicate_ptr, filter_mask):
        self.lib.interception_set_filter(ctx, predicate_ptr, filter_mask)

    def keyboard_predicate_ptr(self):
        return ctypes.cast(self.lib.interception_is_keyboard, ctypes.c_void_p)

    def wait_with_timeout(self, ctx, ms):
        return self.lib.interception_wait_with_timeout(ctx, ms)

    def is_keyboard(self, device):
        return self.lib.interception_is_keyboard(device) != 0

    def is_invalid(self, device):
        return self.lib.interception_is_invalid(device) != 0

    def send(self, ctx, device, stroke):
        return self.lib.interception_send(ctx, device,
                                          ctypes.byref(stroke), 1)

    def receive(self, ctx, device):
        stroke = InterceptionKeyStroke()
        n = self.lib.interception_receive(ctx, device,
                                          ctypes.byref(stroke), 1)
        if n < 1:
            return None
        return stroke

# =====================================================================
#  Test driver at startup
# =====================================================================

def test_interception_driver():
    if not INTERCEPTION_DLL_PATH:
        return False, "interception.dll not found"
    try:
        dll = InterceptionDLL(INTERCEPTION_DLL_PATH)
    except OSError as e:
        return False, f"Cannot load interception.dll: {e}"
    except Exception as e:
        return False, f"Unexpected DLL load error: {e}"
    try:
        ctx = dll.create_context()
    except Exception as e:
        return False, f"create_context raised: {e}"
    if not ctx:
        return False, "Interception driver not loaded. Reboot Windows."
    dll.destroy_context(ctx)
    return True, "OK"

# =====================================================================
#  Xbox button tables
# =====================================================================

XBOX_BUTTON_NAMES = [
    "A", "B", "X", "Y", "LB", "RB", "Start", "Back",
    "LS Click", "RS Click",
    "D-Pad Up", "D-Pad Down", "D-Pad Left", "D-Pad Right",
]
ANALOG_NAMES = [
    "LT", "RT",
    "LS Up", "LS Down", "LS Left", "LS Right",
    "RS Up", "RS Down", "RS Left", "RS Right",
]
ALL_CONTROLS = XBOX_BUTTON_NAMES + ANALOG_NAMES

def _build_xbox_buttons():
    X = vg.XUSB_BUTTON
    return {
        "A": X.XUSB_GAMEPAD_A,  "B": X.XUSB_GAMEPAD_B,
        "X": X.XUSB_GAMEPAD_X,  "Y": X.XUSB_GAMEPAD_Y,
        "LB": X.XUSB_GAMEPAD_LEFT_SHOULDER,
        "RB": X.XUSB_GAMEPAD_RIGHT_SHOULDER,
        "Start": X.XUSB_GAMEPAD_START,
        "Back": X.XUSB_GAMEPAD_BACK,
        "LS Click": X.XUSB_GAMEPAD_LEFT_THUMB,
        "RS Click": X.XUSB_GAMEPAD_RIGHT_THUMB,
        "D-Pad Up": X.XUSB_GAMEPAD_DPAD_UP,
        "D-Pad Down": X.XUSB_GAMEPAD_DPAD_DOWN,
        "D-Pad Left": X.XUSB_GAMEPAD_DPAD_LEFT,
        "D-Pad Right": X.XUSB_GAMEPAD_DPAD_RIGHT,
    }

XBOX_BUTTONS = _build_xbox_buttons()

VK_NAMES = {}
for i, ch in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
    VK_NAMES[0x41 + i] = ch
for i, ch in enumerate("0123456789"):
    VK_NAMES[0x30 + i] = ch
for i in range(10):
    VK_NAMES[0x60 + i] = f"Numpad {i}"
for i in range(1, 25):
    VK_NAMES[0x70 + (i - 1)] = f"F{i}"
VK_NAMES.update({
    0x03: "Cancel", 0x08: "Backspace", 0x09: "Tab",
    0x0C: "Clear", 0x0D: "Enter",
    0x10: "Shift", 0x11: "Ctrl", 0x12: "Alt",
    0x13: "Pause", 0x14: "CapsLock", 0x15: "Kana",
    0x1B: "Esc", 0x20: "Space",
    0x21: "Page Up", 0x22: "Page Down",
    0x23: "End", 0x24: "Home",
    0x25: "Left", 0x26: "Up", 0x27: "Right", 0x28: "Down",
    0x29: "Select", 0x2A: "Print", 0x2B: "Execute",
    0x2C: "Print Screen", 0x2D: "Insert", 0x2E: "Delete", 0x2F: "Help",
    0x5B: "LWin", 0x5C: "RWin", 0x5D: "Menu", 0x5F: "Sleep",
    0x6A: "Numpad *", 0x6B: "Numpad +", 0x6C: "Separator",
    0x6D: "Numpad -", 0x6E: "Numpad .", 0x6F: "Numpad /",
    0x90: "NumLock", 0x91: "ScrollLock",
    0xA0: "LShift", 0xA1: "RShift",
    0xA2: "LCtrl", 0xA3: "RCtrl",
    0xA4: "LAlt", 0xA5: "RAlt",
    0xA6: "Browser Back", 0xA7: "Browser Forward",
    0xA8: "Browser Refresh", 0xA9: "Browser Stop",
    0xAA: "Browser Search", 0xAB: "Browser Favorites",
    0xAC: "Browser Home",
    0xAD: "Volume Mute", 0xAE: "Volume Down", 0xAF: "Volume Up",
    0xB0: "Media Next", 0xB1: "Media Prev", 0xB2: "Media Stop",
    0xB3: "Media Play/Pause",
    0xB4: "Launch Mail", 0xB5: "Launch Media",
    0xB6: "Launch App1", 0xB7: "Launch App2",
    0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-",
    0xBE: ".", 0xBF: "/", 0xC0: "`",
    0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'",
    0xDF: "OEM 8", 0xE2: "OEM 102",
})

def vk_name(vk):
    if vk in VK_NAMES:
        return VK_NAMES[vk]
    return f"Key 0x{vk:02X}"

# =====================================================================
#  Virtual controller
# =====================================================================

class VirtualController:
    def __init__(self):
        self.gamepad = vg.VX360Gamepad()
        self._buttons_down = set()
        self._analog_down = set()
        self._lock = threading.Lock()

    def button_down(self, name):
        with self._lock:
            btn = XBOX_BUTTONS.get(name)
            if btn is None or btn in self._buttons_down:
                return
            self._buttons_down.add(btn)
            self.gamepad.press_button(button=btn)
            self.gamepad.update()

    def button_up(self, name):
        with self._lock:
            btn = XBOX_BUTTONS.get(name)
            if btn is None or btn not in self._buttons_down:
                return
            self._buttons_down.discard(btn)
            self.gamepad.release_button(button=btn)
            self.gamepad.update()

    def analog_down(self, name):
        with self._lock:
            if name in self._analog_down:
                return
            self._analog_down.add(name)
            self._apply_analog()

    def analog_up(self, name):
        with self._lock:
            if name not in self._analog_down:
                return
            self._analog_down.discard(name)
            self._apply_analog()

    def _apply_analog(self):
        p = self._analog_down
        lt = 1.0 if "LT" in p else 0.0
        rt = 1.0 if "RT" in p else 0.0
        lx = (1.0 if "LS Right" in p else 0.0) - (1.0 if "LS Left" in p else 0.0)
        ly = (1.0 if "LS Up"    in p else 0.0) - (1.0 if "LS Down" in p else 0.0)
        rx = (1.0 if "RS Right" in p else 0.0) - (1.0 if "RS Left" in p else 0.0)
        ry = (1.0 if "RS Up"    in p else 0.0) - (1.0 if "RS Down" in p else 0.0)
        self.gamepad.left_trigger_float(lt)
        self.gamepad.right_trigger_float(rt)
        self.gamepad.left_joystick_float(x_value_float=lx, y_value_float=ly)
        self.gamepad.right_joystick_float(x_value_float=rx, y_value_float=ry)
        self.gamepad.update()

    def reset(self):
        with self._lock:
            for btn in list(self._buttons_down):
                try: self.gamepad.release_button(button=btn)
                except Exception: pass
            self._buttons_down.clear()
            self._analog_down.clear()
            try:
                self.gamepad.left_joystick_float(0.0, 0.0)
                self.gamepad.right_joystick_float(0.0, 0.0)
                self.gamepad.left_trigger_float(0.0)
                self.gamepad.right_trigger_float(0.0)
                self.gamepad.update()
            except Exception:
                pass

# =====================================================================
#  Listener thread
# =====================================================================

class InterceptionListener(threading.Thread):
    def __init__(self, on_event):
        super().__init__(daemon=True, name="InterceptionListener")
        self.on_event = on_event
        self.stop_flag = threading.Event()
        self.ready = threading.Event()
        self.started_ok = False
        self.error = None
        self._dll = None
        self._ctx = None

    def run(self):
        if not INTERCEPTION_DLL_PATH:
            self.error = "interception.dll not found"
            self.ready.set()
            return
        try:
            self._dll = InterceptionDLL(INTERCEPTION_DLL_PATH)
        except Exception as e:
            self.error = f"Could not load interception.dll: {e}"
            self.ready.set()
            return
        try:
            ctx = self._dll.create_context()
        except Exception as e:
            self.error = f"create_context exception: {e}"
            self.ready.set()
            return
        if not ctx:
            self.error = ("Interception driver not loaded. "
                          "Reboot Windows after installing the driver.")
            self.ready.set()
            return
        self._ctx = ctx

        try:
            self._dll.set_filter(
                ctx,
                self._dll.keyboard_predicate_ptr(),
                INTERCEPTION_FILTER_KEY_ALL)
            if DEBUG:
                print("[DoKey][debug] Keyboard filter set (0xFFFF).")
        except Exception as e:
            self.error = f"set_filter failed: {e}"
            try: self._dll.destroy_context(ctx)
            except Exception: pass
            self.ready.set()
            return

        self.started_ok = True
        self.ready.set()
        if DEBUG:
            print("[DoKey][debug] Listener ready.")

        try:
            while not self.stop_flag.is_set():
                try:
                    dev = self._dll.wait_with_timeout(ctx, 100)
                except Exception:
                    time.sleep(0.01)
                    continue
                if dev == 0:
                    continue
                if self._dll.is_invalid(dev):
                    continue
                if not self._dll.is_keyboard(dev):
                    stroke = self._dll.receive(ctx, dev)
                    if stroke is not None:
                        try: self._dll.send(ctx, dev, stroke)
                        except Exception: pass
                    continue
                stroke = self._dll.receive(ctx, dev)
                if stroke is None:
                    continue
                raw_state = stroke.state
                is_down = (raw_state & IC_KEY_UP) == IC_KEY_DOWN
                extended = bool(raw_state & (IC_KEY_E0 | IC_KEY_E1))
                vk = scancode_to_vk(stroke.code, extended)
                if DEBUG:
                    print(f"[DoKey][debug] dev={dev} scan=0x{stroke.code:02X} "
                          f"vk=0x{vk:02X} ({'DOWN' if is_down else 'UP'})")
                block = False
                try:
                    block = bool(self.on_event(dev, vk, is_down))
                except Exception:
                    traceback.print_exc()
                if not block:
                    try: self._dll.send(ctx, dev, stroke)
                    except Exception: pass
        finally:
            try: self._dll.destroy_context(ctx)
            except Exception: pass

    def stop(self):
        self.stop_flag.set()

# =====================================================================
#  Palette and font sizes
# =====================================================================

P = {
    "bg":            "#0d0f1a",
    "panel":         "#161a29",
    "card":          "#1d2233",
    "card_hover":    "#242a3e",
    "border":        "#2a3145",
    "text":          "#e8eaf2",
    "text_dim":      "#8b93a7",
    "accent":        "#5b8def",
    "accent_hover":  "#4a7ade",
    "success":       "#2ecc71",
    "success_hover": "#27ae60",
    "danger":        "#e74c3c",
    "danger_hover":  "#c0392b",
    "binding":       "#f5b041",
    "binding_hover": "#e59a30",
    "warn":          "#f39c12",
}

# Centralized font sizes (bumped up for readability)
FS = {
    "app_name":      30,
    "app_subtitle":  13,
    "badge":         14,
    "lang":          14,
    "tab":           15,
    "btn_main":      16,
    "card_title":    15,
    "card_text":     14,
    "card_small":    13,
    "section_big":   22,
    "body":          15,
    "col_header":    13,
    "control":       15,
    "bound_key":     14,
    "row_btn":       14,
    "status":        13,
    "dialog":        15,
    "dialog_btn":    14,
}

# =====================================================================
#  Dialogs
# =====================================================================

class NameDialog(ctk.CTkToplevel):
    def __init__(self, parent, title, prompt, initial=""):
        super().__init__(parent)
        self.result = None
        self.title(title)
        self.geometry("500x220")
        self.resizable(False, False)
        self.configure(fg_color=P["bg"])

        try:
            self.transient(parent)
            self.grab_set()
        except Exception:
            pass

        wrap = ctk.CTkFrame(self, fg_color=P["bg"])
        wrap.pack(fill="both", expand=True, padx=24, pady=24)

        ctk.CTkLabel(wrap, text=prompt,
                     font=ctk.CTkFont(size=FS["dialog"]),
                     text_color=P["text"],
                     justify="center").pack(pady=(0, 14))

        self.entry = ctk.CTkEntry(
            wrap, height=40, font=ctk.CTkFont(size=FS["dialog"]),
            fg_color=P["panel"], border_color=P["border"],
            text_color=P["text"])
        self.entry.pack(fill="x")
        if initial:
            self.entry.insert(0, initial)
        self.entry.focus_set()

        btns = ctk.CTkFrame(wrap, fg_color="transparent")
        btns.pack(fill="x", pady=(18, 0))

        ctk.CTkButton(btns, text=t("btn_cancel"), command=self._cancel,
                      fg_color=P["panel"], hover_color=P["card_hover"],
                      text_color=P["text"],
                      font=ctk.CTkFont(size=FS["dialog_btn"]),
                      width=110, height=40).pack(side="right", padx=(8, 0))
        ctk.CTkButton(btns, text=t("dlg_ok"), command=self._ok,
                      fg_color=P["accent"], hover_color=P["accent_hover"],
                      text_color=P["text"],
                      font=ctk.CTkFont(size=FS["dialog_btn"]),
                      width=110, height=40).pack(side="right")

        self.entry.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self._cancel())

        try:
            self.after(10, self.entry.focus_set)
            self.wait_window()
        except Exception:
            pass

    def _ok(self):
        self.result = self.entry.get().strip()
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()

class ConfirmDialog(ctk.CTkToplevel):
    def __init__(self, parent, title, message):
        super().__init__(parent)
        self.result = False
        self.title(title)
        self.geometry("500x220")
        self.resizable(False, False)
        self.configure(fg_color=P["bg"])

        try:
            self.transient(parent)
            self.grab_set()
        except Exception:
            pass

        wrap = ctk.CTkFrame(self, fg_color=P["bg"])
        wrap.pack(fill="both", expand=True, padx=24, pady=24)

        ctk.CTkLabel(wrap, text=message,
                     font=ctk.CTkFont(size=FS["dialog"]),
                     text_color=P["text"],
                     justify="center",
                     wraplength=420).pack(expand=True)

        btns = ctk.CTkFrame(wrap, fg_color="transparent")
        btns.pack(fill="x", pady=(14, 0))

        ctk.CTkButton(btns, text=t("dlg_no"), command=self._no,
                      fg_color=P["panel"], hover_color=P["card_hover"],
                      text_color=P["text"],
                      font=ctk.CTkFont(size=FS["dialog_btn"]),
                      width=120, height=40).pack(side="right", padx=(8, 0))
        ctk.CTkButton(btns, text=t("dlg_yes"), command=self._yes,
                      fg_color=P["danger"], hover_color=P["danger_hover"],
                      text_color=P["text"],
                      font=ctk.CTkFont(size=FS["dialog_btn"]),
                      width=120, height=40).pack(side="right")

        self.bind("<Escape>", lambda e: self._no())

        try:
            self.wait_window()
        except Exception:
            pass

    def _yes(self):
        self.result = True
        self.destroy()

    def _no(self):
        self.result = False
        self.destroy()

# =====================================================================
#  Main application
# =====================================================================

class DoKeyApp(ctk.CTk):

    def __init__(self):
        super().__init__()

        self._alive = True
        self.selected_tab = 0

        self._state_lock = threading.Lock()
        self._state = {
            "p2_device_id": None,
            "p2_vk_to_control": {},
            "running": False,
            "controller": None,
        }

        self.devices = {}

        self.listener = None
        self._event_queue = queue.Queue()

        self.detecting = False
        self.binding_active = False
        self.binding_control = None

        self.current_layout_name = None
        self._layout_display_map = {}  # displayed -> real name (or None)

        self.control_labels = {}
        self.control_rows = {}
        self.detect_btn = None
        self.layout_menu = None
        self.layout_var = None
        self.delete_layout_btn = None

        self.title(t_raw("app_title"))
        self.geometry("1180x920")
        self.minsize(1040, 780)
        self.configure(fg_color=P["bg"])

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self._build_ui()

        self._load_last_layout_into_state()

        self._start_listener()
        self.after(20, self._poll_events)
        self.after(400, self._check_listener_status)

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.bind("<Escape>", lambda e: self._cancel_all())

        self._rebuild_tab_content()
        self._update_badge_for_listener()

    # =================================================================
    #  Language
    # =================================================================

    def _on_lang_toggle(self, value):
        lang = "fa" if value == "دری" else "en"
        if lang == _current_lang:
            return
        set_lang(lang)
        set_config_value("language", lang)
        self._apply_language_to_ui()

    def _apply_language_to_ui(self):
        try:
            self.title(t_raw("app_title"))
            self.title_label.configure(text=t("app_name"))
            self.subtitle_label.configure(text=t("app_subtitle"))
            tabs = [t("tab_p1"), t("tab_p2")]
            self.tabs_bar.configure(values=tabs)
            self.tabs_bar.set(tabs[self.selected_tab])
            self.start_btn.configure(text=t("btn_start"))
            self.stop_btn.configure(text=t("btn_stop"))
            self._update_badge_for_listener()
        except Exception:
            traceback.print_exc()
        self._rebuild_tab_content()
        self._set_status(t("status_ready"))

    def _update_badge_for_listener(self):
        try:
            with self._state_lock:
                running = self._state["running"]
            if running:
                self.driver_badge.configure(
                    text=t("badge_running"), text_color=P["success"])
            elif self.listener is not None and self.listener.started_ok:
                self.driver_badge.configure(
                    text=t("badge_driver_ok"), text_color=P["success"])
            elif self.listener is not None and self.listener.error:
                self.driver_badge.configure(
                    text=t("badge_driver_failed"), text_color=P["danger"])
            else:
                self.driver_badge.configure(
                    text=t("badge_checking"), text_color=P["text_dim"])
        except Exception:
            pass

    # =================================================================
    #  Layout persistence (state side)
    # =================================================================

    def _load_last_layout_into_state(self):
        name = get_config_value("last_layout")
        if not name:
            return
        layout = load_layout(name)
        if layout is None:
            return
        with self._state_lock:
            self._state["p2_device_id"] = layout["device_id"]
            self._state["p2_vk_to_control"] = layout["vk_map"]
        self.current_layout_name = layout["name"]

    # =================================================================
    #  Listener
    # =================================================================

    def _start_listener(self):
        if not INTERCEPTION_DLL_PATH:
            return
        self.listener = InterceptionListener(self._on_listener_event)
        self.listener.start()

    def _check_listener_status(self):
        if self.listener is None:
            self.driver_badge.configure(
                text=t("badge_driver_failed"), text_color=P["danger"])
            return
        if not self.listener.ready.is_set():
            self.after(400, self._check_listener_status)
            return
        self._update_badge_for_listener()
        if not self.listener.started_ok and self.listener.error:
            self._set_status(f"⚠ {self.listener.error}")

    # =================================================================
    #  Listener callback
    # =================================================================

    def _on_listener_event(self, dev_id, vk, is_down):
        try:
            self._event_queue.put_nowait((dev_id, vk, is_down))
        except Exception:
            pass

        with self._state_lock:
            running = self._state["running"]
            p2_dev = self._state["p2_device_id"]
            vk_map = self._state["p2_vk_to_control"]
            controller = self._state["controller"]

        if not running or p2_dev is None or dev_id != p2_dev:
            return False

        control = vk_map.get(vk)
        if control is None or controller is None:
            return False

        try:
            if control in XBOX_BUTTONS:
                if is_down:
                    controller.button_down(control)
                else:
                    controller.button_up(control)
            elif control in ANALOG_NAMES:
                if is_down:
                    controller.analog_down(control)
                else:
                    controller.analog_up(control)
        except Exception:
            traceback.print_exc()

        return True

    # =================================================================
    #  Main-thread event polling
    # =================================================================

    def _poll_events(self):
        if not self._alive:
            return
        try:
            processed = 0
            while processed < 300:
                try:
                    dev_id, vk, is_down = self._event_queue.get_nowait()
                except queue.Empty:
                    break
                processed += 1
                try:
                    self._handle_ui_event(dev_id, vk, is_down)
                except Exception:
                    traceback.print_exc()
        finally:
            if self._alive:
                self.after(20, self._poll_events)

    def _handle_ui_event(self, dev_id, vk, is_down):
        if dev_id not in self.devices:
            self.devices[dev_id] = t("keyboard_device_fmt", id=dev_id)
            if DEBUG:
                print(f"[DoKey][debug] New device: #{dev_id}")

        if self.detecting and is_down:
            self.detecting = False
            with self._state_lock:
                self._state["p2_device_id"] = dev_id
            self._rebuild_tab_content()
            self._set_status(t("status_p2_kb_set",
                               name=self.devices[dev_id]))
            return

        if self.binding_active and is_down:
            with self._state_lock:
                p2_dev = self._state["p2_device_id"]
            if p2_dev is None or p2_dev != dev_id:
                return
            control = self.binding_control
            if control is None:
                return
            with self._state_lock:
                new_map = {k: v for k, v in
                           self._state["p2_vk_to_control"].items()
                           if v != control}
                new_map[vk] = control
                self._state["p2_vk_to_control"] = new_map
            lbl = self.control_labels.get(control)
            if lbl is not None:
                lbl.configure(text=vk_name(vk),
                              fg_color=P["accent"], text_color=P["text"])
            row = self.control_rows.get(control)
            if row:
                _, bind_btn, _ = row
                bind_btn.configure(text=t("btn_bind"),
                                   fg_color=P["panel"],
                                   hover_color=P["card_hover"],
                                   text_color=P["text"],
                                   command=lambda c=control:
                                       self._start_binding(c))
            self.binding_active = False
            self.binding_control = None
            self._set_status(t("status_bound",
                               key=vk_name(vk), control=control))

    # =================================================================
    #  UI construction
    # =================================================================

    def _build_ui(self):
        # ---- Header ----
        header = ctk.CTkFrame(self, fg_color=P["panel"],
                              corner_radius=0, height=84)
        header.pack(fill="x"); header.pack_propagate(False)

        tb = ctk.CTkFrame(header, fg_color="transparent")
        tb.pack(side="left", padx=28, pady=14)
        self.title_label = ctk.CTkLabel(
            tb, text=t("app_name"),
            font=ctk.CTkFont(size=FS["app_name"], weight="bold"),
            text_color=P["text"])
        self.title_label.pack(anchor="w")
        self.subtitle_label = ctk.CTkLabel(
            tb, text=t("app_subtitle"),
            font=ctk.CTkFont(size=FS["app_subtitle"]),
            text_color=P["text_dim"])
        self.subtitle_label.pack(anchor="w", pady=(2, 0))

        self.driver_badge = ctk.CTkLabel(
            header, text=t("badge_checking"),
            font=ctk.CTkFont(size=FS["badge"], weight="bold"),
            text_color=P["text_dim"])
        self.driver_badge.pack(side="right", padx=(8, 28))

        self.lang_seg = ctk.CTkSegmentedButton(
            header, values=["EN", "دری"],
            command=self._on_lang_toggle,
            selected_color=P["accent"],
            selected_hover_color=P["accent_hover"],
            unselected_color=P["card"],
            unselected_hover_color=P["card_hover"],
            text_color=P["text"],
            font=ctk.CTkFont(size=FS["lang"], weight="bold"),
            width=130, height=38)
        self.lang_seg.pack(side="right", padx=(8, 0))
        self.lang_seg.set("دری" if _current_lang == "fa" else "EN")

        # ---- Toolbar ----
        bar = ctk.CTkFrame(self, fg_color=P["panel"],
                           corner_radius=0, height=76)
        bar.pack(fill="x"); bar.pack_propagate(False)
        inner = ctk.CTkFrame(bar, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=24, pady=14)

        self.tabs_bar = ctk.CTkSegmentedButton(
            inner, values=[t("tab_p1"), t("tab_p2")],
            command=self._on_tab_change,
            selected_color=P["accent"],
            selected_hover_color=P["accent_hover"],
            unselected_color=P["card"],
            unselected_hover_color=P["card_hover"],
            text_color=P["text"],
            font=ctk.CTkFont(size=FS["tab"], weight="bold"),
            height=42, width=320)
        self.tabs_bar.pack(side="left")
        self.tabs_bar.set(t("tab_p1"))

        self.start_btn = ctk.CTkButton(
            inner, text=t("btn_start"), command=self._on_start,
            fg_color=P["success"], hover_color=P["success_hover"],
            text_color="#0d0f1a",
            font=ctk.CTkFont(size=FS["btn_main"], weight="bold"),
            width=150, height=42)
        self.start_btn.pack(side="right", padx=(10, 0))

        self.stop_btn = ctk.CTkButton(
            inner, text=t("btn_stop"), command=self._on_stop,
            fg_color=P["danger"], hover_color=P["danger_hover"],
            text_color=P["text"],
            font=ctk.CTkFont(size=FS["btn_main"], weight="bold"),
            width=150, height=42, state="disabled")
        self.stop_btn.pack(side="right", padx=(10, 0))

        # ---- Content ----
        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.pack(fill="both", expand=True, padx=24, pady=(16, 14))

        # ---- Status bar ----
        sb = ctk.CTkFrame(self, fg_color=P["panel"],
                          corner_radius=0, height=38)
        sb.pack(fill="x", side="bottom"); sb.pack_propagate(False)
        self.status_label = ctk.CTkLabel(
            sb, text=t("status_ready"),
            font=ctk.CTkFont(size=FS["status"]),
            text_color=P["text_dim"])
        self.status_label.pack(side="left", padx=24)

    def _rebuild_tab_content(self):
        for c in self.content.winfo_children():
            c.destroy()
        self.control_labels = {}
        self.control_rows = {}
        self._build_player1_panel()
        self._build_player2_panel()
        self._show_tab(self.selected_tab)

    def _show_tab(self, index):
        self.selected_tab = index
        panels = self.content.winfo_children()
        for i, panel in enumerate(panels):
            if i == index:
                panel.pack(fill="both", expand=True)
            else:
                panel.pack_forget()

    # ---- Player 1 panel ----
    def _build_player1_panel(self):
        panel = ctk.CTkFrame(self.content, fg_color="transparent")
        card = ctk.CTkFrame(panel, fg_color=P["card"], corner_radius=14)
        card.pack(fill="both", expand=True)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(expand=True)

        ctk.CTkLabel(inner, text="💻",
                     font=ctk.CTkFont(size=52)).pack(pady=(50, 12))
        ctk.CTkLabel(inner, text=t("p1_title"),
                     font=ctk.CTkFont(size=FS["section_big"], weight="bold"),
                     text_color=P["text"]).pack(pady=(0, 16))
        ctk.CTkLabel(inner, text=t("p1_body"),
                     font=ctk.CTkFont(size=FS["body"]),
                     text_color=P["text_dim"],
                     justify="center").pack(pady=(0, 24))
        ctk.CTkLabel(inner, text=t("p1_note"),
                     font=ctk.CTkFont(size=FS["card_small"]),
                     text_color=P["warn"],
                     justify="center").pack(pady=(0, 50))
        return panel

    # ---- Player 2 panel ----
    def _build_player2_panel(self):
        panel = ctk.CTkFrame(self.content, fg_color="transparent")

        with self._state_lock:
            p2_dev = self._state["p2_device_id"]

        # ---- Layout card ----
        layout_card = ctk.CTkFrame(panel, fg_color=P["card"], corner_radius=14)
        layout_card.pack(fill="x", pady=(0, 14))

        li = ctk.CTkFrame(layout_card, fg_color="transparent")
        li.pack(fill="x", padx=24, pady=18)

        ctk.CTkLabel(li, text=t("layout_title"),
                     font=ctk.CTkFont(size=FS["card_title"], weight="bold"),
                     text_color=P["text"]).pack(side="left", padx=(0, 18))

        no_layout_label = t("no_layout")
        real_names = list_layouts()
        display_names = [no_layout_label]
        self._layout_display_map = {no_layout_label: None}
        for real in real_names:
            disp = _reshape_rtl(real)
            display_names.append(disp)
            self._layout_display_map[disp] = real

        current_display = no_layout_label
        if self.current_layout_name:
            for disp, real in self._layout_display_map.items():
                if real == self.current_layout_name:
                    current_display = disp
                    break
        if current_display not in display_names:
            current_display = no_layout_label

        self.layout_var = ctk.StringVar(value=current_display)
        self.layout_menu = ctk.CTkOptionMenu(
            li, values=display_names, variable=self.layout_var,
            width=290, height=40,
            fg_color=P["panel"], button_color=P["border"],
            button_hover_color=P["accent"],
            dropdown_fg_color=P["panel"],
            dropdown_hover_color=P["accent"],
            text_color=P["text"],
            font=ctk.CTkFont(size=FS["card_text"]),
            dropdown_font=ctk.CTkFont(size=FS["card_text"]),
            command=self._on_layout_selected)
        self.layout_menu.pack(side="left", padx=(0, 12))

        ctk.CTkButton(
            li, text=t("save_as"), command=self._on_save_layout,
            fg_color=P["accent"], hover_color=P["accent_hover"],
            text_color=P["text"],
            font=ctk.CTkFont(size=FS["card_text"], weight="bold"),
            width=170, height=40).pack(side="left", padx=(0, 10))

        self.delete_layout_btn = ctk.CTkButton(
            li, text=t("btn_delete"), command=self._on_delete_layout,
            fg_color=P["danger"], hover_color=P["danger_hover"],
            text_color=P["text"],
            font=ctk.CTkFont(size=FS["card_text"], weight="bold"),
            width=140, height=40)
        self.delete_layout_btn.pack(side="left")
        if current_display == no_layout_label:
            self.delete_layout_btn.configure(state="disabled")

        # ---- Keyboard card ----
        kb_card = ctk.CTkFrame(panel, fg_color=P["card"], corner_radius=14)
        kb_card.pack(fill="x", pady=(0, 14))

        kbi = ctk.CTkFrame(kb_card, fg_color="transparent")
        kbi.pack(fill="x", padx=24, pady=20)

        ctk.CTkLabel(kbi, text=t("ext_keyboard"),
                     font=ctk.CTkFont(size=FS["card_title"], weight="bold"),
                     text_color=P["text"]).pack(side="left", padx=(0, 18))

        if p2_dev is None:
            ctk.CTkLabel(kbi, text=t("not_detected"),
                         font=ctk.CTkFont(size=FS["card_text"]),
                         text_color=P["text_dim"]).pack(side="left")
        else:
            name = self.devices.get(p2_dev,
                                    t("keyboard_device_fmt", id=p2_dev))
            ctk.CTkLabel(kbi, text=name,
                         font=ctk.CTkFont(size=FS["card_text"], weight="bold"),
                         text_color=P["success"]).pack(side="left")

        detect_btn = ctk.CTkButton(
            kbi, text=t("detect_btn"),
            command=self._start_detect,
            fg_color=P["accent"], hover_color=P["accent_hover"],
            text_color=P["text"],
            font=ctk.CTkFont(size=FS["card_title"], weight="bold"),
            width=210, height=42)
        detect_btn.pack(side="right")
        self.detect_btn = detect_btn

        # ---- Prompt card if no keyboard ----
        if p2_dev is None:
            prompt = ctk.CTkFrame(panel, fg_color=P["card"], corner_radius=14)
            prompt.pack(fill="both", expand=True)
            inner = ctk.CTkFrame(prompt, fg_color="transparent")
            inner.pack(expand=True)

            ctk.CTkLabel(inner, text="🎯",
                         font=ctk.CTkFont(size=52)).pack(pady=(50, 12))
            ctk.CTkLabel(inner, text=t("no_kb_title"),
                         font=ctk.CTkFont(size=FS["section_big"], weight="bold"),
                         text_color=P["text"]).pack(pady=(0, 16))
            ctk.CTkLabel(inner, text=t("no_kb_body"),
                         font=ctk.CTkFont(size=FS["body"]),
                         text_color=P["text_dim"],
                         justify="center").pack(pady=(0, 24))
            ctk.CTkLabel(inner, text=t("no_kb_tip"),
                         font=ctk.CTkFont(size=FS["card_small"]),
                         text_color=P["warn"],
                         justify="center").pack(pady=(0, 50))
            return panel

        # ---- Bindings card ----
        ctl_card = ctk.CTkFrame(panel, fg_color=P["card"], corner_radius=14)
        ctl_card.pack(fill="both", expand=True)

        hdr = ctk.CTkFrame(ctl_card, fg_color="transparent")
        hdr.pack(fill="x", padx=24, pady=(18, 8))
        ctk.CTkLabel(hdr, text=t("bindings_title"),
                     font=ctk.CTkFont(size=FS["card_title"], weight="bold"),
                     text_color=P["text"]).pack(side="left")
        ctk.CTkButton(
            hdr, text=t("btn_clear_all"), command=self._clear_all_bindings,
            fg_color=P["panel"], hover_color=P["card_hover"],
            text_color=P["text"],
            font=ctk.CTkFont(size=FS["row_btn"]),
            width=130, height=34).pack(side="right")

        scroll = ctk.CTkScrollableFrame(
            ctl_card, fg_color=P["panel"], corner_radius=10)
        scroll.pack(fill="both", expand=True, padx=20, pady=(8, 20))

        for col, text in enumerate([t("col_control"), t("col_bound_key"),
                                    "", ""]):
            ctk.CTkLabel(scroll, text=text, anchor="w",
                         font=ctk.CTkFont(size=FS["col_header"], weight="bold"),
                         text_color=P["text_dim"]).grid(
                row=0, column=col, padx=10, pady=(6, 10), sticky="w")

        with self._state_lock:
            vk_map = dict(self._state["p2_vk_to_control"])

        for row, control in enumerate(ALL_CONTROLS, start=1):
            ctk.CTkLabel(scroll, text=control, anchor="w", width=130,
                         font=ctk.CTkFont(size=FS["control"]),
                         text_color=P["text"]).grid(
                row=row, column=0, padx=10, pady=6, sticky="w")

            key_lbl = ctk.CTkLabel(
                scroll, text="—", anchor="w",
                font=ctk.CTkFont(size=FS["bound_key"], weight="bold"),
                text_color=P["text_dim"],
                fg_color=P["card"], corner_radius=8,
                width=190, height=36)
            key_lbl.grid(row=row, column=1, padx=10, pady=6, sticky="w")

            bind_btn = ctk.CTkButton(
                scroll, text=t("btn_bind"), width=100, height=34,
                command=lambda c=control: self._start_binding(c),
                fg_color=P["panel"], hover_color=P["card_hover"],
                text_color=P["text"],
                font=ctk.CTkFont(size=FS["row_btn"]),
                border_width=1, border_color=P["border"])
            bind_btn.grid(row=row, column=2, padx=10, pady=6)

            clear_btn = ctk.CTkButton(
                scroll, text=t("btn_clear"), width=100, height=34,
                command=lambda c=control: self._clear_binding(c),
                fg_color=P["panel"], hover_color=P["card_hover"],
                text_color=P["text"],
                font=ctk.CTkFont(size=FS["row_btn"]),
                border_width=1, border_color=P["border"])
            clear_btn.grid(row=row, column=3, padx=10, pady=6)

            self.control_labels[control] = key_lbl
            self.control_rows[control] = (key_lbl, bind_btn, clear_btn)

            for vk, ctrl in vk_map.items():
                if ctrl == control:
                    key_lbl.configure(text=vk_name(vk),
                                      fg_color=P["accent"],
                                      text_color=P["text"])
                    break

        return panel

    # =================================================================
    #  Layout handlers
    # =================================================================

    def _on_layout_selected(self, value):
        real_name = self._layout_display_map.get(value, None)
        if real_name is None:
            self.current_layout_name = None
            set_config_value("last_layout", None)
            with self._state_lock:
                self._state["p2_vk_to_control"] = {}
            self._rebuild_tab_content()
            self._set_status(t("status_layout_cleared"))
            return

        layout = load_layout(real_name)
        if layout is None:
            self._set_status(t("status_layout_load_error", name=real_name))
            return

        with self._state_lock:
            self._state["p2_device_id"] = layout["device_id"]
            self._state["p2_vk_to_control"] = layout["vk_map"]
        self.current_layout_name = layout["name"]
        set_config_value("last_layout", layout["name"])
        self._rebuild_tab_content()

        dev_note = ""
        if layout["device_id"] is None:
            dev_note = t("status_layout_no_kb")
        self._set_status(t("status_layout_loaded",
                           name=_reshape_rtl(layout["name"]),
                           n=len(layout["vk_map"]),
                           dev_note=dev_note))

    def _on_save_layout(self):
        with self._state_lock:
            p2_dev = self._state["p2_device_id"]
            vk_map = dict(self._state["p2_vk_to_control"])

        if p2_dev is None and not vk_map:
            self._set_status(t("status_nothing_to_save"))
            return

        dlg = NameDialog(self,
                         title=t("dlg_save_title"),
                         prompt=t("dlg_save_text"),
                         initial=self.current_layout_name or "")
        name = dlg.result
        if not name:
            return

        existing = list_layouts()
        match = next((n for n in existing if n.lower() == name.lower()), None)
        if match is not None and match != self.current_layout_name:
            confirm = ConfirmDialog(self,
                                    title=t("dlg_overwrite_title"),
                                    message=t("dlg_overwrite_text", name=match))
            if not confirm.result:
                return
            name = match

        if not save_layout(name, p2_dev, vk_map):
            self._set_status(t("status_layout_save_failed", name=name))
            return

        self.current_layout_name = name
        set_config_value("last_layout", name)
        self._rebuild_tab_content()
        self._set_status(t("status_layout_saved",
                           name=_reshape_rtl(name), n=len(vk_map)))

    def _on_delete_layout(self):
        if not self.current_layout_name:
            self._set_status(t("status_no_layout_sel"))
            return
        name = self.current_layout_name
        confirm = ConfirmDialog(self,
                                title=t("dlg_delete_title"),
                                message=t("dlg_delete_text",
                                          name=_reshape_rtl(name)))
        if not confirm.result:
            return
        if delete_layout(name):
            self.current_layout_name = None
            if get_config_value("last_layout") == name:
                set_config_value("last_layout", None)
            self._rebuild_tab_content()
            self._set_status(t("status_layout_deleted",
                               name=_reshape_rtl(name)))
        else:
            self._set_status(t("status_layout_delete_failed",
                               name=_reshape_rtl(name)))

    # =================================================================
    #  Handlers
    # =================================================================

    def _on_tab_change(self, value):
        tabs = [t("tab_p1"), t("tab_p2")]
        try:
            idx = tabs.index(value)
        except ValueError:
            idx = 0
        self._cancel_all()
        self._show_tab(idx)

    def _start_detect(self):
        with self._state_lock:
            running = self._state["running"]
        if running:
            self._set_status(t("status_stop_first_detect"))
            return
        self._cancel_binding()
        self.detecting = True
        try:
            if self.detect_btn is not None:
                self.detect_btn.configure(
                    text=t("listening_btn"),
                    fg_color=P["binding"],
                    hover_color=P["binding_hover"],
                    text_color="#0d0f1a")
        except Exception:
            pass
        self._set_status(t("status_detect_prompt"))

    def _start_binding(self, control):
        with self._state_lock:
            running = self._state["running"]
            p2_dev = self._state["p2_device_id"]
        if running:
            self._set_status(t("status_stop_first_bind"))
            return
        if p2_dev is None:
            self._set_status(t("status_detect_first"))
            return
        self._cancel_binding()
        self.binding_active = True
        self.binding_control = control
        row = self.control_rows.get(control)
        if row:
            key_lbl, bind_btn, _ = row
            key_lbl.configure(text=t("press_any_key"),
                              fg_color=P["binding"],
                              text_color="#0d0f1a")
            bind_btn.configure(text=t("btn_cancel"),
                               fg_color=P["binding"],
                               hover_color=P["binding_hover"],
                               text_color="#0d0f1a",
                               command=self._cancel_binding)
        self._set_status(t("status_press_key_bind", control=control))

    def _cancel_binding(self):
        if not self.binding_active:
            return
        control = self.binding_control
        self.binding_active = False
        self.binding_control = None
        if control is None:
            return
        row = self.control_rows.get(control)
        if not row:
            return
        key_lbl, bind_btn, _ = row
        with self._state_lock:
            vk_map = dict(self._state["p2_vk_to_control"])
        existing = [vk for vk, c in vk_map.items() if c == control]
        if existing:
            key_lbl.configure(text=vk_name(existing[0]),
                              fg_color=P["accent"], text_color=P["text"])
        else:
            key_lbl.configure(text="—",
                              fg_color=P["card"], text_color=P["text_dim"])
        bind_btn.configure(text=t("btn_bind"),
                           fg_color=P["panel"],
                           hover_color=P["card_hover"],
                           text_color=P["text"],
                           command=lambda c=control:
                               self._start_binding(c))

    def _cancel_all(self):
        self._cancel_binding()
        if self.detecting:
            self.detecting = False
            try:
                if self.detect_btn is not None:
                    self.detect_btn.configure(
                        text=t("detect_btn"),
                        fg_color=P["accent"],
                        hover_color=P["accent_hover"],
                        text_color=P["text"])
            except Exception:
                pass

    def _clear_binding(self, control):
        with self._state_lock:
            new_map = {k: v for k, v in
                       self._state["p2_vk_to_control"].items()
                       if v != control}
            self._state["p2_vk_to_control"] = new_map
        lbl = self.control_labels.get(control)
        if lbl:
            lbl.configure(text="—",
                          fg_color=P["card"], text_color=P["text_dim"])
        self._set_status(t("status_cleared", control=control))

    def _clear_all_bindings(self):
        with self._state_lock:
            self._state["p2_vk_to_control"] = {}
        for _, lbl in self.control_labels.items():
            lbl.configure(text="—",
                          fg_color=P["card"], text_color=P["text_dim"])
        self._set_status(t("status_cleared_all"))

    # =================================================================
    #  Start / Stop
    # =================================================================

    def _on_start(self):
        if self.listener is None or not self.listener.started_ok:
            self._set_status(t("status_cant_start"))
            return

        with self._state_lock:
            p2_dev = self._state["p2_device_id"]
            vk_map = dict(self._state["p2_vk_to_control"])

        if p2_dev is None:
            self._set_status(t("status_no_p2_kb"))
            return
        if not vk_map:
            self._set_status(t("status_no_bindings"))
            return

        with self._state_lock:
            old = self._state["controller"]
            self._state["controller"] = None
        if old is not None:
            try: old.reset()
            except Exception: pass
        gc.collect()
        time.sleep(0.2)

        try:
            ctrl = VirtualController()
        except Exception as e:
            self._set_status(t("status_ctrl_failed", err=str(e)))
            return

        with self._state_lock:
            self._state["controller"] = ctrl
            self._state["running"] = True

        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.driver_badge.configure(text=t("badge_running"),
                                    text_color=P["success"])
        self._set_status(t("status_running", n=len(vk_map)))

    def _on_stop(self):
        with self._state_lock:
            self._state["running"] = False
            ctrl = self._state["controller"]
            self._state["controller"] = None

        if ctrl is not None:
            try: ctrl.reset()
            except Exception: pass

        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self._update_badge_for_listener()
        self._set_status(t("status_stopped"))

    def _set_status(self, text):
        try:
            self.status_label.configure(text=text)
        except Exception:
            pass

    def on_close(self):
        self._alive = False
        try:
            if self.listener is not None:
                self.listener.stop()
        except Exception:
            pass
        try:
            self._on_stop()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass

# =====================================================================
#  Entry point
# =====================================================================

def main():
    try:
        test = vg.VX360Gamepad()
        test.reset()
        del test
        gc.collect()
    except Exception as e:
        print(f"[DoKey] Cannot create a virtual controller: {e}")
        print("[DoKey] Reboot after installing ViGEmBus and try again.")
        sys.exit(1)

    ok, msg = test_interception_driver()
    if not ok:
        print()
        print("=" * 70)
        print("[DoKey] INTERCEPTION DRIVER TEST FAILED")
        print(f"[DoKey] {msg}")
        print("=" * 70)
    else:
        print("[DoKey] Interception driver OK. Opening DoKey.")

    app = DoKeyApp()
    app.mainloop()

if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        input("Press Enter to exit...")