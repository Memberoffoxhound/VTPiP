#!/usr/bin/env python3
"""VTPiP overlay window.

Fullscreen transparent X11 window tagged GAMESCOPE_EXTERNAL_OVERLAY (same
mangoapp / GameModeCord recipe) so gamescope paints it over the running game.
VacuumTube is copied via Chrome DevTools screencast (port 9224). X11 capture
of a GPU Chromium window under gamescope comes back tiled / slanted /
interlaced (especially on 4K Steam Machines), so it is only a fallback.
Never capture the gamescope Xwayland root — that pixmap is black.
"""
import argparse
import base64
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.request

os.environ["GDK_BACKEND"] = "x11"

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib, GdkPixbuf

try:
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
    Gst.init(None)
    HAVE_GST = True
except Exception:
    HAVE_GST = False

HAVE_GST_VIDEO = False
GstVideo = None
if HAVE_GST:
    try:
        gi.require_version("GstVideo", "1.0")
        from gi.repository import GstVideo as _GstVideo
        GstVideo = _GstVideo
        HAVE_GST_VIDEO = True
    except Exception:
        pass

try:
    import cairo
    HAVE_CAIRO = True
except Exception:
    HAVE_CAIRO = False


SIZES = {
    "small": (854, 480),
    "large": (1280, 720),
}
DEFAULT_STATE = {
    "show": False,
    "corner": "bottom-right",
    "size": "small",
    "opacity": 90,
    "capture": None,
    "message": "",
}


def pip_rect(sw, sh, size, corner):
    tw, th = SIZES.get(size, SIZES["small"])
    margin = max(12, int(min(sw, sh) * 0.015))
    scale = min(1.0, (sw - 2 * margin) / float(tw), (sh - 2 * margin) / float(th))
    w, h = max(160, int(tw * scale)), max(90, int(th * scale))
    mid_y = max(margin, (sh - h) // 2)
    if corner in ("left-middle", "middle-left"):
        return margin, mid_y, w, h
    if corner in ("right-middle", "middle-right"):
        return sw - w - margin, mid_y, w, h
    if corner == "top-left":
        return margin, margin, w, h
    if corner == "top-right":
        return sw - w - margin, margin, w, h
    if corner == "bottom-left":
        return margin, sh - h - margin, w, h
    return sw - w - margin, sh - h - margin, w, h


def _set_atoms_ctypes(xid):
    from ctypes import POINTER, c_char_p, c_int, c_ulong, c_void_p, cdll
    x = cdll.LoadLibrary("libX11.so.6")
    x.XOpenDisplay.argtypes = [c_char_p]
    x.XOpenDisplay.restype = c_void_p
    x.XInternAtom.argtypes = [c_void_p, c_char_p, c_int]
    x.XInternAtom.restype = c_ulong
    x.XChangeProperty.argtypes = [
        c_void_p, c_ulong, c_ulong, c_ulong, c_int, c_int, POINTER(c_ulong), c_int
    ]
    x.XSync.argtypes = [c_void_p, c_int]
    x.XCloseDisplay.argtypes = [c_void_p]
    d = x.XOpenDisplay(None)
    if not d:
        raise RuntimeError("XOpenDisplay failed")
    try:
        XA_ATOM, XA_CARDINAL, REPLACE = 4, 6, 0
        one = (c_ulong * 1)(1)
        x.XChangeProperty(
            d, xid, x.XInternAtom(d, b"GAMESCOPE_EXTERNAL_OVERLAY", False),
            XA_CARDINAL, 32, REPLACE, one, 1,
        )
        types = (c_ulong * 2)(
            x.XInternAtom(d, b"_KDE_NET_WM_WINDOW_TYPE_ON_SCREEN_DISPLAY", False),
            x.XInternAtom(d, b"_NET_WM_WINDOW_TYPE_NOTIFICATION", False),
        )
        x.XChangeProperty(
            d, xid, x.XInternAtom(d, b"_NET_WM_WINDOW_TYPE", False),
            XA_ATOM, 32, REPLACE, types, 2,
        )
        x.XSync(d, False)
    finally:
        x.XCloseDisplay(d)


def _set_atoms_xlib(xid):
    from Xlib import Xatom, display
    d = display.Display()
    w = d.create_resource_object("window", xid)
    w.change_property(d.intern_atom("GAMESCOPE_EXTERNAL_OVERLAY"), Xatom.CARDINAL, 32, [1])
    w.change_property(
        d.intern_atom("_NET_WM_WINDOW_TYPE"), Xatom.ATOM, 32,
        [d.intern_atom("_KDE_NET_WM_WINDOW_TYPE_ON_SCREEN_DISPLAY"),
         d.intern_atom("_NET_WM_WINDOW_TYPE_NOTIFICATION")],
    )
    d.sync()
    d.close()


def set_overlay_atom(xid):
    for name, fn in (("xlib", _set_atoms_xlib), ("ctypes", _set_atoms_ctypes)):
        try:
            fn(xid)
            return name
        except Exception as e:
            print("[overlay] atoms via %s failed: %s" % (name, e), flush=True)
    return False


def load_state(path):
    try:
        with open(path, "r") as f:
            data = json.load(f)
        if isinstance(data, dict):
            out = dict(DEFAULT_STATE)
            out.update(data)
            return out
    except Exception:
        pass
    return dict(DEFAULT_STATE)


def pixbuf_from_jpeg(data):
    loader = GdkPixbuf.PixbufLoader.new_with_type("jpeg")
    try:
        loader.write(data)
        loader.close()
        return loader.get_pixbuf()
    except Exception:
        try:
            loader.close()
        except Exception:
            pass
        return None


def _pixbuf_from_gst_sample(sample):
    """RGB GdkPixbuf from an appsink sample, using the real row stride.

    GStreamer pads RGB rows to 4 bytes. GdkPixbuf.new_from_data(..., w*3)
    then shears the frame into the classic interlaced / slanted / double
    image — 854x480 (small PiP) is not 4-byte aligned. Pack to tight RGB.
    """
    buf = sample.get_buffer()
    caps = sample.get_caps()
    if buf is None or caps is None:
        return None
    st = caps.get_structure(0)
    w = int(st.get_value("width") or 0)
    h = int(st.get_value("height") or 0)
    fmt = str(st.get_value("format") or "RGB")
    if w <= 0 or h <= 0:
        return None
    ok, info = buf.map(Gst.MapFlags.READ)
    if not ok:
        return None
    try:
        stride = 0
        if HAVE_GST_VIDEO:
            try:
                meta = GstVideo.buffer_get_video_meta(buf)
                if meta is not None:
                    stride = int(meta.stride[0])
            except Exception:
                stride = 0
        mv = info.data
        size = int(info.size)
        if stride <= 0 and h > 0:
            stride = size // h
        src_bpp = 4 if fmt in ("RGBx", "BGRx", "RGBA", "BGRA", "xRGB", "xBGR") else 3
        if stride < w * src_bpp:
            stride = w * src_bpp
        tight = w * 3
        bgr = fmt.startswith("BGR") or fmt in ("xBGR",)
        if fmt == "RGB" and stride == tight:
            packed = bytes(mv[: h * stride])
            pb = GdkPixbuf.Pixbuf.new_from_data(
                packed, GdkPixbuf.Colorspace.RGB, False, 8, w, h, tight,
            )
            return pb.copy()
        out = bytearray(h * tight)
        for y in range(h):
            row = mv[y * stride: y * stride + w * src_bpp]
            dest = y * tight
            if src_bpp == 3 and not bgr:
                out[dest: dest + tight] = row[:tight]
                continue
            for x in range(w):
                i = x * src_bpp
                o = dest + x * 3
                if bgr:
                    out[o] = row[i + 2]
                    out[o + 1] = row[i + 1]
                    out[o + 2] = row[i]
                else:
                    out[o] = row[i]
                    out[o + 1] = row[i + 1]
                    out[o + 2] = row[i + 2]
        packed = bytes(out)
        pb = GdkPixbuf.Pixbuf.new_from_data(
            packed, GdkPixbuf.Colorspace.RGB, False, 8, w, h, tight,
        )
        return pb.copy()
    except Exception as e:
        print("[overlay] gst frame: %s" % e, flush=True)
        return None
    finally:
        buf.unmap(info)


def _find_xid_on_display(display, xauth=""):
    """Largest VacuumTube window on *display*. Host-side lookup (no sandbox xauth)."""
    env = os.environ.copy()
    env["DISPLAY"] = display
    if xauth and os.path.isfile(xauth):
        env["XAUTHORITY"] = xauth
    else:
        env.pop("XAUTHORITY", None)
    xw = shutil.which("xwininfo")
    if not xw:
        return None
    try:
        r = subprocess.run(
            [xw, "-root", "-tree"], env=env, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=3, check=False,
        )
        text = r.stdout.decode("utf-8", "replace")
    except Exception:
        return None
    best_id, best_area = None, 0
    for line in text.splitlines():
        if "vacuumtube" not in line.lower():
            continue
        m = re.search(r"(0x[0-9a-fA-F]+).+?\s+(\d+)x(\d+)\+", line)
        if not m:
            continue
        area = int(m.group(2)) * int(m.group(3))
        if area < 100 * 100:
            continue
        if area > best_area:
            best_area, best_id = area, m.group(1)
    return best_id


class MiniWS:
    """Tiny RFC6455 client (text frames) for Chrome DevTools Protocol."""

    def __init__(self, url):
        self.url = url
        self.sock = None
        self._buf = b""

    def connect(self):
        # urlparse is stdlib; imported lazily to keep the overlay's top small.
        from urllib.parse import urlparse
        u = urlparse(self.url)
        host = u.hostname or "127.0.0.1"
        port = u.port or 80
        path = u.path or "/"
        if u.query:
            path += "?" + u.query
        sock = socket.create_connection((host, port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        req = (
            "GET %s HTTP/1.1\r\n"
            "Host: %s:%d\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: %s\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n" % (path, host, port, key)
        )
        sock.sendall(req.encode("ascii"))
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                sock.close()
                raise RuntimeError("CDP handshake closed")
            buf += chunk
        header, rest = buf.split(b"\r\n\r\n", 1)
        status = header.split(b"\r\n", 1)[0]
        if b"101" not in status:
            sock.close()
            raise RuntimeError("CDP handshake failed: %s" % status.decode("utf-8", "replace"))
        self.sock = sock
        self._buf = rest

    def close(self):
        sock = self.sock
        self.sock = None
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass

    def send_json(self, obj):
        payload = json.dumps(obj).encode("utf-8")
        mask = os.urandom(4)
        header = bytearray()
        header.append(0x81)
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack(">H", n))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack(">Q", n))
        header.extend(mask)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(header + masked)

    def recv_json(self, timeout=2.0):
        if self.sock is None:
            return None
        self.sock.settimeout(timeout)
        while True:
            opcode, payload = self._recv_frame()
            if opcode == 0x8:
                return None
            if opcode == 0x9:
                self._send_raw(payload, 0xA)
                continue
            if opcode in (0x1, 0x2):
                return json.loads(payload.decode("utf-8"))

    def _send_raw(self, payload, opcode):
        mask = os.urandom(4)
        header = bytearray([0x80 | opcode, 0x80 | len(payload)])
        if len(payload) >= 126:
            return
        header.extend(mask)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(header + masked)

    def _recv_more(self):
        if self.sock is None:
            raise RuntimeError("CDP closed")
        chunk = self.sock.recv(65536)
        if not chunk:
            raise RuntimeError("CDP closed")
        self._buf += chunk

    def _recv_frame(self):
        while len(self._buf) < 2:
            self._recv_more()
        b0, b1 = self._buf[0], self._buf[1]
        opcode = b0 & 0x0F
        masked = bool(b1 & 0x80)
        n = b1 & 0x7F
        i = 2
        if n == 126:
            while len(self._buf) < 4:
                self._recv_more()
            n = struct.unpack(">H", self._buf[2:4])[0]
            i = 4
        elif n == 127:
            while len(self._buf) < 10:
                self._recv_more()
            n = struct.unpack(">Q", self._buf[2:10])[0]
            i = 10
        if masked:
            while len(self._buf) < i + 4:
                self._recv_more()
            mask = self._buf[i:i + 4]
            i += 4
        else:
            mask = None
        while len(self._buf) < i + n:
            self._recv_more()
        payload = self._buf[i:i + n]
        self._buf = self._buf[i + n:]
        if mask is not None:
            payload = bytes(b ^ mask[j % 4] for j, b in enumerate(payload))
        return opcode, payload


class Capture:
    """Pull frames from VacuumTube: CDP screencast first, then X11 xid."""

    def __init__(self, on_frame):
        self.on_frame = on_frame
        self._lock = threading.Lock()
        self._pipe = None
        self._ff = None
        self._cdp = None
        self._cdp_thread = None
        self._stop = threading.Event()
        self._key = None

    def stop(self):
        self._stop.set()
        with self._lock:
            self._stop_locked()
        self._stop.clear()

    def _stop_locked(self):
        if self._pipe is not None:
            try:
                self._pipe.set_state(Gst.State.NULL)
            except Exception:
                pass
            self._pipe = None
        if self._ff is not None:
            try:
                self._ff.kill()
            except Exception:
                pass
            self._ff = None
        if self._cdp is not None:
            try:
                self._cdp.close()
            except Exception:
                pass
            self._cdp = None
        self._key = None

    def ensure(self, capture, width, height):
        if not capture:
            with self._lock:
                self._stop_locked()
            return
        key = (
            capture.get("display") or "",
            str(capture.get("xid") or ""),
            int(capture.get("cdp") or 9224),
            int(width),
            int(height),
        )
        with self._lock:
            if key == self._key and (
                self._pipe is not None or self._ff is not None or self._cdp is not None
            ):
                return
            self._stop_locked()
            self._stop.clear()
            self._key = key
            cap = dict(capture)
            if not cap.get("xid") and cap.get("display"):
                cap["xid"] = _find_xid_on_display(cap.get("display"), cap.get("xauth") or "")
            # CDP first. ximagesrc of Chromium under gamescope (4K Steam
            # Machine, tiled GPU buffers) looks interlaced / slanted / doubled.
            # GST/ffmpeg only if DevTools is down.
            if self._start_cdp(cap, width, height):
                return
            if HAVE_GST and cap.get("xid") and self._start_gst(cap, width, height):
                return
            if cap.get("xid"):
                self._start_ffmpeg(cap, width, height)
            else:
                print("[overlay] no VacuumTube xid and CDP failed; not capturing root (black on gamescope)", flush=True)

    def _start_cdp(self, capture, width, height):
        port = int(capture.get("cdp") or 9224)
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/json" % port, timeout=1.5) as resp:
                pages = json.loads(resp.read().decode("utf-8", "replace"))
        except Exception as e:
            print("[overlay] cdp /json: %s" % e, flush=True)
            return False
        page = None
        for p in pages if isinstance(pages, list) else []:
            if p.get("type") == "page" and p.get("webSocketDebuggerUrl"):
                page = p
                if "youtube" in (p.get("url") or "").lower():
                    break
        if not page:
            print("[overlay] cdp: no page target", flush=True)
            return False
        url = page["webSocketDebuggerUrl"]
        pending = object()
        self._cdp = pending
        t = threading.Thread(
            target=self._cdp_loop, args=(url, width, height, pending), daemon=True,
        )
        self._cdp_thread = t
        t.start()
        print("[overlay] cdp screencast :%d %dx%d" % (port, width, height), flush=True)
        return True

    def _cdp_loop(self, url, width, height, pending):
        ws = None
        try:
            ws = MiniWS(url)
            ws.connect()
            with self._lock:
                if self._stop.is_set():
                    return
                self._cdp = ws
            ws.send_json({"id": 1, "method": "Page.enable"})
            # Keep painting while gamescope shows a different app.
            ws.send_json({"id": 8, "method": "Page.setWebLifecycleState", "params": {"state": "active"}})
            ws.send_json({"id": 9, "method": "Emulation.setFocusEmulationEnabled", "params": {"enabled": True}})
            ws.send_json({
                "id": 2,
                "method": "Page.startScreencast",
                "params": {
                    "format": "jpeg",
                    "quality": 55,
                    "maxWidth": int(width),
                    "maxHeight": int(height),
                    "everyNthFrame": 1,
                },
            })
            ack_id = 10
            while not self._stop.is_set():
                try:
                    msg = ws.recv_json(timeout=1.0)
                except socket.timeout:
                    continue
                except (OSError, RuntimeError, AttributeError) as e:
                    if not self._stop.is_set():
                        print("[overlay] cdp recv: %s" % e, flush=True)
                    break
                if msg is None:
                    break
                if msg.get("method") != "Page.screencastFrame":
                    continue
                params = msg.get("params") or {}
                sid = params.get("sessionId")
                data = params.get("data")
                try:
                    ws.send_json({
                        "id": ack_id,
                        "method": "Page.screencastFrameAck",
                        "params": {"sessionId": sid},
                    })
                    ack_id += 1
                except Exception:
                    break
                if not data:
                    continue
                try:
                    raw = base64.b64decode(data)
                    pb = pixbuf_from_jpeg(raw)
                    if pb is not None:
                        self.on_frame(pb)
                except Exception as e:
                    print("[overlay] cdp frame: %s" % e, flush=True)
        except Exception as e:
            print("[overlay] cdp loop: %s" % e, flush=True)
        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass
            with self._lock:
                if self._cdp is pending or self._cdp is ws:
                    self._cdp = None
                    self._key = None

    def _start_gst(self, capture, width, height):
        display = capture.get("display") or ":0"
        xid = capture.get("xid")
        if not xid:
            print("[overlay] gst refused: no xid (root capture is black on gamescope)", flush=True)
            return False
        try:
            xid_int = int(str(xid), 0)
        except ValueError:
            print("[overlay] gst bad xid=%s" % xid, flush=True)
            return False
        xauth = capture.get("xauth")
        old_xauth = os.environ.get("XAUTHORITY")
        if xauth and os.path.isfile(xauth):
            os.environ["XAUTHORITY"] = xauth
        parts = [
            "ximagesrc",
            'display-name="%s"' % display.replace('"', ""),
            "use-damage=false",
            "show-pointer=false",
            "xid=%d" % xid_int,
        ]
        launch = (
            " ".join(parts)
            + " ! videoconvert"
            + " ! videoscale method=1 add-borders=false"
            + " ! video/x-raw,width=%d,height=%d,framerate=15/1" % (width, height)
            + " ! videoconvert"
            + " ! video/x-raw,format=RGB,pixel-aspect-ratio=1/1"
            + " ! appsink name=sink emit-signals=true max-buffers=2 drop=true sync=false"
        )
        try:
            pipe = Gst.parse_launch(launch)
            sink = pipe.get_by_name("sink")
            sink.connect("new-sample", self._on_sample)
            if pipe.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
                pipe.set_state(Gst.State.NULL)
                print("[overlay] gst set_state PLAYING failed", flush=True)
                return False
            self._pipe = pipe
            print("[overlay] gst capture %s xid=%s %dx%d" % (display, xid, width, height), flush=True)
            return True
        except Exception as e:
            print("[overlay] gst start failed: %s" % e, flush=True)
            return False
        finally:
            if old_xauth is None:
                os.environ.pop("XAUTHORITY", None)
            elif xauth and os.path.isfile(xauth):
                os.environ["XAUTHORITY"] = old_xauth

    def _on_sample(self, sink):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        pb = _pixbuf_from_gst_sample(sample)
        if pb is not None:
            self.on_frame(pb)
        return Gst.FlowReturn.OK

    def _start_ffmpeg(self, capture, width, height):
        ff = shutil.which("ffmpeg")
        if not ff:
            print("[overlay] no ffmpeg fallback", flush=True)
            return False
        display = capture.get("display") or ":0"
        env = os.environ.copy()
        env["DISPLAY"] = display
        xauth = capture.get("xauth")
        if xauth and os.path.isfile(xauth):
            env["XAUTHORITY"] = xauth
        else:
            env.pop("XAUTHORITY", None)
        cmd = [
            ff, "-nostdin", "-hide_banner", "-loglevel", "error",
            "-f", "x11grab", "-draw_mouse", "0", "-framerate", "15",
        ]
        xid = capture.get("xid")
        if xid:
            cmd.extend(["-window_id", str(int(str(xid), 0))])
        cmd.extend([
            "-i", display,
            "-vf", "scale=%d:%d" % (width, height),
            "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
        ])
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        except Exception as e:
            print("[overlay] ffmpeg start failed: %s" % e, flush=True)
            return False
        self._ff = proc
        t = threading.Thread(target=self._ffmpeg_loop, args=(proc, width, height), daemon=True)
        t.start()
        print("[overlay] ffmpeg capture %s %dx%d" % (display, width, height), flush=True)
        return True

    def _ffmpeg_loop(self, proc, width, height):
        frame = width * height * 3
        buf = b""
        while not self._stop.is_set() and proc.poll() is None:
            chunk = proc.stdout.read(frame - len(buf))
            if not chunk:
                break
            buf += chunk
            if len(buf) < frame:
                continue
            data, buf = buf[:frame], buf[frame:]
            try:
                pb = GdkPixbuf.Pixbuf.new_from_data(
                    data, GdkPixbuf.Colorspace.RGB, False, 8, width, height, width * 3,
                )
                self.on_frame(pb.copy())
            except Exception:
                pass
        try:
            proc.kill()
        except Exception:
            pass


class Overlay:
    def __init__(self, state_path):
        self.state_path = state_path
        self.state = load_state(state_path)
        self.frame = None
        self.sw, self.sh = 1920, 1080
        self.win = None
        self.area = None
        self.capture = Capture(self._set_frame)
        self._mtime = 0

    def _save_display(self, sw, sh):
        try:
            d = os.path.dirname(self.state_path)
            path = os.path.join(d, "display.json")
            tmp = os.path.join(d, ".display.tmp")
            with open(tmp, "w") as f:
                json.dump({"width": int(sw), "height": int(sh)}, f)
            os.replace(tmp, path)
        except Exception as e:
            print("[overlay] display.json: %s" % e, flush=True)

    def _set_frame(self, pixbuf):
        self.frame = pixbuf
        GLib.idle_add(self._redraw)

    def _redraw(self):
        if self.area is not None:
            self.area.queue_draw()
        return False

    def _monitor_size(self):
        """Size of the gamescope Xwayland the overlay lives on.

        Homemade Steam Machines are often 1080p / 1440p / 4K, not Deck 800p.
        Use the largest Gdk monitor (already in pixels on X11 gamescope),
        not a hardcoded 1280x800 and not Gdk.Screen (deprecated, wrong on
        multi-output PCs).
        """
        try:
            disp = Gdk.Display.get_default()
            n = disp.get_n_monitors() if disp is not None else 0
            best_w, best_h = 0, 0
            for i in range(n):
                mon = disp.get_monitor(i)
                geo = mon.get_geometry()
                if geo.width * geo.height > best_w * best_h:
                    best_w, best_h = geo.width, geo.height
            if best_w >= 320:
                return int(best_w), int(best_h)
        except Exception as e:
            print("[overlay] monitor geometry failed: %s" % e, flush=True)
        return 1920, 1080

    def build(self):
        self.sw, self.sh = self._monitor_size()
        self._save_display(self.sw, self.sh)
        win = Gtk.Window()
        win.set_decorated(False)
        win.set_skip_taskbar_hint(True)
        win.set_skip_pager_hint(True)
        win.set_app_paintable(True)
        win.set_title("VTPiP Overlay")
        win.set_accept_focus(False)
        win.set_focus_on_map(False)
        win.set_can_focus(False)
        win.set_keep_above(True)
        win.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
        win.set_default_size(self.sw, self.sh)
        win.set_size_request(self.sw, self.sh)
        win.move(0, 0)
        win.fullscreen()
        rgba = win.get_screen().get_rgba_visual()
        if rgba:
            win.set_visual(rgba)

        area = Gtk.DrawingArea()
        area.set_app_paintable(True)
        area.connect("draw", self.on_draw)
        win.add(area)

        def on_map(_w):
            gdk_win = win.get_window()
            xid = gdk_win.get_xid()
            ok = set_overlay_atom(xid)
            passthrough = False
            if HAVE_CAIRO:
                try:
                    gdk_win.input_shape_combine_region(cairo.Region(), 0, 0)
                    passthrough = True
                except Exception as e:
                    print("[overlay] input passthrough failed: %s" % e, flush=True)
            print("[overlay] mapped xid=%s atom=%s passthrough=%s %dx%d"
                  % (hex(xid), ok, passthrough, self.sw, self.sh), flush=True)

        win.connect("map", on_map)
        win.connect("destroy", Gtk.main_quit)
        self.win = win
        self.area = area
        return win

    def on_draw(self, _widget, cr):
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        st = self.state
        if not st.get("show"):
            return False
        x, y, w, h = pip_rect(self.sw, self.sh, st.get("size") or "small", st.get("corner") or "bottom-right")
        alpha = max(0, min(100, int(st.get("opacity") or 90))) / 100.0
        frame = self.frame
        cr.set_operator(cairo.OPERATOR_OVER)
        if frame is not None:
            cr.save()
            cr.translate(x, y)
            fw, fh = frame.get_width(), frame.get_height()
            if fw > 0 and fh > 0:
                cr.scale(w / float(fw), h / float(fh))
                Gdk.cairo_set_source_pixbuf(cr, frame, 0, 0)
                cr.paint_with_alpha(alpha)
            cr.restore()
        else:
            cr.set_source_rgba(0.05, 0.05, 0.08, alpha * 0.85)
            cr.rectangle(x, y, w, h)
            cr.fill()
            msg = st.get("message") or "Waiting for VacuumTube…"
            cr.set_source_rgba(1, 1, 1, alpha)
            cr.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            cr.set_font_size(max(14, h // 16))
            ext = cr.text_extents(msg)
            cr.move_to(x + (w - ext.width) / 2, y + h / 2)
            cr.show_text(msg)
        cr.set_source_rgba(1, 1, 1, alpha * 0.35)
        cr.set_line_width(2)
        cr.rectangle(x + 1, y + 1, w - 2, h - 2)
        cr.stroke()
        return False

    def tick(self):
        try:
            mtime = os.path.getmtime(self.state_path)
        except OSError:
            mtime = 0
        if mtime != self._mtime:
            self._mtime = mtime
            self.state = load_state(self.state_path)
        sw, sh = self._monitor_size()
        if self.win is not None and (sw, sh) != (self.sw, self.sh) and sw >= 320:
            self.sw, self.sh = sw, sh
            self._save_display(sw, sh)
            try:
                self.win.resize(sw, sh)
                self.win.move(0, 0)
            except Exception:
                pass
            print("[overlay] resize %dx%d" % (sw, sh), flush=True)
        st = self.state
        x, y, w, h = pip_rect(self.sw, self.sh, st.get("size") or "small", st.get("corner") or "bottom-right")
        cap_w, cap_h = SIZES.get(st.get("size") or "small", SIZES["small"])
        if st.get("show") and st.get("capture"):
            self.capture.ensure(st.get("capture"), cap_w, cap_h)
        else:
            self.capture.ensure(None, cap_w, cap_h)
        self.area.queue_draw()
        return True

    def run(self):
        win = self.build()
        win.show_all()
        GLib.timeout_add(400, self.tick)
        Gtk.main()
        self.capture.stop()


def probe():
    info = {
        "gst": HAVE_GST,
        "cairo": HAVE_CAIRO,
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "gtk": True,
    }
    print(json.dumps(info), flush=True)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-dir", required=False, default="")
    ap.add_argument("--probe", action="store_true")
    args = ap.parse_args()
    if args.probe:
        return probe()
    if not args.state_dir:
        print("overlay.py --state-dir DIR", file=sys.stderr)
        return 2
    if not HAVE_CAIRO:
        print("[overlay] pycairo is required", flush=True)
        return 1
    path = os.path.join(args.state_dir, "state.json")
    Overlay(path).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
