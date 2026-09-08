"""VacuumTube install, process lookup, window capture target, and audio."""
import json
import os
import re
import shutil
import struct
from pathlib import Path
from subprocess import DEVNULL, PIPE

from asyncio import create_subprocess_exec, wait_for, sleep

from decky import logger  # type: ignore

FLATPAK_APP = "rocks.shy.VacuumTube"
CDP_PORT = 9224
CONFIG_REL = Path(".var/app") / FLATPAK_APP / "config" / "VacuumTube" / "config.json"

_EXTRA_PATH = (
    "/run/wrappers/bin",
    "/run/current-system/sw/bin",
    "~/.nix-profile/bin",
    "/nix/var/nix/profiles/default/bin",
    "/var/guix/profiles/system/profile/bin",
    "~/.guix-profile/bin",
    "~/.local/bin",
    "/usr/local/bin", "/usr/bin", "/bin",
    "/usr/local/sbin", "/usr/sbin", "/sbin",
)


def _ensure_path():
    try:
        cur = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d]
        seen = set(cur)
        extra = []
        for d in _EXTRA_PATH:
            d = os.path.expanduser(d)
            if d not in seen and os.path.isdir(d):
                seen.add(d)
                extra.append(d)
        if extra:
            os.environ["PATH"] = os.pathsep.join(cur + extra)
    except Exception:
        pass


_ensure_path()


def user_env():
    """Graphical env for a process spawned by plugin_loader (systemd/root)."""
    uid = os.getuid()
    rt = f"/run/user/{uid}"
    env = {
        **os.environ,
        "XDG_RUNTIME_DIR": rt,
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path={rt}/bus",
        "HOME": str(Path.home()),
    }
    orig = env.pop("LD_LIBRARY_PATH_ORIG", None)
    if orig is not None:
        env["LD_LIBRARY_PATH"] = orig
    else:
        env.pop("LD_LIBRARY_PATH", None)
    env.pop("LD_PRELOAD", None)
    if "/tmp/_MEI" in env.get("LD_LIBRARY_PATH", ""):
        env.pop("LD_LIBRARY_PATH", None)
    return env


async def show_session_env():
    try:
        proc = await create_subprocess_exec(
            "systemctl", "--user", "show-environment",
            stdout=PIPE, stderr=DEVNULL, env=user_env(),
        )
        out, _ = await proc.communicate()
        env = {}
        for line in out.decode().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                env[k] = v
        return env
    except Exception:
        return {}


async def overlay_env():
    env = dict(user_env())
    try:
        env.update(await show_session_env())
    except Exception:
        pass
    env.setdefault("DISPLAY", ":0")
    env.setdefault("GDK_BACKEND", "x11")
    if "/tmp/_MEI" in env.get("LD_LIBRARY_PATH", ""):
        env.pop("LD_LIBRARY_PATH", None)
    return env


def home():
    return Path(os.environ.get("DECKY_USER_HOME") or Path.home())


def flatpak_bin():
    return shutil.which("flatpak")


def installed():
    """True if VacuumTube is present as a user/system Flatpak (or a PATH binary)."""
    h = home()
    if (h / ".local/share/flatpak/app" / FLATPAK_APP).is_dir():
        return True
    if Path("/var/lib/flatpak/app").joinpath(FLATPAK_APP).is_dir():
        return True
    desktop = h / ".local/share/flatpak/exports/share/applications" / (FLATPAK_APP + ".desktop")
    if desktop.is_file():
        return True
    if shutil.which("vacuumtube") or shutil.which("VacuumTube"):
        return True
    fp = flatpak_bin()
    if fp:
        try:
            r = __import__("subprocess").run(
                [fp, "info", FLATPAK_APP],
                stdout=DEVNULL, stderr=DEVNULL, timeout=5,
                env=user_env(), check=False,
            )
            if r.returncode == 0:
                return True
        except Exception:
            pass
    return False


def install_scope():
    if (home() / ".local/share/flatpak/app" / FLATPAK_APP).is_dir():
        return "user"
    if Path("/var/lib/flatpak/app").joinpath(FLATPAK_APP).is_dir():
        return "system"
    return None


async def install():
    fp = flatpak_bin()
    if not fp:
        return {"ok": False, "error": "flatpak is not installed on this system"}
    env = user_env()
    r = await create_subprocess_exec(
        fp, "remote-add", "--user", "--if-not-exists", "flathub",
        "https://flathub.org/repo/flathub.flatpakrepo",
        stdout=PIPE, stderr=PIPE, env=env,
    )
    await r.wait()
    proc = await create_subprocess_exec(
        fp, "install", "--user", "-y", "--noninteractive", "flathub", FLATPAK_APP,
        stdout=PIPE, stderr=PIPE, env=env,
    )
    try:
        out, err = await wait_for(proc.communicate(), timeout=600)
    except Exception:
        proc.kill()
        return {"ok": False, "error": "VacuumTube install timed out"}
    if proc.returncode != 0 and not installed():
        msg = (err or out).decode("utf-8", "replace")[-400:]
        return {"ok": False, "error": msg.strip() or "flatpak install failed"}
    return {"ok": True, "installed": installed()}


def proc_pids(cmdline=None, comm=None):
    comm_rx = re.compile(comm, re.I) if comm else None
    cmd_rx = re.compile(cmdline, re.I) if cmdline else None
    me = os.getpid()
    out = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return out
    for name in entries:
        if not name.isdigit():
            continue
        pid = int(name)
        if pid == me:
            continue
        try:
            if comm_rx is not None:
                with open(f"/proc/{pid}/comm") as f:
                    if not comm_rx.search(f.read().strip()):
                        continue
            if cmd_rx is not None:
                with open(f"/proc/{pid}/cmdline", "rb") as f:
                    argv = f.read().replace(b"\0", b" ").decode("utf-8", "replace")
                if not cmd_rx.search(argv):
                    continue
        except OSError:
            continue
        out.append(pid)
    return out


def running_pids():
    pids = proc_pids(cmdline=r"rocks\.shy\.VacuumTube|VacuumTube")
    # Do not match the overlay or this plugin.
    keep = []
    for pid in pids:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                argv = f.read().replace(b"\0", b" ").decode("utf-8", "replace")
            low = argv.lower()
            if "overlay.py" in low:
                continue
            if "vtpip-run" in low and "rocks.shy.vacuumtube" not in low:
                continue
        except OSError:
            continue
        keep.append(pid)
    return keep


def running():
    return bool(running_pids())


def read_environ(pid):
    env = {}
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return env
    for item in raw.split(b"\0"):
        if not item or b"=" not in item:
            continue
        k, v = item.split(b"=", 1)
        try:
            env[k.decode()] = v.decode("utf-8", "replace")
        except Exception:
            pass
    return env


def _host_xauth(path):
    """Flatpak sets XAUTHORITY to a sandbox path the host cannot read."""
    if path and os.path.isfile(path):
        return path
    return ""


def find_capture_target(overlay_display=":0"):
    """Pick VacuumTube's X window (and CDP port) for the overlay to copy.

    Never capture a gamescope Xwayland *root*: GPU clients are not in that
    pixmap, so ximagesrc of :1 comes back black. Always prefer the real
    VacuumTube xid; CDP (port 9224) is a second path that keeps working when
    the window is unfocused.
    """
    pids = running_pids()
    if not pids:
        return None
    overlay_display = (overlay_display or ":0").split(".")[0]
    best = None
    for pid in pids:
        env = read_environ(pid)
        display = env.get("DISPLAY") or ""
        if not display:
            continue
        disp = display.split(".")[0]
        xauth = _host_xauth(env.get("XAUTHORITY") or "")
        xid = _find_xid(disp, xauth)
        info = {
            "display": disp,
            "xauth": xauth,
            "xid": xid,
            "pid": pid,
            "cdp": CDP_PORT,
        }
        if xid:
            return info
        if disp != overlay_display:
            # Other Xwayland: CDP can still grab the page without an xid.
            best = info
        elif best is None:
            best = info
    if best is None:
        return None
    if not best.get("xid") and best.get("display") == overlay_display:
        # Same X display as the overlay — capturing the whole screen would
        # grab the game (or a hall of mirrors). Wait until we have a window id.
        # CDP is still usable; keep the target so the overlay can attach.
        pass
    return best


def _find_xid(display, xauth):
    """Return the largest VacuumTube window id on *display* (hex string)."""
    env = os.environ.copy()
    env["DISPLAY"] = display
    if xauth:
        env["XAUTHORITY"] = xauth
    else:
        env.pop("XAUTHORITY", None)
    best_id, best_area = None, 0
    xw = shutil.which("xwininfo")
    if xw:
        try:
            p = subprocess_run([xw, "-root", "-tree"], env=env)
            for line in (p or "").splitlines():
                if "vacuumtube" not in line.lower():
                    continue
                m = re.search(
                    r"(0x[0-9a-fA-F]+).+?\s+(\d+)x(\d+)\+", line,
                )
                if not m:
                    continue
                area = int(m.group(2)) * int(m.group(3))
                if area < 100 * 100:
                    continue
                if area > best_area:
                    best_area, best_id = area, m.group(1)
        except Exception:
            pass
    if best_id:
        return best_id
    xd = shutil.which("xdotool")
    if xd:
        try:
            p = subprocess_run(
                [xd, "search", "--name", "VacuumTube"], env=env,
            )
            ids = [x for x in (p or "").split() if x]
            # Prefer the last id: Chromium's tiny helper window is usually first.
            if ids:
                raw = ids[-1]
                return hex(int(raw)) if raw.isdigit() else raw
        except Exception:
            pass
    return None


def subprocess_run(cmd, env=None):
    try:
        r = __import__("subprocess").run(
            cmd, env=env, stdout=PIPE, stderr=DEVNULL, timeout=3, check=False,
        )
        return r.stdout.decode("utf-8", "replace")
    except Exception:
        return ""


def disable_pause_on_blur():
    path = home() / CONFIG_REL
    try:
        if not path.is_file():
            return False
        data = json.loads(path.read_text())
        if data.get("pause_on_blur"):
            data["pause_on_blur"] = False
            path.write_text(json.dumps(data, indent=4))
            logger.info("[vtpip] pause_on_blur disabled so PiP keeps playing")
            return True
    except Exception as e:
        logger.warning("[vtpip] config patch failed: %s", e)
    return False


# ── Pulse/PipeWire volume ──────────────────────────────────────────────────

_YT_MATCH = re.compile(r"vacuumtube|rocks\.shy\.vacuumtube", re.I)
_SKIP_GAME = re.compile(
    r"vacuumtube|vesktop|discord|steam|pipewire|speech-dispatcher|chrome.?xdg",
    re.I,
)


async def _pactl(*args, timeout=5):
    env = user_env()
    proc = await create_subprocess_exec(
        "pactl", *args, stdout=PIPE, stderr=DEVNULL, env=env,
    )
    try:
        out, _ = await wait_for(proc.communicate(), timeout=timeout)
    except Exception:
        proc.kill()
        return ""
    return out.decode("utf-8", "replace")


async def _sink_inputs():
    raw = await _pactl("--format=json", "list", "sink-inputs")
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _props(item):
    return item.get("properties") or item.get("property_list") or {}


def _is_yt(item):
    p = _props(item)
    blob = " ".join(str(v) for v in (
        p.get("application.name"),
        p.get("application.process.binary"),
        p.get("application.process.host-binary"),
        p.get("pipewire.access.portal.app_id"),
        p.get("media.name"),
        p.get("application.id"),
    ) if v)
    return bool(_YT_MATCH.search(blob))


def _is_game_candidate(item):
    p = _props(item)
    blob = " ".join(str(v) for v in (
        p.get("application.name"),
        p.get("application.process.binary"),
        p.get("media.name"),
        p.get("application.id"),
    ) if v)
    return not bool(_SKIP_GAME.search(blob or " "))


def _index(item):
    for k in ("index", "SinkInput"):
        if k in item:
            try:
                return int(item[k])
            except Exception:
                pass
    return None


async def apply_volume(yt_pct, game_pct, muted):
    yt_pct = max(0, min(150, int(yt_pct)))
    game_pct = max(0, min(150, int(game_pct)))
    inputs = await _sink_inputs()
    for item in inputs:
        idx = _index(item)
        if idx is None:
            continue
        if _is_yt(item):
            await _pactl("set-sink-input-volume", str(idx), f"{yt_pct}%")
            await _pactl("set-sink-input-mute", str(idx), "1" if muted else "0")
        elif _is_game_candidate(item):
            await _pactl("set-sink-input-volume", str(idx), f"{game_pct}%")
    return True


# ── shortcuts.vdf (detect an existing VacuumTube shortcut) ───────────────

def _read_cstring(buf, i):
    j = buf.index(b"\x00", i)
    return buf[i:j].decode("utf-8", "replace"), j + 1


def parse_binary_vdf(buf, i=0):
    obj = {}
    n = len(buf)
    while i < n:
        t = buf[i]
        i += 1
        if t == 8:
            return obj, i
        if t == 0:
            name, i = _read_cstring(buf, i)
            child, i = parse_binary_vdf(buf, i)
            obj[name] = child
        elif t == 1:
            name, i = _read_cstring(buf, i)
            val, i = _read_cstring(buf, i)
            obj[name] = val
        elif t == 2:
            name, i = _read_cstring(buf, i)
            obj[name] = struct.unpack_from("<i", buf, i)[0]
            i += 4
        elif t == 3:
            name, i = _read_cstring(buf, i)
            obj[name] = struct.unpack_from("<f", buf, i)[0]
            i += 4
        elif t == 7:
            name, i = _read_cstring(buf, i)
            obj[name] = struct.unpack_from("<Q", buf, i)[0]
            i += 8
        else:
            break
    return obj, i


def _steam_roots():
    h = home()
    return [h / ".steam/steam", h / ".local/share/Steam", h / ".steam/root"]


def userdata_config_dirs():
    dirs = []
    for root in _steam_roots():
        ud = root / "userdata"
        if not ud.is_dir():
            continue
        for child in ud.iterdir():
            cfg = child / "config"
            if cfg.is_dir():
                dirs.append(cfg)
    return dirs


def shortcut_appid_from_vdf(wrapper_hint="vtpip-run"):
    """Return the unsigned Steam shortcut appid if we already added one."""
    for cfg in userdata_config_dirs():
        path = cfg / "shortcuts.vdf"
        if not path.is_file():
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        try:
            data, _ = parse_binary_vdf(raw, 0)
        except Exception:
            data = None
        shortcuts = None
        if isinstance(data, dict):
            shortcuts = data.get("shortcuts") or data
        if isinstance(shortcuts, dict):
            for sc in shortcuts.values():
                if not isinstance(sc, dict):
                    continue
                exe = str(sc.get("Exe") or sc.get("exe") or "")
                name = str(sc.get("AppName") or sc.get("appname") or "")
                if wrapper_hint in exe or (name == "VacuumTube" and "flatpak" in exe.lower()):
                    appid = sc.get("appid")
                    if appid is not None:
                        return int(appid) & 0xFFFFFFFF
        # Fallback: raw search if parse failed
        if wrapper_hint.encode() in raw:
            return True
    return None
