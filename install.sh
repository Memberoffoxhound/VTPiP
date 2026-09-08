#!/usr/bin/env bash
# VTPiP — install / update for Steam Deck, SteamOS, Bazzite, Steam Machines.
#
#   curl -fsSL https://raw.githubusercontent.com/Memberoffoxhound/VTPiP/main/install.sh | bash
#
# From a git checkout of this repo, the same script copies the local tree.
#
# Required: Decky Loader, Steam Game Mode (for use), Flatpak, Python 3.
# VacuumTube (rocks.shy.VacuumTube) is installed from Flathub unless you pass
# --skip-vacuumtube. The plugin can also install it later from the QAM.
set -euo pipefail

REPO="Memberoffoxhound/VTPiP"
PLUGIN="VTPiP"
VERSION="0.9.9b"
DEST="${HOME}/homebrew/plugins/${PLUGIN}"
ZIP_URL="https://github.com/${REPO}/releases/latest/download/${PLUGIN}.zip"
API_URL="https://api.github.com/repos/${REPO}/releases/latest"
TAG_TAR_URL="https://github.com/${REPO}/archive/refs/tags/v${VERSION}.tar.gz"
MAIN_TAR_URL="https://github.com/${REPO}/archive/refs/heads/main.tar.gz"
FLATPAK_APP="rocks.shy.VacuumTube"

SKIP_VT=0
FROM_RELEASE=0

say() { printf 'VTPiP: %s\n' "$*"; }
die() { printf 'VTPiP: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<EOF
VTPiP ${VERSION} installer

Usage: install.sh [options]

  --skip-vacuumtube   Install the plugin only. You can still tap
                      "Install VacuumTube" in the Quick Access Menu.
  --from-release      Ignore a local checkout and download the GitHub zip.
  --help              Show this help.

One-liner (Desktop Mode terminal):

  curl -fsSL https://raw.githubusercontent.com/${REPO}/main/install.sh | bash

Required before this will work in Game Mode:

  - Decky Loader   https://github.com/SteamDeckHomebrew/decky-loader
  - Flatpak        (SteamOS / Bazzite already have it)
  - Python 3 + GTK 3
  - VacuumTube     Flathub app ${FLATPAK_APP} (this script can install it)

Use the plugin in Steam Game Mode, not Desktop Mode.
EOF
}

for arg in "$@"; do
  case "$arg" in
    --skip-vacuumtube|--skip-vacuum) SKIP_VT=1 ;;
    --from-release) FROM_RELEASE=1 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $arg  (try --help)" ;;
  esac
done

need() {
  command -v "$1" >/dev/null 2>&1 || die "missing '$1'. Install it and re-run."
}

have() { command -v "$1" >/dev/null 2>&1; }

# Piped `curl | bash` has no useful BASH_SOURCE; a local clone does.
SELF="${BASH_SOURCE[0]:-}"
LOCAL=""
if [[ -n "$SELF" && -f "$SELF" ]]; then
  _dir="$(cd "$(dirname "$SELF")" && pwd)"
  if [[ -f "$_dir/plugin.json" && -f "$_dir/main.py" ]]; then
    LOCAL="$_dir"
  fi
fi

say "VTPiP ${VERSION}"

# --- required: Decky ---
if [[ ! -d "${HOME}/homebrew" ]]; then
  die "Decky Loader not found at ${HOME}/homebrew
Install Decky first:
  https://github.com/SteamDeckHomebrew/decky-loader
then re-run this script.

VTPiP is a Decky plugin. It cannot run without Decky."
fi
if [[ ! -d "${HOME}/homebrew/plugins" ]]; then
  die "Decky plugins folder missing (${HOME}/homebrew/plugins).
Is Decky Loader fully installed?"
fi

need python3
if ! python3 -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk, Gdk, GdkPixbuf" 2>/dev/null; then
  say "warning: Python GTK 3 is not importable. The overlay may fail to start."
  say "On SteamOS / Bazzite this is normally already there."
fi

SUDO=""
if [[ ! -w "${HOME}/homebrew/plugins" ]] || { [[ -e "$DEST" ]] && [[ ! -w "$DEST" ]]; }; then
  if have pkexec; then
    SUDO="pkexec"
  else
    need sudo
    SUDO="sudo"
  fi
fi

if [[ -L "$DEST" ]]; then
  die "$DEST is a symlink to $(readlink -f "$DEST")
Refusing to overwrite a live checkout. Update that tree instead."
fi

TMP="$(mktemp -d)"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

install_from_dir() {
  local src="$1"
  [[ -f "$src/plugin.json" ]] || die "no plugin.json in $src"
  [[ -f "$src/dist/index.js" ]] || die "no dist/index.js in $src"
  [[ -f "$src/main.py" ]] || die "no main.py in $src"
  [[ -f "$src/game_overlay/overlay.py" ]] || die "no game_overlay/overlay.py in $src"
  [[ -f "$src/bin/vtpip-run" ]] || die "no bin/vtpip-run in $src"
  $SUDO mkdir -p "$(dirname "$DEST")"
  $SUDO rm -rf "$DEST"
  $SUDO mkdir -p "$DEST"
  $SUDO cp -a "$src"/. "$DEST"/
  $SUDO rm -rf "$DEST/.git" "$DEST/__pycache__" "$DEST/game_overlay/__pycache__" \
    "$DEST/node_modules" "$DEST/.github"
  $SUDO rm -f "$DEST/VTPiP.zip" "$DEST/"*.zip 2>/dev/null || true
  $SUDO chmod a+x "$DEST/bin/vtpip-run" "$DEST/install.sh" 2>/dev/null || true
  $SUDO chown -R "$(id -u):$(id -g)" "$DEST" 2>/dev/null || true
}

download_release_zip() {
  local zip="$TMP/${PLUGIN}.zip"
  say "downloading latest release zip…"
  if curl -fL --retry 3 --retry-delay 1 -o "$zip" "$ZIP_URL" && [[ -s "$zip" ]]; then
    printf '%s\n' "$zip"
    return 0
  fi
  say "latest/download missed — asking the GitHub API…"
  local url
  url="$(curl -fsSL "$API_URL" | sed -n 's/.*"browser_download_url": *"\([^"]*VTPiP\.zip\)".*/\1/p' | head -n1 || true)"
  [[ -n "$url" ]] || return 1
  curl -fL --retry 3 --retry-delay 1 -o "$zip" "$url" || return 1
  [[ -s "$zip" ]] || return 1
  printf '%s\n' "$zip"
}

extract_zip() {
  local zip="$1"
  local out="$TMP/unpacked"
  mkdir -p "$out"
  unzip -q "$zip" -d "$out"
  if [[ -f "$out/plugin.json" ]]; then
    printf '%s\n' "$out"
    return 0
  fi
  local found
  found="$(find "$out" -name plugin.json -type f | head -n1 || true)"
  [[ -n "$found" ]] || die "zip did not contain plugin.json"
  dirname "$found"
}

download_tarball() {
  local url="$1"
  local tar="$TMP/src.tar.gz"
  say "downloading $url"
  curl -fL --retry 3 --retry-delay 1 -o "$tar" "$url" || return 1
  [[ -s "$tar" ]] || return 1
  local out="$TMP/tar"
  mkdir -p "$out"
  tar -xzf "$tar" -C "$out"
  local found
  found="$(find "$out" -name plugin.json -type f | head -n1 || true)"
  [[ -n "$found" ]] || return 1
  dirname "$found"
}

if [[ -n "$LOCAL" && -f "$LOCAL/dist/index.js" && "$FROM_RELEASE" != "1" ]]; then
  say "installing from local checkout $LOCAL"
  install_from_dir "$LOCAL"
else
  need curl
  ROOT=""
  if have unzip; then
    ZIP="$(download_release_zip || true)"
    if [[ -n "${ZIP:-}" && -s "$ZIP" ]]; then
      ROOT="$(extract_zip "$ZIP")"
    fi
  fi
  if [[ -z "$ROOT" ]]; then
    say "no release zip — falling back to the v${VERSION} source archive"
    ROOT="$(download_tarball "$TAG_TAR_URL" || true)"
  fi
  if [[ -z "$ROOT" ]]; then
    say "tag archive missed — falling back to main"
    ROOT="$(download_tarball "$MAIN_TAR_URL" || true)"
  fi
  [[ -n "$ROOT" ]] || die "could not download VTPiP from GitHub.
Create/wait for a release, or run this script from a checkout that has
plugin.json + dist/index.js + main.py."
  say "installing into $DEST"
  install_from_dir "$ROOT"
fi

if have systemctl; then
  if systemctl list-unit-files plugin_loader.service >/dev/null 2>&1 \
     || systemctl status plugin_loader.service >/dev/null 2>&1; then
    say "restarting Decky (plugin_loader)…"
    if [[ -n "$SUDO" ]]; then
      $SUDO systemctl restart plugin_loader.service \
        || say "could not restart plugin_loader — reload plugins from Decky, or reboot Steam"
    else
      systemctl restart plugin_loader.service 2>/dev/null \
        || sudo -n systemctl restart plugin_loader.service 2>/dev/null \
        || say "could not restart plugin_loader — reload plugins from Decky, or reboot Steam"
    fi
  else
    say "plugin_loader service not found — open Decky and reload plugins, or reboot Steam"
  fi
fi

vt_installed() {
  [[ -d "${HOME}/.local/share/flatpak/app/${FLATPAK_APP}" ]] && return 0
  [[ -d "/var/lib/flatpak/app/${FLATPAK_APP}" ]] && return 0
  have flatpak && flatpak info "$FLATPAK_APP" >/dev/null 2>&1 && return 0
  return 1
}

echo
if vt_installed; then
  say "VacuumTube is already installed."
elif [[ "$SKIP_VT" = "1" ]]; then
  say "skipping VacuumTube. Open VTPiP in the Quick Access Menu later and tap Install VacuumTube."
else
  say "VacuumTube (YouTube TV) was not found."
  say "VTPiP needs it. It is installed from Flathub with your Flatpak, not bundled here."
  if [[ -t 0 ]]; then
    printf "Install VacuumTube from Flathub now? [Y/n] "
    read -r ans || ans=""
    case "${ans:-Y}" in
      n|N|no|No) say "OK. Open VTPiP in the QAM later and tap Install VacuumTube." ;;
      *)
        if ! have flatpak; then
          die "flatpak is not on PATH. Install Flatpak in Desktop Mode, then retry."
        fi
        flatpak remote-add --user --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo || true
        flatpak install --user -y --noninteractive flathub "$FLATPAK_APP"
        ;;
    esac
  else
    if have flatpak; then
      say "no TTY — installing VacuumTube from Flathub (non-interactive)"
      flatpak remote-add --user --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo || true
      if ! flatpak install --user -y --noninteractive flathub "$FLATPAK_APP"; then
        say "VacuumTube install failed. Open VTPiP in the QAM and tap Install VacuumTube."
      fi
    else
      say "flatpak not on PATH. Open VTPiP in the QAM later — it will ask to install VacuumTube."
    fi
  fi
fi

echo
say "installed $PLUGIN ${VERSION} → $DEST"
say "next: Game Mode → Quick Access Menu (… ) → VTPiP → Start VacuumTube"
say "then launch a game and turn Show over game on."
