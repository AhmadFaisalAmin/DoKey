# ⚡ DoKey — دو کلید

**Two-Player Keyboard Splitter for Windows**

Play local co-op games with two players sharing one PC — one on the built-in laptop keyboard, the other on an external keyboard that acts as a virtual Xbox 360 controller.

Built by **Ahmad Faisal Amin**

---

## 📖 What is DoKey?

DoKey is a Windows desktop app that lets **two players play a local co-op game on a single PC** without needing a physical gamepad.

It works by:

1. **Player 1** uses the built-in laptop keyboard directly — the game sees normal keyboard input.
2. **Player 2** uses a second (external) keyboard. DoKey captures the keys, **blocks them from reaching Windows**, and translates them into a **virtual Xbox 360 controller** using the ViGEmBus driver.

The result: any game that supports both keyboard and controller at the same time can be played by two people side-by-side on one machine.

> Perfect for offline, single-player, and split-screen co-op games. **Not** for competitive online games — see the [Anti-Cheat warning](#-anti-cheat-warning) below.

---

## ✨ Features

- 🎮 **Virtual Xbox 360 controller** for Player 2 (via ViGEmBus)
- ⌨️ **Any external keyboard** can be detected and bound — hot-swap friendly
- 🔒 **Built-in keyboard is reserved for Player 1** — never captured by mistake
- 🎯 **Multiple keys per button** — bind `W`, `A`, `S`, `D` _and_ arrow keys to the same D-Pad direction
- 🎨 **Live controller preview** — see button presses light up in real time
- 💾 **Layouts** — save different key mappings for different games
- 🔗 **Link to Game** — auto-load a layout when a specific game becomes the foreground window
- 🖥️ **System tray** support — minimize to tray, quit from tray, quick Start/Stop
- ⌨️ **Global hotkey** — `Ctrl+Shift+F9` toggles Start/Stop (with fallbacks)
- 🔌 **Auto-reconnect** — if the external keyboard is unplugged and replugged, DoKey re-detects it automatically
- 🌐 **Bilingual UI** — English and Persian (دری), with proper RTL text rendering

---

## 📋 Requirements

- **Windows 10 or Windows 11** (64-bit)
- A **laptop with a built-in keyboard** _and_ an **external USB/Bluetooth keyboard**
  _(A desktop with two external keyboards works too.)_
- Administrator rights **once** — to install the Interception kernel driver
- A reboot after driver installation

---

## 🚀 Installation

### Option A — Use the prebuilt `.exe` (recommended)

1. Download `DoKey.exe` from the [Releases](../../releases) page.
2. Put it anywhere — Desktop, a USB stick, wherever.
3. Double-click it.
4. On first run:
   - DoKey downloads and installs the **Interception driver** (you'll see a UAC prompt — click Yes).
   - It also asks to install **ViGEmBus** if missing.
   - **Restart Windows** to finish driver activation.
5. Run DoKey again — you're ready to play.

> **Windows SmartScreen warning:** Because DoKey is not code-signed, Windows may warn you the first time. Click **More info → Run anyway**. This is normal for any freshly built, unsigned application.

### Option B — Run from source

```powershell
git clone https://github.com/AhmadFaisalAmin/DoKey.git
cd DoKey
python dokey.py
```
