"""Control VacuumTube through Chrome DevTools: player, InnerTube, lists."""
import base64
import json
import os
import socket
import struct
import threading
import time
import urllib.request
from urllib.parse import urlparse

try:
    from decky import logger  # type: ignore
except Exception:  # running outside the plugin loader
    class logger:
        @staticmethod
        def warning(*a, **k):
            print("[vtpip]", *a)
        @staticmethod
        def debug(*a, **k):
            pass

CDP_PORT = 9224
WEB_CLIENT_VERSION = "2.20250925.01.00"
TV_ORIGIN = "https://www.youtube.com/tv?env_enableMediaStreams=true"

# Runs inside youtube.com/tv. Keep the return values small (parsed lists).
_HELPER_JS = r"""
(() => {
  if (window.__vtpip && window.__vtpip.v === 13) return "ok";
  const g = (k) => { try { return window.ytcfg.get(k); } catch (e) { return null; } };
  function videoIdFromUrl() {
    const m = (String(location.hash || "") + "&" + String(location.search || "")).match(/[?&]v=([a-zA-Z0-9_-]{6,})/);
    return m ? m[1] : "";
  }
  function clickLabel(want) {
    const w = String(want || "").toLowerCase();
    const els = document.querySelectorAll("[aria-label]");
    for (const el of els) {
      const l = (el.getAttribute("aria-label") || "").toLowerCase();
      if (l === w || l.startsWith(w + " ") || l.startsWith(w + ",")) {
        el.click();
        return true;
      }
    }
    return false;
  }
  function textOf(node) {
    if (!node) return "";
    if (typeof node === "string") return node;
    if (node.simpleText) return node.simpleText;
    if (Array.isArray(node.runs)) return node.runs.map((r) => r.text || "").join("");
    return "";
  }
  function watchId(node) {
    if (!node || typeof node !== "object") return "";
    const we = node.watchEndpoint
      || (node.navigationEndpoint && node.navigationEndpoint.watchEndpoint)
      || (node.innertubeCommand && node.innertubeCommand.watchEndpoint)
      || (node.endpoint && node.endpoint.watchEndpoint)
      || {};
    const vid = node.videoId || we.videoId || "";
    return /^[a-zA-Z0-9_-]{11}$/.test(vid) ? vid : "";
  }
  function pushVideo(out, seen, vid, title, channel, length) {
    if (!vid || !title || seen.has(vid)) return;
    seen.add(vid);
    out.push({
      id: vid,
      title: String(title).slice(0, 180),
      channel: String(channel || "").slice(0, 80),
      length: String(length || ""),
      thumb: "https://i.ytimg.com/vi/" + vid + "/mqdefault.jpg",
    });
  }
  function walkVideos(node, out, seen) {
    if (!node || typeof node !== "object") return;
    if (Array.isArray(node)) { node.forEach((n) => walkVideos(n, out, seen)); return; }
    if (node.tileRenderer) {
      try {
        const tr = node.tileRenderer;
        const md = (tr.metadata && tr.metadata.tileMetadataRenderer) || {};
        const vid = watchId(tr) || watchId(node);
        let length = "";
        const ov = ((tr.header || {}).tileHeaderRenderer || {}).thumbnailOverlays || [];
        for (let i = 0; i < ov.length; i++) {
          const ts = ov[i] && ov[i].thumbnailOverlayTimeStatusRenderer;
          if (ts) length = textOf(ts.text);
        }
        const line0 = ((((md.lines || [])[0] || {}).lineRenderer || {}).items || [])[0];
        const channel = textOf(line0 && line0.lineItemRenderer && line0.lineItemRenderer.text);
        pushVideo(out, seen, vid, textOf(md.title), channel, length);
      } catch (e) {}
    }
    const vid = watchId(node);
    const title = textOf(node.title) || textOf(node.headline);
    if (vid && title) {
      const channel = textOf(node.shortBylineText) || textOf(node.ownerText)
        || textOf(node.longBylineText);
      const length = textOf(node.lengthText);
      pushVideo(out, seen, vid, title, channel, length);
    }
    for (const k in node) {
      if (Object.prototype.hasOwnProperty.call(node, k)) walkVideos(node[k], out, seen);
    }
  }
  function pushComment(out, seen, cid, author, text, likes, time) {
    if (!text || out.length >= 40) return;
    const id = cid || ("c-" + out.length);
    if (seen.has(id)) return;
    seen.add(id);
    out.push({
      id: id,
      author: String(author || "YouTube").slice(0, 80),
      text: String(text).slice(0, 600),
      likes: parseInt(likes, 10) || 0,
      time: String(time || "").slice(0, 40),
      heart: false,
    });
  }
  function walkComments(node, out, seen) {
    if (!node || typeof node !== "object" || out.length >= 40) return;
    if (Array.isArray(node)) { node.forEach((n) => walkComments(n, out, seen)); return; }
    const ent = node.commentEntityPayload || (node.payload && node.payload.commentEntityPayload);
    if (ent && ent.properties) {
      const text = (ent.properties.content && ent.properties.content.content) || "";
      const likes = ent.toolbar && (ent.toolbar.likeCountNotliked || ent.toolbar.likeCountLiked);
      pushComment(out, seen, ent.properties.commentId || ent.key,
        ent.author && (ent.author.displayName || ent.author.channelId),
        text, likes, ent.properties.publishedTime);
    }
    const cr = node.commentRenderer || (node.comment && node.comment.commentRenderer) || null;
    if (cr && (cr.commentId || textOf(cr.contentText))) {
      pushComment(out, seen, cr.commentId, textOf(cr.authorText), textOf(cr.contentText),
        cr.likeCount, textOf(cr.publishedTimeText));
    }
    for (const k in node) {
      if (Object.prototype.hasOwnProperty.call(node, k)) walkComments(node[k], out, seen);
    }
  }
  function commentContinuation(root) {
    let token = "";
    (function walk(n, d) {
      if (!n || typeof n !== "object" || d > 24 || token) return;
      const cir = n.continuationItemRenderer;
      if (cir) {
        const t = ((((cir.continuationEndpoint || {}).continuationCommand) || {}).token)
          || ((((cir.button || {}).buttonRenderer || {}).command || {}).continuationCommand || {}).token;
        if (t) { token = t; return; }
      }
      if (Array.isArray(n)) n.forEach((x) => walk(x, d + 1));
      else for (const k in n) walk(n[k], d + 1);
    })(root, 0);
    return token;
  }
  async function innertube(path, body, web) {
    const key = g("INNERTUBE_API_KEY");
    const ctx = g("INNERTUBE_CONTEXT") || { client: {} };
    const context = web ? {
      client: {
        hl: (ctx.client && ctx.client.hl) || "en",
        gl: (ctx.client && ctx.client.gl) || "US",
        clientName: "WEB",
        clientVersion: "2.20250925.01.00",
        visitorData: ctx.client && ctx.client.visitorData,
      },
    } : ctx;
    const r = await fetch("https://www.youtube.com/youtubei/v1/" + path + "?key=" + encodeURIComponent(key) + "&prettyPrint=false", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(Object.assign({ context }, body || {})),
      credentials: "include",
    });
    if (!r.ok) {
      const t = await r.text();
      throw new Error("innertube " + path + " " + r.status + " " + t.slice(0, 120));
    }
    return await r.json();
  }
  window.__vtpip = {
    v: 13,
    htmlPlayer() {
      return document.querySelector(".html5-video-player");
    },
    currentId() {
      const hp = this.htmlPlayer();
      try {
        const id = hp && hp.getVideoData && hp.getVideoData().video_id;
        if (id) return String(id);
      } catch (e) {}
      return videoIdFromUrl();
    },
    player() {
      const v = document.querySelector("video");
      const hp = this.htmlPlayer();
      const id = this.currentId();
      let title = "";
      try { title = (hp && hp.getVideoData && hp.getVideoData().title) || ""; } catch (e) {}
      if (!title) {
        const tEl = document.querySelector(".ytLrVideoDetailsVideoTitleText, .ytp-title-link, [class*='VideoTitle']");
        if (tEl && tEl.textContent) title = tEl.textContent.trim();
      }
      return {
        videoId: id,
        paused: v ? !!v.paused : true,
        ended: v ? !!v.ended : false,
        time: v && isFinite(v.currentTime) ? v.currentTime : 0,
        duration: v && isFinite(v.duration) ? v.duration : 0,
        title,
        loggedIn: !!g("LOGGED_IN"),
        href: String(location.href || ""),
        hasVideo: !!v,
      };
    },
    playPause() {
      const v = document.querySelector("video");
      if (!v) return { ok: false, error: "no video" };
      if (v.paused) { v.play(); return { ok: true, paused: false }; }
      v.pause();
      return { ok: true, paused: true };
    },
    skip(seconds) {
      const v = document.querySelector("video");
      if (!v) return { ok: false, error: "no video" };
      const d = isFinite(v.duration) ? v.duration : 1e9;
      v.currentTime = Math.max(0, Math.min(d, v.currentTime + Number(seconds || 0)));
      return { ok: true, time: v.currentTime, duration: d };
    },
    seek(seconds) {
      const v = document.querySelector("video");
      if (!v) return { ok: false, error: "no video" };
      const d = isFinite(v.duration) ? v.duration : 1e9;
      v.currentTime = Math.max(0, Math.min(d, Number(seconds || 0)));
      return { ok: true, time: v.currentTime, duration: d };
    },
    click(label) { return { ok: clickLabel(label) }; },
    next() {
      const hp = this.htmlPlayer();
      if (hp && typeof hp.nextVideo === "function") {
        hp.nextVideo();
        return { ok: true, via: "player.nextVideo" };
      }
      if (clickLabel("Next")) return { ok: true, via: "click" };
      return { ok: false, error: "no next" };
    },
    prev() {
      const hp = this.htmlPlayer();
      if (hp && typeof hp.previousVideo === "function") {
        hp.previousVideo();
        return { ok: true, via: "player.previousVideo" };
      }
      if (clickLabel("Previous")) return { ok: true, via: "click" };
      return { ok: false, error: "no previous" };
    },
    navigate(id) {
      const vid = String(id || "").trim();
      if (!vid) return { ok: false, error: "missing id" };
      const hash = "#/watch?v=" + encodeURIComponent(vid);
      const old = location.hash;
      if (old !== hash) location.hash = hash;
      try {
        window.dispatchEvent(new HashChangeEvent("hashchange", {
          oldURL: location.origin + location.pathname + location.search + old,
          newURL: location.href,
        }));
      } catch (e) {}
      return { ok: true, href: location.href };
    },
    async playVideo(id) {
      const vid = String(id || "").trim();
      if (!/^[a-zA-Z0-9_-]{11}$/.test(vid)) return { ok: false, error: "bad id" };
      const hp = this.htmlPlayer();
      if (!hp || typeof hp.loadVideoById !== "function") {
        return { ok: false, error: "no-player" };
      }
      hp.loadVideoById(vid);
      const start = Date.now();
      while (Date.now() - start < 8000) {
        await new Promise((r) => setTimeout(r, 200));
        if (this.currentId() === vid) {
          try { if (typeof hp.playVideo === "function") hp.playVideo(); } catch (e) {}
          const hash = "#/watch?v=" + encodeURIComponent(vid);
          if (location.hash !== hash) location.hash = hash;
          try { await this.syncChrome(vid); } catch (e) {}
          return { ok: true, via: "loadVideoById", href: location.href, videoId: vid };
        }
      }
      return { ok: false, error: "no-switch", via: "loadVideoById", videoId: this.currentId() };
    },
    async syncChrome(id) {
      const vid = String(id || "").trim();
      let title = "", channel = "", avatar = "";
      try {
        const meta = await this.details(vid);
        title = (meta && meta.title) || "";
        channel = (meta && meta.channel) || "";
        avatar = (meta && meta.avatar) || "";
      } catch (e) {}
      const setFmt = (el, t) => {
        if (!el || !t) return;
        el.textContent = t;
        try { el.simpleText = t; } catch (e) {}
      };
      const tray = document.querySelector("ytlr-video-title-tray");
      if (tray) {
        setFmt(tray.querySelector('[idomkey="title-text"] yt-formatted-string') || tray.querySelector("yt-formatted-string"), title);
        setFmt(tray.querySelector('[idomkey="detail-text-0"] yt-formatted-string'), channel);
      }
      const ytp = document.querySelector(".ytp-title-link, .ytp-title-text");
      if (ytp && title) ytp.textContent = title;
      const owner = document.querySelector("ytlr-video-owner-renderer");
      if (owner && avatar) {
        const faces = owner.querySelectorAll("image, img");
        for (const face of faces) {
          try { face.style.backgroundImage = 'url("' + avatar + '")'; } catch (e) {}
          try { if (face.tagName === "IMG" || face.src !== undefined) face.src = avatar; } catch (e) {}
        }
      }
      try {
        const watch = document.querySelector("ytlr-watch-page");
        const inst = watch && watch.__instance;
        const cfg = inst && inst.state && inst.state.playbackConfig;
        if (cfg && cfg.watchEndpoint) cfg.watchEndpoint.videoId = vid;
      } catch (e) {}
      return { ok: true, title, channel, avatar: !!avatar };
    },
    async details(id) {
      const vid = id || videoIdFromUrl();
      if (!vid) return null;
      const data = await innertube("next", { videoId: vid });
      const title = (function findTitle(n, d) {
        if (!n || typeof n !== "object" || d > 18) return "";
        if (n.videoMetadataRenderer) return textOf(n.videoMetadataRenderer.title);
        if (Array.isArray(n)) {
          for (const x of n) { const t = findTitle(x, d + 1); if (t) return t; }
          return "";
        }
        for (const k in n) {
          const t = findTitle(n[k], d + 1);
          if (t) return t;
        }
        return "";
      })(data, 0);
      const owner = (function findOwner(n, d) {
        if (!n || typeof n !== "object" || d > 18) return null;
        if (n.videoOwnerRenderer) return n.videoOwnerRenderer;
        if (Array.isArray(n)) {
          for (const x of n) { const t = findOwner(x, d + 1); if (t) return t; }
          return null;
        }
        for (const k in n) {
          const t = findOwner(n[k], d + 1);
          if (t) return t;
        }
        return null;
      })(data, 0);
      const channel = owner ? (textOf(owner.title) || "") : "";
      let avatar = "";
      const thumbs = owner && ((owner.thumbnail && owner.thumbnail.thumbnails) || (owner.thumbnails && owner.thumbnails.thumbnails) || owner.thumbnails);
      if (Array.isArray(thumbs) && thumbs.length) {
        const best = thumbs[thumbs.length - 1];
        avatar = (best && (best.url || best.imageUrl)) || "";
      }
      return {
        id: vid,
        title: title || "",
        channel: channel || "",
        avatar: avatar || "",
        thumb: "https://i.ytimg.com/vi/" + vid + "/mqdefault.jpg",
      };
    },
    async watchLater() {
      const tries = [
        { web: true, body: { browseId: "VLWL" } },
        { web: false, body: { browseId: "VLWL" } },
        { web: false, body: { playlistId: "WL" } },
      ];
      let items = [];
      let lastErr = "";
      for (const t of tries) {
        try {
          const data = await innertube("browse", t.body, t.web);
          const seen = new Set();
          const out = [];
          walkVideos(data, out, seen);
          if (out.length) { items = out; break; }
          if (data && data.alerts) lastErr = lastErr || "sign-in";
        } catch (e) { lastErr = lastErr || String(e && e.message || e); }
      }
      return {
        ok: true,
        items,
        error: items.length ? "" : (lastErr === "sign-in"
          ? "Sign in inside VacuumTube (Account on the TV home) so Watch Later can load here."
          : (lastErr || "Watch Later is empty.")),
      };
    },
    async search(query) {
      const q = String(query || "").trim();
      if (!q) return { ok: false, error: "empty query", items: [] };
      const data = await innertube("search", { query: q }, true);
      const seen = new Set();
      const items = [];
      walkVideos(data, items, seen);
      return { ok: true, items: items.slice(0, 25) };
    },
    async comments(id) {
      const vid = id || this.currentId() || videoIdFromUrl();
      if (!vid) return { ok: false, error: "no video", items: [] };
      const seen = new Set();
      const items = [];
      try {
        const web = await innertube("next", { videoId: vid }, true);
        const panels = (web && web.engagementPanels) || [];
        const commentsPanel = panels.find((p) => {
          const e = p.engagementPanelSectionListRenderer || {};
          return String(e.panelIdentifier || e.targetId || "").toLowerCase().indexOf("comment") >= 0;
        });
        const token = commentContinuation(commentsPanel) || commentContinuation(web);
        if (token) {
          const more = await innertube("next", { continuation: token }, true);
          walkComments(more, items, seen);
        }
        if (!items.length) walkComments(web, items, seen);
      } catch (e) {}
      if (!items.length) {
        try {
          const tv = await innertube("next", { videoId: vid }, false);
          walkComments(tv, items, seen);
        } catch (e) {}
      }
      return { ok: true, items: items.slice(0, 30), videoId: vid };
    },
  };
  return "ok";
})()
"""


class MiniWS:
    def __init__(self, url):
        self.url = url
        self.sock = None
        self._buf = b""

    def connect(self):
        u = urlparse(self.url)
        host = u.hostname or "127.0.0.1"
        port = u.port or 80
        path = u.path or "/"
        if u.query:
            path += "?" + u.query
        sock = socket.create_connection((host, port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        req = (
            "GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\n"
            "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n" % (path, host, port, key)
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
        if b"101" not in header.split(b"\r\n", 1)[0]:
            sock.close()
            raise RuntimeError("CDP handshake failed")
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
        header = bytearray([0x81])
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

    def _send_raw(self, payload, opcode):
        if self.sock is None or len(payload) >= 126:
            return
        mask = os.urandom(4)
        header = bytearray([0x80 | opcode, 0x80 | len(payload)])
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
        fin = bool(b0 & 0x80)
        opcode = b0 & 0x0F
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
        if b1 & 0x80:
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
        return fin, opcode, payload

    def recv_json(self, timeout=12.0):
        if self.sock is None:
            return None
        self.sock.settimeout(timeout)
        parts = []
        data_op = None
        while True:
            fin, opcode, payload = self._recv_frame()
            if opcode == 0x8:
                return None
            if opcode == 0x9:
                self._send_raw(payload, 0xA)
                continue
            if opcode == 0xA:
                continue
            if opcode in (0x1, 0x2):
                data_op = opcode
                parts = [payload]
            elif opcode == 0x0:
                parts.append(payload)
            else:
                continue
            if fin and parts:
                raw = b"".join(parts)
                if data_op == 0x1 or (data_op == 0x2 and raw[:1] in (b"{", b"[")):
                    return json.loads(raw.decode("utf-8"))
                parts = []


def _page_ws_url(port=CDP_PORT):
    url = "http://127.0.0.1:%d/json" % int(port)
    with urllib.request.urlopen(url, timeout=1.5) as resp:
        pages = json.loads(resp.read().decode("utf-8", "replace"))
    for p in pages if isinstance(pages, list) else []:
        if p.get("type") == "page" and p.get("webSocketDebuggerUrl"):
            if "youtube" in (p.get("url") or "").lower():
                return p["webSocketDebuggerUrl"]
    for p in pages if isinstance(pages, list) else []:
        if p.get("type") == "page" and p.get("webSocketDebuggerUrl"):
            return p["webSocketDebuggerUrl"]
    raise RuntimeError("VacuumTube DevTools has no page")


class YtSession:
    def __init__(self, port=CDP_PORT):
        self.port = port
        self._lock = threading.Lock()
        self._ws = None
        self._id = 20
        self._helper = False
        self._meta = {}
        self._meta_id = ""
        self.queue = []
        self.index = -1

    def close(self):
        with self._lock:
            if self._ws is not None:
                self._ws.close()
                self._ws = None
            self._helper = False

    def _call(self, method, params=None, timeout=12.0):
        ws = self._ws
        if ws is None or ws.sock is None:
            raise RuntimeError("CDP not connected")
        self._id += 1
        cid = self._id
        ws.send_json({"id": cid, "method": method, "params": params or {}})
        deadline = time.time() + timeout
        while True:
            left = max(0.1, deadline - time.time())
            msg = ws.recv_json(timeout=left)
            if msg is None:
                raise RuntimeError("CDP closed")
            if msg.get("id") == cid:
                if msg.get("error"):
                    raise RuntimeError(str(msg["error"]))
                return msg.get("result") or {}
            if time.time() > deadline:
                raise RuntimeError("CDP timeout %s" % method)

    def _connect(self):
        url = _page_ws_url(self.port)
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
        ws = MiniWS(url)
        ws.connect()
        self._ws = ws
        self._helper = False
        self._call("Runtime.enable", timeout=4)
        self._ensure_helper()

    def _ensure_helper(self):
        res = self._eval(_HELPER_JS, timeout=8)
        self._helper = res == "ok" or res is True or res == "ok"
        if not self._helper:
            logger.warning("[vtpip] helper inject returned %r", res)

    def _eval(self, expr, await_promise=False, timeout=15.0):
        result = self._call(
            "Runtime.evaluate",
            {
                "expression": expr,
                "returnByValue": True,
                "awaitPromise": bool(await_promise),
            },
            timeout=timeout,
        )
        if result.get("exceptionDetails"):
            desc = result["exceptionDetails"]
            text = (desc.get("text") or "") + " " + str(
                ((desc.get("exception") or {}).get("description")) or ""
            )
            raise RuntimeError(text.strip() or "JS error")
        inner = result.get("result") or {}
        return inner.get("value")

    def _ready(self):
        if self._ws is not None and self._ws.sock is not None and self._helper:
            try:
                ver = self._eval("window.__vtpip && window.__vtpip.v", timeout=3)
                if ver == 13:
                    return
                self._ensure_helper()
                return
            except Exception:
                pass
        self._connect()

    def invoke(self, method, *args, await_promise=False, timeout=15.0):
        payload = json.dumps(list(args), ensure_ascii=False)
        expr = "window.__vtpip.%s(...%s)" % (method, payload)
        if await_promise:
            expr = "(async () => window.__vtpip.%s(...%s))()" % (method, payload)
        last = None
        with self._lock:
            for attempt in range(3):
                try:
                    self._ready()
                    return self._eval(expr, await_promise=await_promise, timeout=timeout)
                except Exception as e:
                    last = e
                    logger.warning("[vtpip] invoke %s try %s: %s", method, attempt, e)
                    self.close()
                    self._helper = False
                    time.sleep(0.35)
        raise last

    def available(self):
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/json" % self.port, timeout=0.8).read(64)
            return True
        except Exception:
            return False

    def player(self):
        st = self.invoke("player") or {}
        vid = st.get("videoId") or ""
        if vid and vid != self._meta_id:
            try:
                meta = self.invoke("details", vid, await_promise=True, timeout=12) or {}
                if meta:
                    self._meta = meta
                    self._meta_id = vid
            except Exception as e:
                logger.debug("[vtpip] details: %s", e)
        if vid and self._meta_id == vid:
            st["title"] = st.get("title") or self._meta.get("title") or ""
            st["channel"] = self._meta.get("channel") or ""
            st["thumb"] = self._meta.get("thumb") or ("https://i.ytimg.com/vi/%s/mqdefault.jpg" % vid)
        elif vid:
            st["thumb"] = "https://i.ytimg.com/vi/%s/mqdefault.jpg" % vid
        st["ok"] = True
        st["queueIndex"] = self.index
        st["queueLen"] = len(self.queue)
        return st

    def play_pause(self):
        return self.invoke("playPause") or {}

    def skip(self, seconds):
        return self.invoke("skip", float(seconds)) or {}

    def play_id(self, video_id, items=None, index=None):
        vid = str(video_id or "").strip()
        if not vid:
            return {"ok": False, "error": "missing video id"}
        if items is not None:
            self.queue = list(items)
        if index is not None:
            self.index = int(index)
        else:
            ids = [x.get("id") for x in self.queue]
            self.index = ids.index(vid) if vid in ids else self.index
        try:
            r = self.invoke("playVideo", vid, await_promise=True, timeout=12) or {}
        except Exception as e:
            r = {"ok": False, "error": str(e)}
        r["videoId"] = vid
        logger.warning("[vtpip] play_id %s -> %s", vid, r)
        return r

    def next_video(self):
        if self.queue:
            nxt = self.index + 1 if self.index >= 0 else 0
            if nxt < len(self.queue):
                self.index = nxt
                return self.play_id(self.queue[self.index].get("id"), index=self.index)
        return self.invoke("next") or {"ok": False, "error": "no next"}

    def prev_video(self):
        if self.queue and self.index > 0:
            self.index -= 1
            return self.play_id(self.queue[self.index].get("id"), index=self.index)
        return self.invoke("prev") or {"ok": False, "error": "no previous"}

    def watch_later(self):
        r = self.invoke("watchLater", await_promise=True, timeout=20) or {}
        items = r.get("items") or []
        if items:
            self.queue = items
            self.index = -1
        r["ok"] = True
        r["items"] = items
        return r

    def search(self, query):
        r = self.invoke("search", query, await_promise=True, timeout=20) or {}
        items = r.get("items") or []
        if items:
            self.queue = items
            self.index = -1
        r["ok"] = bool(r.get("ok", True))
        r["items"] = items
        return r

    def comments(self, video_id=None):
        args = [video_id] if video_id else []
        r = self.invoke("comments", *args, await_promise=True, timeout=20) or {}
        r["ok"] = True
        r["items"] = r.get("items") or []
        return r


_session = None
_session_lock = threading.Lock()


def session():
    global _session
    with _session_lock:
        if _session is None:
            _session = YtSession()
        return _session
