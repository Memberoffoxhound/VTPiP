import asyncio
import json
import os
import re
import shutil
import sys
from pathlib import Path
from subprocess import DEVNULL, PIPE

import decky
from decky import logger  # type: ignore

_HERE = Path(os.environ.get("DECKY_PLUGIN_DIR") or Path(__file__).resolve().parent)
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import vacuumtube as vt
import ytcdp


def _plugin_dir():
    return _HERE


def _settings_path():
    env = os.environ.get("DECKY_PLUGIN_SETTINGS_DIR")
    if env:
        p = Path(env)
    else:
        p = vt.home() / "homebrew" / "settings" / "VTPiP"
    p.mkdir(parents=True, exist_ok=True)
    return p / "settings.json"


def _state_dir():
    p = vt.home() / ".local" / "share" / "vtpip"
    p.mkdir(parents=True, exist_ok=True)
    return p


DEFAULTS = {
    "enabled": False,
    "corner": "bottom-right",
    "size": "small",
    "opacity": 90,
    "yt_volume": 100,
    "game_volume": 100,
    "muted": False,
    "shortcut_appid": None,
}

POSITIONS = (
    "top-left",
    "top-right",
    "bottom-left",
    "bottom-right",
    "left-middle",
    "right-middle",
)
POSITION_ALIASES = {
    "middle-left": "left-middle",
    "middle-right": "right-middle",
}


def sys_python():
    return shutil.which("python3") or shutil.which("python") or "/usr/bin/python3"


class Plugin:
    def __init__(self):
        self._overlay = None
        self._watch = None
        self._installing = False
        self._last_msg = ""
        self._focus_hide = False
        self._settings = None
        self._display_wh = (0, 0)

    def _load(self):
        data = dict(DEFAULTS)
        path = _settings_path()
        try:
            if path.is_file():
                data.update(json.loads(path.read_text()))
        except Exception as e:
            logger.warning("settings read: %s", e)
        self._settings = data
        return data

    def _save(self, data=None):
        data = data or self._load()
        self._settings = data
        try:
            _settings_path().write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.warning("settings write: %s", e)

    def _write_state(self, **extra):
        s = self._settings or self._load()
        overlay_display = extra.get("overlay_display")
        capture = None
        msg = extra.get("message")
        if s.get("enabled") and not self._focus_hide:
            capture = vt.find_capture_target(overlay_display or ":0")
            if not vt.installed():
                msg = msg or "Install VacuumTube from this menu"
            elif not vt.running():
                msg = msg or "Start VacuumTube from this menu"
            elif not capture:
                msg = msg or "Looking for the VacuumTube window…"
        corner = POSITION_ALIASES.get(s.get("corner"), s.get("corner")) or "bottom-right"
        if corner not in POSITIONS:
            corner = "bottom-right"
        payload = {
            "show": bool(s.get("enabled")) and not self._focus_hide,
            "corner": corner,
            "size": s.get("size") or "small",
            "opacity": int(s.get("opacity") or 90),
            "capture": capture,
            "message": msg or "",
        }
        path = _state_dir() / "state.json"
        tmp = _state_dir() / ".state.tmp"
        tmp.write_text(json.dumps(payload))
        os.replace(tmp, path)
        return payload

    def _overlay_script(self):
        d = _plugin_dir()
        for p in (d / "game_overlay" / "overlay.py", d / "defaults" / "game_overlay" / "overlay.py"):
            if p.is_file():
                return p
        return d / "game_overlay" / "overlay.py"

    def _overlay_alive(self):
        return self._overlay is not None and self._overlay.returncode is None

    async def _start_overlay(self):
        if self._overlay_alive():
            return True
        script = self._overlay_script()
        if not script.is_file():
            logger.error("overlay.py missing at %s", script)
            self._last_msg = "Overlay helper is missing"
            return False
        self._write_state()
        env = await vt.overlay_env()
        try:
            self._overlay = await asyncio.create_subprocess_exec(
                sys_python(), str(script), "--state-dir", str(_state_dir()),
                env=env, stdout=PIPE, stderr=PIPE,
            )
            asyncio.create_task(self._watch_stream(self._overlay.stdout, False))
            asyncio.create_task(self._watch_stream(self._overlay.stderr, True))
            await asyncio.sleep(0.8)
            if self._overlay is not None and self._overlay.returncode is not None:
                logger.warning("overlay exited immediately rc=%s", self._overlay.returncode)
                self._overlay = None
                self._last_msg = "Overlay failed to start (see plugin log)"
                return False
            logger.info("overlay started")
            return True
        except Exception as e:
            logger.warning("overlay start failed: %s", e)
            self._last_msg = str(e)
            return False

    async def _stop_overlay(self):
        proc = self._overlay
        self._overlay = None
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2)
            except Exception:
                proc.kill()
        except Exception:
            pass

    async def _watch_stream(self, stream, is_err):
        if stream is None:
            return
        async for line in stream:
            text = line.decode("utf-8", "replace").rstrip()
            if not text:
                continue
            if is_err:
                logger.warning("[overlay] %s", text)
            else:
                logger.info("[overlay] %s", text)

    async def _loop(self):
        while True:
            try:
                s = self._settings or self._load()
                if s.get("enabled"):
                    if not self._overlay_alive():
                        await self._start_overlay()
                    env = await vt.overlay_env()
                    self._write_state(overlay_display=env.get("DISPLAY", ":0"))
                    try:
                        await vt.apply_volume(
                            s.get("yt_volume") or 100,
                            s.get("game_volume") or 100,
                            bool(s.get("muted")),
                        )
                    except Exception as e:
                        logger.debug("volume: %s", e)
                else:
                    if self._overlay_alive():
                        self._write_state()
                        await self._stop_overlay()
            except Exception as e:
                logger.warning("watch loop: %s", e)
            await asyncio.sleep(2)

    async def _main(self):
        logger.info("VTPiP backend started")
        self._load()
        self._watch = asyncio.create_task(self._loop())

    async def _unload(self):
        if self._watch is not None:
            self._watch.cancel()
            self._watch = None
        await self._stop_overlay()
        try:
            ytcdp.session().close()
        except Exception:
            pass

    def _overlay_display_size(self):
        try:
            path = _state_dir() / "display.json"
            if path.is_file():
                data = json.loads(path.read_text())
                w, h = int(data.get("width") or 0), int(data.get("height") or 0)
                if w >= 320 and h >= 240:
                    return w, h
        except Exception:
            pass
        return 0, 0

    async def _probe_display_size(self):
        env = await vt.overlay_env()
        try:
            proc = await asyncio.create_subprocess_exec(
                "xrandr", "--current",
                stdout=PIPE, stderr=DEVNULL, env=env,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), 2)
            text = out.decode("utf-8", "replace")
            m = re.search(r"current\s+(\d+)\s+x\s+(\d+)", text)
            if m:
                return int(m.group(1)), int(m.group(2))
            m = re.search(r" connected(?: primary)? (\d+)x(\d+)", text)
            if m:
                return int(m.group(1)), int(m.group(2))
        except Exception as e:
            logger.warning("display size: %s", e)
        return 1280, 800

    async def _display_size(self):
        w, h = self._overlay_display_size()
        if w:
            self._display_wh = (w, h)
            return w, h
        if self._display_wh[0] >= 320:
            return self._display_wh
        w, h = await self._probe_display_size()
        if w >= 320 and h >= 240:
            self._display_wh = (w, h)
            return w, h
        return 1280, 800

    async def status(self):
        s = self._load()
        stored = s.get("shortcut_appid")
        found = vt.shortcut_appid_from_vdf()
        if found and found is not True and not stored:
            s["shortcut_appid"] = found
            self._save(s)
            stored = found
        dw, dh = await self._display_size()
        corner = POSITION_ALIASES.get(s.get("corner"), s.get("corner")) or "bottom-right"
        if corner not in POSITIONS:
            corner = "bottom-right"
        return {
            "ok": True,
            "installed": vt.installed(),
            "installing": self._installing,
            "running": vt.running(),
            "overlay": self._overlay_alive(),
            "flatpak": bool(vt.flatpak_bin()),
            "enabled": bool(s.get("enabled")),
            "corner": corner,
            "size": s.get("size"),
            "opacity": int(s.get("opacity") or 90),
            "display_width": dw,
            "display_height": dh,
            "yt_volume": int(s.get("yt_volume") or 100),
            "game_volume": int(s.get("game_volume") or 100),
            "muted": bool(s.get("muted")),
            "shortcut_appid": stored,
            "wrapper": str(_plugin_dir() / "bin" / "vtpip-run"),
            "icon": str(_plugin_dir() / "assets" / "icon.png"),
            "message": self._last_msg,
            "focus_hide": self._focus_hide,
        }

    async def install_vacuumtube(self):
        if self._installing:
            return {"ok": False, "error": "Install already running"}
        self._installing = True
        self._last_msg = "Installing VacuumTube…"
        try:
            r = await vt.install()
            if r.get("ok"):
                self._last_msg = "VacuumTube installed"
            else:
                self._last_msg = r.get("error") or "Install failed"
            return r
        finally:
            self._installing = False

    async def set_enabled(self, enabled):
        s = self._load()
        s["enabled"] = bool(enabled)
        self._save(s)
        if enabled:
            vt.disable_pause_on_blur()
            ok = await self._start_overlay()
            env = await vt.overlay_env()
            self._write_state(overlay_display=env.get("DISPLAY", ":0"))
            if not ok:
                return {"ok": False, "error": self._last_msg or "overlay failed"}
        else:
            self._write_state()
            await self._stop_overlay()
        return {"ok": True}

    async def set_layout(self, corner=None, size=None, opacity=None):
        s = self._load()
        if corner is not None:
            corner = POSITION_ALIASES.get(corner, corner)
            if corner in POSITIONS:
                s["corner"] = corner
        if size in ("small", "large"):
            s["size"] = size
        if opacity is not None:
            s["opacity"] = max(10, min(100, int(opacity)))
        self._save(s)
        env = await vt.overlay_env()
        self._write_state(overlay_display=env.get("DISPLAY", ":0"))
        return {"ok": True, "corner": s["corner"], "size": s["size"], "opacity": s["opacity"]}

    async def set_audio(self, yt_volume=None, game_volume=None, muted=None):
        s = self._load()
        if yt_volume is not None:
            s["yt_volume"] = max(0, min(150, int(yt_volume)))
        if game_volume is not None:
            s["game_volume"] = max(0, min(150, int(game_volume)))
        if muted is not None:
            s["muted"] = bool(muted)
        self._save(s)
        try:
            await vt.apply_volume(s["yt_volume"], s["game_volume"], s["muted"])
        except Exception as e:
            logger.warning("apply_volume: %s", e)
        return {
            "ok": True,
            "yt_volume": s["yt_volume"],
            "game_volume": s["game_volume"],
            "muted": s["muted"],
        }

    async def set_shortcut_appid(self, appid):
        s = self._load()
        try:
            s["shortcut_appid"] = int(appid) if appid is not None else None
        except Exception:
            s["shortcut_appid"] = appid
        self._save(s)
        return {"ok": True, "shortcut_appid": s["shortcut_appid"]}

    async def set_focus(self, hide, name="", appid=0):
        self._focus_hide = bool(hide)
        env = await vt.overlay_env()
        self._write_state(overlay_display=env.get("DISPLAY", ":0"))
        return {"ok": True, "hide": self._focus_hide, "name": name, "appid": appid}

    async def wrapper_info(self):
        d = _plugin_dir()
        return {
            "wrapper": str(d / "bin" / "vtpip-run"),
            "startdir": str(d / "bin"),
            "icon": str(d / "assets" / "icon.png"),
        }

    def _yt_err(self, e):
        msg = str(e).strip() or "VacuumTube is not ready"
        logger.warning("[vtpip] yt: %s", msg)
        return {"ok": False, "error": msg}

    async def player_state(self):
        if not vt.running():
            return {"ok": False, "running": False, "hasVideo": False}
        try:
            st = await asyncio.to_thread(ytcdp.session().player)
            st["running"] = True
            return st
        except Exception as e:
            return self._yt_err(e)

    async def play_pause(self):
        try:
            return await asyncio.to_thread(ytcdp.session().play_pause)
        except Exception as e:
            return self._yt_err(e)

    async def skip(self, seconds):
        try:
            return await asyncio.to_thread(ytcdp.session().skip, float(seconds))
        except Exception as e:
            return self._yt_err(e)

    async def next_video(self):
        try:
            return await asyncio.to_thread(ytcdp.session().next_video)
        except Exception as e:
            return self._yt_err(e)

    async def prev_video(self):
        try:
            return await asyncio.to_thread(ytcdp.session().prev_video)
        except Exception as e:
            return self._yt_err(e)

    async def play_video(self, video_id, items=None, index=None):
        logger.info("[vtpip] play_video %s", video_id)
        try:
            r = await asyncio.to_thread(
                ytcdp.session().play_id, video_id, items, index,
            )
            logger.info("[vtpip] play_video result %s", r)
            return r
        except Exception as e:
            return self._yt_err(e)

    async def watch_later(self):
        try:
            return await asyncio.to_thread(ytcdp.session().watch_later)
        except Exception as e:
            return self._yt_err(e)

    async def search(self, query):
        try:
            return await asyncio.to_thread(ytcdp.session().search, query or "")
        except Exception as e:
            return self._yt_err(e)

    async def comments(self, video_id=None):
        try:
            return await asyncio.to_thread(ytcdp.session().comments, video_id)
        except Exception as e:
            return self._yt_err(e)
