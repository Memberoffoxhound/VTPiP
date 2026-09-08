const manifest = {"name":"VTPiP","author":"Memberoffoxhound","flags":[],"api_version":1,"publish":{"tags":["youtube","pip","overlay","gamemode","vacuumtube"],"description":"Watch YouTube in a corner while you play. VacuumTube picture-in-picture for Steam Game Mode.","image":""}};
const API_VERSION = 2;
if (!manifest?.name) {
    throw new Error('[@decky/api]: Failed to find plugin manifest.');
}
const internalAPIConnection = window.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit;
if (!internalAPIConnection) {
    throw new Error('[@decky/api]: Failed to connect to the loader as as the loader API was not initialized. This is likely a bug in Decky Loader.');
}
let api;
try {
    api = internalAPIConnection.connect(API_VERSION, manifest.name);
}
catch {
    api = internalAPIConnection.connect(1, manifest.name);
    console.warn(`[@decky/api] Requested API version ${API_VERSION} but the running loader only supports version 1. Some features may not work.`);
}
if (api._version != API_VERSION) {
    console.warn(`[@decky/api] Requested API version ${API_VERSION} but the running loader only supports version ${api._version}. Some features may not work.`);
}
const call = api.call;

const e = window.SP_REACT.createElement;
const useState = window.SP_REACT.useState;
const useEffect = window.SP_REACT.useEffect;
const useRef = window.SP_REACT.useRef;

const STAMP_PX = {
    small: [854, 480],
    large: [1280, 720],
};
const POSITIONS = [
    { id: "top-left", label: "Top left" },
    { id: "top-right", label: "Top right" },
    { id: "left-middle", label: "Left middle" },
    { id: "right-middle", label: "Right middle" },
    { id: "bottom-left", label: "Bottom left" },
    { id: "bottom-right", label: "Bottom right" },
];
const POSITION_ROWS = [
    ["top-left", "top-right"],
    ["left-middle", "right-middle"],
    ["bottom-left", "bottom-right"],
];
const POSITION_LABEL = {};
for (const p of POSITIONS) POSITION_LABEL[p.id] = p.label;
const SIZES = [
    { label: "Small (480p)", data: "small" },
    { label: "Large (720p)", data: "large" },
];

const RED = "#ff4d4d";
const GREEN = "#3ddc84";
const MUTED = "rgba(255,255,255,0.62)";
const CARD = {
    background: "rgba(255,255,255,0.06)",
    borderRadius: 10,
    padding: 10,
    marginBottom: 8,
    minWidth: 0,
};
const ROW = {
    display: "flex",
    gap: 10,
    alignItems: "center",
    minWidth: 0,
    padding: "8px 6px",
    borderRadius: 8,
    background: "rgba(0,0,0,0.18)",
};

function fmtTime(sec) {
    const n = Math.max(0, Math.floor(Number(sec) || 0));
    const h = Math.floor(n / 3600);
    const m = Math.floor((n % 3600) / 60);
    const s = n % 60;
    const mm = h ? String(m).padStart(2, "0") : String(m);
    const ss = String(s).padStart(2, "0");
    return h ? h + ":" + mm + ":" + ss : mm + ":" + ss;
}

function runningApp() {
    try {
        const app = DFL.Router.MainRunningApp;
        if (!app) return null;
        return { name: app.display_name || "", appid: app.appid ?? null };
    } catch {
        return null;
    }
}

function shouldHide(st, game) {
    if (!game) return true;
    const id = String(game.appid ?? "");
    const name = (game.name || "").toLowerCase();
    if (st && st.shortcut_appid != null && String(st.shortcut_appid) === id) return true;
    if (name.indexOf("vacuumtube") >= 0) return true;
    return false;
}

async function ensureShortcut(st) {
    if (st && st.shortcut_appid) return st.shortcut_appid;
    const info = await call("wrapper_info");
    const exe = `"${info.wrapper}"`;
    const dir = `"${info.startdir}"`;
    const appId = await SteamClient.Apps.AddShortcut("VacuumTube", exe, dir, "");
    if (appId) {
        try { SteamClient.Apps.SetShortcutExe(appId, exe); } catch (err) {}
        try { SteamClient.Apps.SetShortcutStartDir(appId, dir); } catch (err) {}
        try { SteamClient.Apps.SetShortcutName(appId, "VacuumTube"); } catch (err) {}
        if (info.icon) {
            try { SteamClient.Apps.SetShortcutIcon(appId, info.icon); } catch (err) {}
        }
        await call("set_shortcut_appid", appId);
    }
    return appId;
}

async function launchVacuumTube(st) {
    const appId = await ensureShortcut(st);
    if (!appId) throw new Error("Could not add VacuumTube to Steam");
    const id = String(appId);
    const sc = window.SteamClient;
    if (sc?.Apps?.RunGame) {
        sc.Apps.RunGame(id, "", -1, 100);
        return appId;
    }
    throw new Error("Steam could not start VacuumTube");
}

function promptInstallVacuumTube(onYes) {
    const title = "Install VacuumTube?";
    const body = "VacuumTube is not installed. VTPiP needs it to show YouTube in the corner. Install the YouTube TV app from Flathub now? Your system Flatpak will keep it updated — this plugin does not bundle it.";
    try {
        if (DFL.showModal && DFL.ConfirmModal) {
            DFL.showModal(e(DFL.ConfirmModal, {
                strTitle: title,
                strDescription: body,
                strOKButtonText: "Install",
                strCancelButtonText: "Not now",
                onOK: onYes,
            }));
            return;
        }
    } catch (err) {}
    onYes();
}

function Thumb({ id, src, w = 96, h = 54 }) {
    const url = src || (id ? "https://i.ytimg.com/vi/" + id + "/mqdefault.jpg" : "");
    return e("img", {
        src: url,
        alt: "",
        draggable: false,
        style: {
            width: w, height: h, objectFit: "cover", borderRadius: 6,
            background: "#111", flexShrink: 0, display: "block",
            pointerEvents: "none",
        },
    });
}

function Hint({ children }) {
    return e("div", { style: { fontSize: 11, lineHeight: 1.35, opacity: 0.62, margin: "4px 0 8px" } }, children);
}

function displaySize(st) {
    const w = Number(st && st.display_width) || 0;
    const h = Number(st && st.display_height) || 0;
    if (w >= 320 && h >= 240) return [w, h];
    try {
        const sw = window.screen && window.screen.width;
        const sh = window.screen && window.screen.height;
        if (sw >= 320 && sh >= 240) return [sw, sh];
    } catch (err) {}
    return [1280, 800];
}

function pipRect(sw, sh, size, corner) {
    const spec = STAMP_PX[size] || STAMP_PX.small;
    const tw = spec[0], th = spec[1];
    const margin = Math.max(12, Math.floor(Math.min(sw, sh) * 0.015));
    const scale = Math.min(1, (sw - 2 * margin) / tw, (sh - 2 * margin) / th);
    const w = Math.max(160, Math.floor(tw * scale));
    const h = Math.max(90, Math.floor(th * scale));
    const midY = Math.max(margin, Math.floor((sh - h) / 2));
    if (corner === "left-middle" || corner === "middle-left") return [margin, midY, w, h];
    if (corner === "right-middle" || corner === "middle-right") return [sw - w - margin, midY, w, h];
    if (corner === "top-left") return [margin, margin, w, h];
    if (corner === "top-right") return [sw - w - margin, margin, w, h];
    if (corner === "bottom-left") return [margin, sh - h - margin, w, h];
    return [sw - w - margin, sh - h - margin, w, h];
}

function handleAnchor(id) {
    if (id === "top-left") return { top: 5, left: 5 };
    if (id === "top-right") return { top: 5, right: 5 };
    if (id === "left-middle") return { top: "50%", left: 5, marginTop: -4 };
    if (id === "right-middle") return { top: "50%", right: 5, marginTop: -4 };
    if (id === "bottom-left") return { bottom: 5, left: 5 };
    return { bottom: 5, right: 5 };
}

function PosCell({ id, selected, onSelect }) {
    const [focused, setFocused] = useState(false);
    const label = POSITION_LABEL[id] || id;
    const pick = () => onSelect(id);
    return e(DFL.Focusable, {
        focusable: true,
        noFocusRing: true,
        onActivate: pick,
        onClick: pick,
        onOKButton: pick,
        onOKActionDescription: "Place here",
        onFocus: () => setFocused(true),
        onBlur: () => setFocused(false),
        onGamepadFocus: () => setFocused(true),
        onGamepadBlur: () => setFocused(false),
        style: { flex: 1, minWidth: 0, minHeight: 0, position: "relative" },
    },
        e("div", {
            onClick: pick,
            style: {
                position: "absolute",
                inset: 0,
                cursor: "pointer",
                background: selected
                    ? "rgba(61,220,132,0.10)"
                    : focused
                        ? "rgba(255,255,255,0.08)"
                        : "transparent",
                boxShadow: focused
                    ? "inset 0 0 0 2px #fff, inset 0 0 18px 2px rgba(61,220,132,0.45)"
                    : "none",
            },
        }, e("span", {
            style: {
                position: "absolute", width: 1, height: 1, overflow: "hidden",
                clip: "rect(0,0,0,0)",
            },
        }, label)),
        selected ? null : e("div", {
            style: {
                position: "absolute",
                width: 9,
                height: 9,
                borderRadius: 2,
                pointerEvents: "none",
                zIndex: 3,
                background: focused ? "#fff" : "rgba(255,255,255,0.40)",
                boxShadow: focused ? "0 0 6px rgba(255,255,255,0.8)" : "none",
                ...handleAnchor(id),
            },
        }),
    );
}

function PositionPreview({ corner, size, displayW, displayH, onChange }) {
    const dw = displayW;
    const dh = displayH;
    const rect = pipRect(dw, dh, size, corner);
    const sx = rect[0], sy = rect[1], sw = rect[2], sh = rect[3];
    return e("div", { style: { minWidth: 0, width: "100%" } },
        e("div", {
            style: { fontSize: 13, fontWeight: 650, margin: "2px 0 6px" },
        }, "Position"),
        e("div", {
            style: {
                position: "relative",
                width: "min(100%, calc(152px * " + dw + " / " + dh + "))",
                aspectRatio: dw + " / " + dh,
                margin: "0 auto 4px",
                background: "rgba(0,0,0,0.48)",
                borderRadius: 8,
                boxShadow: "inset 0 0 0 1px rgba(255,255,255,0.14)",
                overflow: "hidden",
            },
        },
            e("div", {
                style: {
                    position: "absolute",
                    left: (sx / dw * 100) + "%",
                    top: (sy / dh * 100) + "%",
                    width: (sw / dw * 100) + "%",
                    height: (sh / dh * 100) + "%",
                    background: "linear-gradient(180deg, rgba(255,90,90,0.95), rgba(190,28,28,0.92))",
                    borderRadius: 3,
                    pointerEvents: "none",
                    zIndex: 1,
                    boxShadow: "0 0 0 1px rgba(255,255,255,0.45)",
                    overflow: "hidden",
                    transition: "left .15s ease, top .15s ease, width .15s ease, height .15s ease",
                },
            },
                e("div", {
                    style: {
                        height: "18%",
                        maxHeight: 10,
                        background: "rgba(0,0,0,0.35)",
                    },
                }),
            ),
            e("div", {
                style: {
                    position: "absolute",
                    inset: 0,
                    display: "flex",
                    flexDirection: "column",
                    zIndex: 2,
                },
            }, POSITION_ROWS.map((row, ri) =>
                e(DFL.Focusable, {
                    key: "pos-row-" + ri,
                    noFocusRing: true,
                    "flow-children": "row",
                    style: { display: "flex", flex: 1, minHeight: 0, alignItems: "stretch" },
                }, row.map((id) => e(PosCell, {
                    key: id,
                    id,
                    selected: corner === id,
                    onSelect: onChange,
                }))),
            )),
        ),
        e("div", {
            style: {
                fontSize: 11,
                lineHeight: 1.35,
                opacity: 0.62,
                textAlign: "center",
                marginBottom: 4,
            },
        }, (POSITION_LABEL[corner] || corner) + "  ·  " + dw + "×" + dh),
        e(Hint, null, "D-pad around the preview, A to place."),
    );
}

function Btn({ children, onClick, disabled, grow, accent }) {
    return e(DFL.DialogButton, {
        disabled: !!disabled,
        onClick,
        style: {
            flex: grow ? 1 : undefined,
            minWidth: 0,
            minHeight: 36,
            padding: "6px 8px",
            fontSize: 13,
            fontWeight: 650,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            background: accent ? "rgba(255,77,77,0.22)" : undefined,
        },
    }, children);
}

function BackShell({ enabled, onBack, children }) {
    const onCancel = (ev) => {
        if (!enabled) return false;
        ev?.preventDefault?.();
        ev?.stopPropagation?.();
        ev?.stopImmediatePropagation?.();
        onBack();
        return true;
    };
    const attach = (el) => {
        if (!el || el.__vtpipBack) return;
        el.__vtpipBack = true;
        el.addEventListener("vgp_oncancel", (ev) => {
            if (onCancel(ev)) {
                ev.preventDefault();
                ev.stopPropagation();
            }
        }, true);
    };
    return e(DFL.Focusable, {
        noFocusRing: true,
        "flow-children": "column",
        onCancel: onCancel,
        onCancelButton: onCancel,
        onButtonDown: (ev) => { if (ev?.detail?.button === 2) onCancel(ev); },
        onCancelActionDescription: enabled ? "Back" : undefined,
        style: { minWidth: 0 },
    }, e("div", { ref: attach, style: { minWidth: 0 } }, children));
}

function Progress({ time, duration }) {
    const pct = duration > 0 ? Math.max(0, Math.min(100, (time / duration) * 100)) : 0;
    return e("div", { style: { minWidth: 0 } },
        e("div", {
            style: {
                height: 6, borderRadius: 99, background: "rgba(255,255,255,0.12)",
                overflow: "hidden", margin: "8px 0 4px",
            },
        }, e("div", { style: { width: pct + "%", height: "100%", background: RED, borderRadius: 99 } })),
        e("div", {
            style: {
                display: "flex", justifyContent: "space-between",
                fontSize: 11, fontVariantNumeric: "tabular-nums", opacity: 0.8,
            },
        }, e("span", null, fmtTime(time)), e("span", null, duration ? fmtTime(duration) : "--:--")),
    );
}

function Transport({ player, busy, onPrev, onPlay, onNext, onSkip }) {
    const paused = !player || player.paused || !player.hasVideo;
    return e("div", { style: { minWidth: 0 } },
        e(DFL.Focusable, {
            "flow-children": "row",
            style: { display: "flex", gap: 6, marginTop: 6 },
        },
            e(Btn, { grow: true, disabled: busy, onClick: onPrev }, "Prev"),
            e(Btn, { grow: true, disabled: busy, accent: true, onClick: onPlay }, paused ? "Play" : "Pause"),
            e(Btn, { grow: true, disabled: busy, onClick: onNext }, "Next"),
        ),
        e(DFL.Focusable, {
            "flow-children": "row",
            style: { display: "flex", gap: 6, marginTop: 6 },
        },
            e(Btn, { grow: true, disabled: busy, onClick: () => onSkip(-15) }, "−15s"),
            e(Btn, { grow: true, disabled: busy, onClick: () => onSkip(15) }, "+15s"),
        ),
    );
}

function NowPlaying({ player, busy, onPrev, onPlay, onNext, onSkip, onComments }) {
    if (!player || !player.videoId) {
        return e("div", { style: CARD },
            e("div", { style: { fontSize: 13, fontWeight: 650, marginBottom: 4 } }, "Now playing"),
            e("div", { style: { fontSize: 12, opacity: 0.7, lineHeight: 1.4 } },
                "Nothing is playing yet. Open Watch Later or Search, then press A on a video."),
        );
    }
    return e("div", { style: CARD },
        e("div", { style: { display: "flex", gap: 10, minWidth: 0 } },
            e(Thumb, { id: player.videoId, src: player.thumb, w: 108, h: 60 }),
            e("div", { style: { minWidth: 0, flex: 1 } },
                e("div", {
                    style: {
                        fontSize: 13, fontWeight: 700, lineHeight: 1.3,
                        overflow: "hidden", display: "-webkit-box",
                        WebkitLineClamp: 2, WebkitBoxOrient: "vertical",
                    },
                }, player.title || "YouTube"),
                player.channel ? e("div", {
                    style: { fontSize: 11, color: MUTED, marginTop: 3, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" },
                }, player.channel) : null,
            ),
        ),
        e(Progress, { time: player.time, duration: player.duration }),
        e(Transport, { player, busy, onPrev, onPlay, onNext, onSkip }),
        e("div", { style: { marginTop: 6 } },
            e(Btn, { grow: true, disabled: busy, onClick: onComments }, "Comments")),
    );
}

function VideoRow({ item, onPlay }) {
    const [focused, setFocused] = useState(false);
    return e(DFL.DialogButton, {
        onClick: () => onPlay(item),
        onFocus: () => setFocused(true),
        onBlur: () => setFocused(false),
        onGamepadFocus: () => setFocused(true),
        onGamepadBlur: () => setFocused(false),
        style: {
            minWidth: 0,
            width: "100%",
            marginBottom: 8,
            padding: 8,
            justifyContent: "flex-start",
            textAlign: "left",
            color: "#fff",
            background: focused ? "rgba(61,220,132,0.38)" : "rgba(0,0,0,0.22)",
            boxShadow: focused
                ? "0 0 0 3px #fff, 0 0 18px 3px rgba(61,220,132,0.9)"
                : "inset 0 0 0 1px rgba(255,255,255,0.08)",
            transform: focused ? "scale(1.02)" : "scale(1)",
            transition: "box-shadow .08s ease, transform .08s ease, background .08s ease",
            position: "relative",
            zIndex: focused ? 2 : 0,
            borderRadius: 10,
        },
    }, e("div", {
        style: {
            display: "flex", gap: 10, alignItems: "center",
            minWidth: 0, width: "100%", pointerEvents: "none",
        },
    },
        e(Thumb, { id: item.id, src: item.thumb }),
        e("div", { style: { minWidth: 0, flex: 1 } },
            e("div", {
                style: {
                    fontSize: 13, fontWeight: 750, lineHeight: 1.3,
                    overflow: "hidden", display: "-webkit-box",
                    WebkitLineClamp: 2, WebkitBoxOrient: "vertical",
                    whiteSpace: "normal", color: "#fff",
                },
            }, item.title || "Untitled"),
            e("div", {
                style: {
                    fontSize: 11, color: focused ? "#fff" : MUTED, marginTop: 3,
                    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                },
            }, [item.channel, item.length].filter(Boolean).join("  ·  ")),
        ),
        focused ? e("div", {
            style: {
                flexShrink: 0, fontSize: 12, fontWeight: 800, color: "#04140a",
                background: GREEN, borderRadius: 6, padding: "4px 8px",
            },
        }, "A  Play") : null,
    ));
}

function CompactNowPlaying({ player }) {
    if (!player || !player.videoId) return null;
    return e("div", {
        style: {
            ...CARD,
            display: "flex",
            gap: 10,
            alignItems: "center",
            padding: "8px 10px",
        },
    },
        e(Thumb, { id: player.videoId, src: player.thumb, w: 72, h: 40 }),
        e("div", { style: { minWidth: 0, flex: 1 } },
            e("div", {
                style: {
                    fontSize: 12, fontWeight: 650, overflow: "hidden",
                    textOverflow: "ellipsis", whiteSpace: "nowrap",
                },
            }, player.title || "YouTube"),
            e("div", { style: { fontSize: 11, color: MUTED, marginTop: 2 } },
                (player.paused ? "Paused  ·  " : "") + fmtTime(player.time) + (player.duration ? " / " + fmtTime(player.duration) : "")),
        ),
    );
}

function StatusStrip({ st, msg }) {
    const ready = !!(st && st.installed && st.running);
    const color = !st
        ? "#888"
        : !st.flatpak || !st.installed
            ? RED
            : ready
                ? GREEN
                : "#fc6";
    const label = !st
        ? "Checking…"
        : !st.flatpak
            ? "Flatpak missing"
            : !st.installed
                ? "VacuumTube not installed"
                : !st.running
                    ? "Start VacuumTube"
                    : "VacuumTube Ready";
    const extra = msg || (st && st.message) || "";
    return e("div", {
        style: {
            ...CARD,
            display: "flex",
            alignItems: "center",
            gap: 10,
            marginBottom: 8,
            padding: "8px 10px",
        },
    },
        e("div", {
            style: {
                width: 10,
                height: 10,
                borderRadius: 99,
                background: color,
                boxShadow: ready ? "0 0 8px " + GREEN : "none",
                flexShrink: 0,
            },
        }),
        e("div", { style: { minWidth: 0, flex: 1 } },
            e("div", { style: { fontSize: 13, fontWeight: 750, lineHeight: 1.2 } }, label),
            extra ? e("div", {
                style: {
                    fontSize: 11,
                    lineHeight: 1.35,
                    marginTop: 3,
                    color: extra.indexOf("✓") >= 0 ? GREEN : MUTED,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                },
            }, extra) : null,
        ),
    );
}

function CommentRow({ item }) {
    return e("div", {
        style: {
            padding: "8px 6px", borderRadius: 8, marginBottom: 6,
            background: "rgba(0,0,0,0.18)", minWidth: 0,
        },
    },
        e("div", { style: { display: "flex", justifyContent: "space-between", gap: 8, marginBottom: 4 } },
            e("span", { style: { fontSize: 12, fontWeight: 700, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } }, item.author || "YouTube"),
            e("span", { style: { fontSize: 10, opacity: 0.55, flexShrink: 0 } }, item.time || ""),
        ),
        e("div", { style: { fontSize: 12, lineHeight: 1.4, opacity: 0.92, whiteSpace: "pre-wrap", overflowWrap: "anywhere" } }, item.text || ""),
        item.likes ? e("div", { style: { fontSize: 10, opacity: 0.55, marginTop: 4 } }, item.likes + " likes") : null,
    );
}

function Content() {
    const [st, setSt] = useState(null);
    const [player, setPlayer] = useState(null);
    const [msg, setMsg] = useState("");
    const [busy, setBusy] = useState(false);
    const [game, setGame] = useState(runningApp());
    const [view, setView] = useState("home");
    const [query, setQuery] = useState("");
    const [items, setItems] = useState([]);
    const [comments, setComments] = useState([]);
    const [listTitle, setListTitle] = useState("");
    const listRef = useRef([]);
    const playLock = useRef(false);

    const refresh = async () => {
        try {
            const s = await call("status");
            setSt(s);
        } catch (err) {
            setMsg(String(err));
        }
    };

    const refreshPlayer = async () => {
        try {
            const p = await call("player_state");
            if (p && p.ok !== false) setPlayer(p);
            else if (p && p.running === false) setPlayer(null);
        } catch (err) {}
    };

    useEffect(() => {
        refresh();
        refreshPlayer();
        const id = window.setInterval(() => {
            setGame(runningApp());
            call("status").then(setSt).catch(() => {});
        }, 2000);
        const id2 = window.setInterval(refreshPlayer, 1000);
        return () => { window.clearInterval(id); window.clearInterval(id2); };
    }, []);

    useEffect(() => {
        if (!st) return;
        const hide = shouldHide(st, game);
        call("set_focus", hide, game ? game.name : "", game ? game.appid : 0).catch(() => {});
    }, [st && st.shortcut_appid, st && st.enabled, game && game.appid, game && game.name]);

    useEffect(() => {
        if (view !== "search" && view !== "wl") return;
        if (!items.length) return;
        const t = window.setTimeout(() => {
            const root = document.querySelector("[data-vtpip-list]");
            const btn = root && root.querySelector("button");
            if (btn && typeof btn.focus === "function") btn.focus();
        }, 150);
        return () => window.clearTimeout(t);
    }, [view, items]);

    const run = async (label, fn) => {
        if (busy) return;
        setBusy(true);
        setMsg(label + "…");
        try {
            const r = await fn();
            if (r && r.ok === false) setMsg(r.error || "Failed");
            else setMsg("");
            await refresh();
            await refreshPlayer();
            return r;
        } catch (err) {
            setMsg(String(err));
        } finally {
            setBusy(false);
        }
    };

    const needTube = async () => {
        if (st && st.running) return true;
        setMsg("Start VacuumTube from this menu first.");
        return false;
    };

    const openList = async (kind) => {
        if (!(await needTube())) return;
        setBusy(true);
        setMsg(kind === "wl" ? "Loading Watch Later…" : "Searching…");
        try {
            const r = kind === "wl"
                ? await call("watch_later")
                : await call("search", query);
            if (r && r.ok === false) {
                setMsg(r.error || "Failed");
                return;
            }
            const next = r.items || [];
            listRef.current = next;
            setItems(next);
            setListTitle(kind === "wl" ? "Watch Later" : ("Search  ·  " + (query || "").trim()));
            setView(kind === "wl" ? "wl" : "search");
            setMsg(next.length ? "" : (kind === "wl"
                ? "Watch Later is empty. Sign in inside VacuumTube if you have not already."
                : "No videos found."));
        } catch (err) {
            setMsg(String(err));
        } finally {
            setBusy(false);
        }
    };

    const playItem = async (item, index) => {
        if (playLock.current) return;
        if (!item || !item.id) {
            setMsg("That row has no video id.");
            return;
        }
        const live = await call("status").catch(() => st);
        if (live && !live.running) {
            setMsg("VacuumTube is not running. Go back and tap Start VacuumTube.");
            return;
        }
        playLock.current = true;
        setMsg("Playing “" + (item.title || item.id) + "”…");
        try {
            const r = await call("play_video", item.id, listRef.current, index);
            if (r && r.ok === false) {
                const err = String(r.error || "Play failed");
                if (/no page|refused|CDP|DevTools|Bad file/i.test(err)) {
                    setMsg("VacuumTube dropped. Go back and tap Start VacuumTube.");
                } else {
                    setMsg(err);
                }
                return;
            }
            setMsg("");
            setView("home");
            await refresh();
            await refreshPlayer();
        } catch (err) {
            setMsg(String(err));
        } finally {
            playLock.current = false;
        }
    };

    const openComments = async () => {
        if (!(await needTube())) return;
        setBusy(true);
        setMsg("Loading comments…");
        try {
            const r = await call("comments");
            if (r && r.ok === false) {
                setMsg(r.error || "Failed");
                return;
            }
            setComments(r.items || []);
            setView("comments");
            setMsg((r.items && r.items.length) ? "" : "No comments on this video (or YouTube hid them from TV).");
        } catch (err) {
            setMsg(String(err));
        } finally {
            setBusy(false);
        }
    };

    const doInstall = () => promptInstallVacuumTube(() => run("Install", () => call("install_vacuumtube")));
    const goHome = () => { setView("home"); setMsg(""); };

    const installed = !!(st && st.installed);
    const running = !!(st && st.running);
    const enabled = !!(st && st.enabled);
    const disp = displaySize(st);

    const listBody = e("div", { style: { minWidth: 0 } },
        e("div", { style: { display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 8, margin: "4px 0 6px" } },
            e("div", { style: { fontSize: 15, fontWeight: 750 } }, listTitle || "Videos"),
            e("div", { style: { fontSize: 11, opacity: 0.55 } }, "B  back"),
        ),
        view === "search" ? e("div", { style: { marginBottom: 8 } },
            e(DFL.TextField, {
                value: query,
                placeholder: "Search YouTube",
                onChange: (ev) => setQuery(ev?.target?.value ?? ev ?? ""),
                onKeyDown: (ev) => {
                    if (ev?.key === "Enter") {
                        ev.preventDefault?.();
                        openList("search");
                    }
                },
                style: { width: "100%", fontSize: 14 },
            }),
            e("div", { style: { marginTop: 6 } },
                e(Btn, { grow: true, disabled: busy || !String(query).trim(), onClick: () => openList("search") }, "Search")),
            e(Hint, null, "D-pad down onto a result, A to play. B goes back."),
        ) : e(Hint, null, "D-pad to a video, A to play. B returns to the main menu."),
        items.length
            ? e("div", { "data-vtpip-list": "1", style: { minWidth: 0 } },
                e(DFL.Focusable, {
                    "flow-children": "column",
                    style: { minWidth: 0, display: "flex", flexDirection: "column" },
                }, items.map((item, i) => e(VideoRow, { key: item.id + "-" + i, item, onPlay: () => playItem(item, i) }))))
            : e("div", { style: { fontSize: 12, opacity: 0.7, padding: "8px 2px" } }, busy ? "Loading…" : "No videos."),
    );

    const commentsBody = e("div", { style: { minWidth: 0 } },
        e("div", { style: { display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 8, margin: "4px 0 6px" } },
            e("div", { style: { fontSize: 15, fontWeight: 750 } }, "Comments"),
            e("div", { style: { fontSize: 11, opacity: 0.55 } }, "B  back"),
        ),
        e(Hint, null, "Pulled from the signed-in YouTube account in VacuumTube."),
        comments.length
            ? comments.map((c) => e(CommentRow, { key: c.id, item: c }))
            : e("div", { style: { fontSize: 12, opacity: 0.7 } }, busy ? "Loading…" : "No comments to show."),
    );

    const home = e(window.SP_REACT.Fragment, null,
        e(NowPlaying, {
            player, busy,
            onPrev: () => run("Prev", () => call("prev_video")),
            onPlay: () => run("Play", () => call("play_pause")),
            onNext: () => run("Next", () => call("next_video")),
            onSkip: (n) => run(n < 0 ? "Back 15" : "Ahead 15", () => call("skip", n)),
            onComments: openComments,
        }),
        e(DFL.PanelSection, { title: "Library" },
            e(DFL.PanelSectionRow, null,
                e(DFL.ButtonItem, {
                    layout: "below",
                    disabled: busy || !running,
                    onClick: () => openList("wl"),
                }, "Watch Later")),
            e(DFL.PanelSectionRow, null,
                e("div", { style: { minWidth: 0, width: "100%" } },
                    e(DFL.TextField, {
                        value: query,
                        placeholder: "Search YouTube",
                        onChange: (ev) => setQuery(ev?.target?.value ?? ev ?? ""),
                        onKeyDown: (ev) => {
                            if (ev?.key === "Enter") {
                                ev.preventDefault?.();
                                openList("search");
                            }
                        },
                        style: { width: "100%", fontSize: 14 },
                    }),
                    e("div", { style: { marginTop: 6 } },
                        e(Btn, {
                            grow: true,
                            disabled: busy || !running || !String(query).trim(),
                            onClick: () => openList("search"),
                        }, "Search")),
                    e(Hint, null, "A on the search box opens the Steam keyboard. D-pad down to pick a result."),
                )),
        ),
        !running ? e(DFL.PanelSection, { title: "VacuumTube" },
            !installed ? e(DFL.PanelSectionRow, null,
                e(DFL.ButtonItem, {
                    layout: "below",
                    disabled: busy || (st && st.installing),
                    onClick: doInstall,
                }, st && st.installing ? "Installing…" : "Install VacuumTube")) : null,
            installed ? e(DFL.PanelSectionRow, null,
                e(DFL.ButtonItem, {
                    layout: "below",
                    disabled: busy,
                    onClick: () => run("Start", () => launchVacuumTube(st)),
                }, "Start VacuumTube")) : null,
        ) : null,
        e(DFL.PanelSection, { title: "Picture in picture" },
            e(DFL.PanelSectionRow, null,
                e(DFL.ToggleField, {
                    label: "Show over game",
                    description: "Stamps YouTube over the game. Audio keeps going when you switch games.",
                    checked: enabled,
                    disabled: !installed,
                    onChange: (v) => {
                        if (v && !installed) {
                            doInstall();
                            return;
                        }
                        call("set_enabled", v).then(refresh).catch((err) => setMsg(String(err)));
                    },
                })),
            e(DFL.PanelSectionRow, null,
                e(PositionPreview, {
                    corner: (st && st.corner) || "bottom-right",
                    size: (st && st.size) || "small",
                    displayW: disp[0],
                    displayH: disp[1],
                    onChange: (pos) => {
                        call("set_layout", pos, undefined, undefined).then(refresh).catch((err) => setMsg(String(err)));
                    },
                })),
            e(DFL.PanelSectionRow, null,
                e(DFL.DropdownItem, {
                    label: "Size",
                    rgOptions: SIZES,
                    selectedOption: (st && st.size) || "small",
                    onChange: (v) => {
                        call("set_layout", undefined, v.data, undefined).then(refresh).catch((err) => setMsg(String(err)));
                    },
                })),
            e(DFL.PanelSectionRow, null,
                e(DFL.SliderField, {
                    label: "Opacity  " + ((st && st.opacity) || 90) + "%",
                    value: (st && st.opacity) || 90,
                    min: 20,
                    max: 100,
                    step: 5,
                    onChange: (v) => {
                        call("set_layout", undefined, undefined, v).then(refresh).catch((err) => setMsg(String(err)));
                    },
                    bottomSeparator: "none",
                })),
        ),
        e(DFL.PanelSection, { title: "Volume" },
            e(DFL.PanelSectionRow, null,
                e(DFL.ToggleField, {
                    label: "Mute YouTube",
                    checked: !!(st && st.muted),
                    onChange: (v) => {
                        call("set_audio", undefined, undefined, v).then(refresh).catch((err) => setMsg(String(err)));
                    },
                })),
            e(DFL.PanelSectionRow, null,
                e(DFL.SliderField, {
                    label: "YouTube  " + ((st && st.yt_volume) || 100) + "%",
                    value: (st && st.yt_volume) || 100,
                    min: 0,
                    max: 150,
                    step: 5,
                    onChange: (v) => {
                        call("set_audio", v, undefined, undefined).then(refresh).catch((err) => setMsg(String(err)));
                    },
                    bottomSeparator: "none",
                })),
            e(DFL.PanelSectionRow, null,
                e(DFL.SliderField, {
                    label: "Game  " + ((st && st.game_volume) || 100) + "%",
                    value: (st && st.game_volume) || 100,
                    min: 0,
                    max: 150,
                    step: 5,
                    onChange: (v) => {
                        call("set_audio", undefined, v, undefined).then(refresh).catch((err) => setMsg(String(err)));
                    },
                    bottomSeparator: "none",
                })),
        ),
    );

    const inner = view === "home" ? home
        : view === "comments" ? commentsBody
        : listBody;

    return e(BackShell, { enabled: view !== "home", onBack: goHome },
        e("div", { style: { minWidth: 0 } },
            e(StatusStrip, { st, msg }),
            view !== "home" ? e(CompactNowPlaying, { player }) : null,
            inner,
        ),
    );
}

function PipIcon() {
    return e("svg", {
        viewBox: "0 0 24 24", width: "1em", height: "1em", fill: "currentColor",
        xmlns: "http://www.w3.org/2000/svg",
    }, e("path", { d: "M3 5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2h-6v-2h6V5H5v4H3V5zm0 8h8v8H3v-8zm2 2v4h4v-4H5z" }));
}

var index = DFL.definePlugin(() => {
    let last = "";
    const pushFocus = () => {
        call("status").then((st) => {
            const g = runningApp();
            const hide = shouldHide(st, g);
            const k = String(hide) + "|" + (g ? g.appid : "") + "|" + (g ? g.name : "");
            if (k === last) return;
            last = k;
            call("set_focus", hide, g ? g.name : "", g ? g.appid : 0).catch(() => {});
        }).catch(() => {});
    };
    const iv = window.setInterval(pushFocus, 1500);
    setTimeout(pushFocus, 500);
    let unreg;
    try {
        unreg = window.SteamClient?.GameSessions?.RegisterForAppLifetimeNotifications?.(() => {
            setTimeout(pushFocus, 400);
        })?.unregister;
    } catch (err) {}
    return {
        title: e("div", { className: DFL.staticClasses.Title }, "VTPiP"),
        content: e(Content, null),
        icon: e(PipIcon, null),
        alwaysRender: true,
        onDismount() {
            window.clearInterval(iv);
            try { unreg && unreg(); } catch (err) {}
        },
    };
});

export { index as default };
