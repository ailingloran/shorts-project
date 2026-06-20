"""
OBS capture layer — drive OBS recording over its WebSocket so the user can
launch a replay and have the app record it, then edit.

The user launches the replay in WoWS (Steam DRM blocks us launching it). The app:
  - connects to OBS (Tools -> WebSocket Server Settings, port 4455),
  - starts/stops recording,
  - marks "battle started" (F9 / button) -> sets the battle offset so counters
    and auto-suggest sync with zero manual nudging,
  - marks cool moments (F8 / button),
  - on stop, hands the recording back as an editor source with the offset baked in.

Clock convention (matches the rest of the editor): battle_t = src_t + battle_offset.
At the moment battle starts, src_t = (battle_start - record_start), and battle_t = 0,
so battle_offset = -(battle_start - record_start)  (negative: loading precedes battle).
"""
from __future__ import annotations

import glob
import os
import time

try:
    import obsws_python as obsws
except Exception:                       # import error shouldn't break the app
    obsws = None


class CaptureManager:
    def __init__(self):
        self.client = None
        self.connected = False
        self.error = None
        self.record_dir = None
        self.record_start = None        # time.monotonic() at StartRecord
        self.battle_start = None        # time.monotonic() at "battle started"
        self.markers: list[dict] = []   # [{t_rec, label}]
        self.last_path = None
        self._hotkeys = False

    # --- connection ---
    def connect(self, host="localhost", port=4455, password="") -> dict:
        if obsws is None:
            self.error = "obsws-python not installed"
            return {"connected": False, "error": self.error}
        try:
            self.client = obsws.ReqClient(host=host, port=int(port),
                                          password=password or "", timeout=3)
            v = self.client.get_version()
            try:
                self.record_dir = self.client.get_record_directory().record_directory
            except Exception:
                self.record_dir = None
            self.connected = True
            self.error = None
            return {"connected": True,
                    "obs_version": getattr(v, "obs_version", "?"),
                    "ws_version": getattr(v, "obs_web_socket_version", "?"),
                    "record_dir": self.record_dir}
        except Exception as e:
            self.connected = False
            self.client = None
            self.error = f"{type(e).__name__}: {e}"
            return {"connected": False, "error": self.error,
                    "hint": "Is OBS open with Tools → WebSocket Server enabled on this port/password?"}

    # --- offset / markers ---
    def battle_offset(self) -> float:
        if self.record_start is None or self.battle_start is None:
            return 0.0
        return round(-(self.battle_start - self.record_start), 2)

    def mark_battle_start(self) -> dict:
        if self.record_start is None:
            return {"ok": False, "error": "not recording"}
        self.battle_start = time.monotonic()
        return {"ok": True, "battle_offset": self.battle_offset()}

    def mark(self, label="moment") -> dict:
        if self.record_start is None:
            return {"ok": False, "error": "not recording"}
        self.markers.append({"t_rec": round(time.monotonic() - self.record_start, 2), "label": label})
        return {"ok": True, "marker": self.markers[-1]}

    # --- recording ---
    def start(self) -> dict:
        if not self.connected:
            return {"ok": False, "error": "not connected"}
        try:
            self.client.start_record()
        except Exception as e:
            return {"ok": False, "error": str(e)}
        self.record_start = time.monotonic()
        self.battle_start = None
        self.markers = []
        self._register_hotkeys()
        return {"ok": True}

    def stop(self) -> dict:
        if not self.connected:
            return {"ok": False, "error": "not connected"}
        try:
            resp = self.client.stop_record()
            path = getattr(resp, "output_path", None)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        if not path:
            path = self._newest_recording()
        self._unregister_hotkeys()
        offset = self.battle_offset()
        markers = list(self.markers)
        self.last_path = path
        self.record_start = None
        return {"ok": True, "path": (path or "").replace("\\", "/"),
                "battle_offset": offset, "markers": markers}

    def status(self) -> dict:
        st = {"connected": self.connected, "recording": self.record_start is not None,
              "error": self.error, "record_dir": self.record_dir,
              "battle_offset": self.battle_offset(), "markers": self.markers,
              "has_battle_start": self.battle_start is not None}
        if self.connected and self.client:
            try:
                rs = self.client.get_record_status()
                st["recording"] = bool(getattr(rs, "output_active", st["recording"]))
                st["rec_duration_s"] = round(getattr(rs, "output_duration", 0) / 1000.0, 1)
            except Exception as e:
                st["error"] = str(e)
        return st

    def _newest_recording(self):
        if not self.record_dir:
            return None
        files = []
        for ext in ("*.mkv", "*.mp4", "*.mov"):
            files += glob.glob(os.path.join(self.record_dir, ext))
        return max(files, key=os.path.getmtime) if files else None

    # --- global hotkeys (best-effort; UI buttons are the reliable path) ---
    def _register_hotkeys(self):
        try:
            import keyboard
            keyboard.add_hotkey("f9", lambda: self.mark_battle_start())
            keyboard.add_hotkey("f8", lambda: self.mark("moment"))
            self._hotkeys = True
        except Exception:
            self._hotkeys = False

    def _unregister_hotkeys(self):
        if self._hotkeys:
            try:
                import keyboard
                keyboard.remove_all_hotkeys()
            except Exception:
                pass
            self._hotkeys = False


MANAGER = CaptureManager()
