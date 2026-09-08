# VTPiP 0.9.9b

Watch YouTube in a picture-in-picture stamp over your game.

VTPiP is a [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) plugin for **Steam Game Mode**. It runs **[VacuumTube](https://github.com/shy1132/VacuumTube)** (the YouTube TV app) as a Steam app, stamps a live picture of it on the game, and lets you pick videos from the Quick Access Menu (`…`).

It is **not** affiliated with YouTube, Google, Valve, or VacuumTube.

---

## What you need

You cannot skip these. The plugin will not work without them.

| Required | Why | How you get it |
| --- | --- | --- |
| **Steam Game Mode** (SteamOS, Bazzite, ChimeraOS, or a similar gamescope session) | The overlay is a gamescope external overlay. Desktop Mode is only for installing. | Already how you play games on Deck / Steam Machine |
| **[Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader)** | VTPiP is a Decky plugin. The installer puts it in `~/homebrew/plugins/VTPiP`. | Install Decky first, then VTPiP |
| **Flatpak** | VacuumTube is a Flathub app. VTPiP does not bundle YouTube. | Already on SteamOS and Bazzite |
| **Python 3 + GTK 3** | The overlay helper is a small Python/GTK window. | Already on SteamOS and Bazzite |
| **VacuumTube** (`rocks.shy.VacuumTube`) | That is the YouTube TV window the stamp copies. | The installer can install it, or tap **Install VacuumTube** in the QAM |

**Also required to actually see the stamp**

1. VacuumTube must be **started from VTPiP** (so Steam launches it and it shows in **running apps**).
2. A **game** must be focused. The stamp hides while you are inside VacuumTube so you do not get two copies of YouTube.
3. **Show over game** must be on in the VTPiP menu.

**Optional**

- A Google account signed in inside VacuumTube — needed for **Watch Later**. TV-only pairing is not enough.
- A controller — everything in the QAM is meant to be used with D-pad / A / B.

**Works on**

- Steam Deck LCD and OLED (1280×800)
- SteamOS PCs and homemade Steam Machines (720p / 1080p / 1440p / 4K)

The overlay follows the gamescope output. It does not assume a Deck panel. If a 720p stamp would not fit, it shrinks.

**Does not work**

- Steam Desktop Mode as the place you watch (install there, play in Game Mode)
- Windows
- A session without gamescope / Decky
- Capturing a random browser. Only VacuumTube launched through VTPiP.

---

## Install

### 1. Install Decky Loader

If Decky is not already on the machine: [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader). You should see the Decky icon in the Quick Access Menu (`…`).

### 2. Install VTPiP

**From a terminal in Desktop Mode** (easiest):

```bash
curl -fsSL https://raw.githubusercontent.com/Memberoffoxhound/VTPiP/main/install.sh | bash
```

That checks for Decky, copies the plugin into `~/homebrew/plugins/VTPiP`, restarts the plugin loader, and offers to install VacuumTube from Flathub.

Re-run the same command to update.

**From a clone of this repo**

```bash
git clone https://github.com/Memberoffoxhound/VTPiP.git
cd VTPiP
./install.sh
```

**From Decky (ZIP)**

1. Decky → **General** → enable **Developer Mode**
2. **Developer** → **Install plugin from ZIP** (or Install from URL)
3. Use the `VTPiP.zip` from the [latest GitHub release](https://github.com/Memberoffoxhound/VTPiP/releases/latest)

Release URL for “Install from URL”:

```
https://github.com/Memberoffoxhound/VTPiP/releases/latest/download/VTPiP.zip
```

Installer flags:

```bash
./install.sh --help
./install.sh --skip-vacuumtube    # plugin only; install YouTube later from the QAM
./install.sh --from-release       # even from a git checkout, pull the GitHub zip
```

### 3. Install VacuumTube if the installer did not

VacuumTube is **not** shipped inside this plugin. It is the official Flathub package, so YouTube updates the normal way.

From the VTPiP menu: **Install VacuumTube**.

Or in Desktop Mode:

```bash
flatpak remote-add --user --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
flatpak install --user -y flathub rocks.shy.VacuumTube
```

---

## First run

Do this in **Game Mode**.

1. Open the Quick Access Menu (`…`) → Decky → **VTPiP**.
2. If VacuumTube is missing, tap **Install VacuumTube** and wait.
3. Tap **Start VacuumTube**. Steam launches it. It must appear in the Steam **running apps** list. Do not start it from Desktop Mode.
4. In VacuumTube, sign in (**Account** on the TV home) if you want Watch Later.
5. Launch a game.
6. Open VTPiP again and turn **Show over game** on.
7. Pick a position on the preview (four corners plus left-middle / right-middle), then size and opacity.

Play the game. Hear YouTube. Glance at the stamp.

When you want the next video you can stay in the QAM (Play / Pause, Watch Later, Search) or press the Steam button → running apps → **VacuumTube** for the full TV UI, then switch back. The stamp comes back when the game is focused.

---

## What it does

- Installs VacuumTube with your system Flatpak (same as Discover / Flathub).
- Adds VacuumTube to Steam as a non-Steam app via a small wrapper (`bin/vtpip-run`) so it shows in **running apps**.
- Copies that YouTube window (Chrome DevTools screencast, with X11 capture as fallback) into a 480p or 720p stamp over the game.
- Lets you place the stamp, set opacity, and mix YouTube / game volume from the QAM.

**From the QAM (no need to switch to VacuumTube)**

- Play / Pause, Prev / Next, −15s / +15s, live timecode
- Watch Later (signed-in VacuumTube account)
- Search YouTube — A on the box opens Steam’s keyboard
- Comments on the current video
- Position preview at your display’s aspect ratio; D-pad to a slot, A to place
- Size (small 480p / large 720p), opacity, YouTube volume, game volume, mute
- **B** leaves Watch Later / Search / Comments. On the main menu, B closes the QAM.

---

## Troubleshooting

- **Stuck / nothing happens after Start VacuumTube.** Steam has to launch it. Stay in Game Mode, tap **Start VacuumTube** again, then Steam button → confirm **VacuumTube** is in running apps.
- **No picture, but you hear YouTube.** **Show over game** must be on, and a **game** (not VacuumTube) must be focused. The stamp hides inside VacuumTube on purpose.
- **Blank / black stamp.** The overlay copies VacuumTube’s window (or DevTools), never the gamescope root (that pixmap is black). Restart the plugin, then Start VacuumTube from VTPiP, then toggle **Show over game**.
- **Interlaced, slanted, or doubled picture.** Old builds grabbed Chromium’s GPU buffer over X11 (common on 4K). Current builds screencast through DevTools. Restart VacuumTube from the VTPiP menu so it is launched with the debug port (`9224`).
- **Watch Later is empty.** Open VacuumTube full screen → Account → sign in with Google.
- **Search box does not type.** Highlight it, press A for Steam’s keyboard. B closes the keyboard; B again leaves Search.
- **VacuumTube is not in running apps.** You started it from Desktop Mode or Discover. Stop it and start it from the VTPiP menu.
- **Install fails.** In Desktop Mode: `flatpak install --user flathub rocks.shy.VacuumTube`
- **Decky not found / installer refuses.** Install [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) first. The plugin directory is `~/homebrew/plugins/VTPiP`.
- **Logs.** `~/homebrew/logs/VTPiP/`

---

## How the overlay works

VTPiP draws a fullscreen transparent X11 window tagged `GAMESCOPE_EXTERNAL_OVERLAY` (same recipe as mangoapp / GameModeCord) so gamescope composites it over the running game.

VacuumTube is started with `--ozone-platform=x11` and a DevTools port. The overlay prefers **Page.screencast** over that port. Grabbing a GPU Chromium window with X11/GStreamer under gamescope often comes back tiled or slanted, so that path is only a fallback. The gamescope Xwayland *root* is never captured — it is empty.

---

## Legal

[VacuumTube](https://github.com/shy1132/VacuumTube) is an unofficial open-source YouTube TV wrapper (**MIT License**). VTPiP does **not** copy, bundle, or modify VacuumTube. It only:

- installs the official Flathub package with your own Flatpak
- launches that app through Steam
- captures its window to draw a picture-in-picture overlay

YouTube, the YouTube logo, and related marks belong to Google LLC. VacuumTube belongs to its authors. This plugin is an independent overlay. Using VacuumTube is subject to YouTube’s terms and to VacuumTube’s own license.

---

## Credits

- [VacuumTube](https://github.com/shy1132/VacuumTube) by shy — the YouTube TV app this plugin overlays
- [GameModeCord](https://github.com/Memberoffoxhound/GameModeCord) — Game Mode overlay window recipe (`GAMESCOPE_EXTERNAL_OVERLAY`)
- [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader)

## License

[BSD 3-Clause](LICENSE). Copyright (c) 2026 Memberoffoxhound.
