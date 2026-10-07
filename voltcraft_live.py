#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Voltcraft Live contributors
#
# This file is part of Voltcraft Live.
# Voltcraft Live is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Voltcraft Live is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Voltcraft Live. If not, see <https://www.gnu.org/licenses/>.

"""Voltcraft DSO1084F: continuous USB waveform view in a local browser.

Python standard library only. Live waveform view, serialized SCPI remote control,
persistent editable profiles, and mouse-wheel zoom. Vertical graph values remain
signed sample codes, not calibrated voltages. Public ALL transfer was verified on
this DSO1084F firmware 2.0.0. New remote actions need hardware validation.
"""
import argparse
import datetime as dt
import fcntl
import http.server
import json
import math
import os
from pathlib import Path
import re
import queue
import tempfile
import secrets
import struct
import threading
import time
import webbrowser

__version__ = "0.1.0"

MAX_BYTES = 8 * 1024 * 1024
COMMANDS = {"display": ":WAVeform:DATA:DISP?", "legacy": ":WAVeform:DATA:ALL?"}
FLOAT = rb"[+-]?\d\.\d+e[+-]\d{2,3}"
META = re.compile(rb"^([01])([01])([ +\-\d]{16})(" + FLOAT + rb")(" + FLOAT
                  + rb")(" + FLOAT + rb")(" + FLOAT + rb")([01]{4})("
                  + FLOAT + rb")(\d{6})(" + FLOAT + rb")(" + FLOAT + rb")")


class NoMeasurement(ValueError):
    """The instrument answered with its valid empty-data sentinel."""


def metadata(data):
    if len(data) != 99:
        raise ValueError("Der Datenkopf hat nicht 99 Bytes.")
    m = META.match(data)
    if not m:
        raise ValueError("Unbekannter Messdatenkopf; Rohdaten in Diagnose enthalten.")
    g = m.groups()
    rate, factor = float(g[8]), int(g[9])
    if not math.isfinite(rate) or rate <= 0 or not 0 < factor <= 999999:
        raise ValueError("Ungültige Abtastrate im Datenkopf.")
    return {"running": g[0] == b"1", "triggered": g[1] == b"1",
            "enabled": [i + 1 for i, on in enumerate(g[7]) if on == 49],
            "sample_rate": rate, "sampling_factor": factor,
            "dt": factor / rate, "scales_reported": [float(s) for s in g[3:7]],
            "channel_offsets_reported": [int(g[2][i:i+4]) for i in range(0, 16, 4)],
            "trigger_time_reported": float(g[10]), "start_time_reported": float(g[11])}


def packet_parts(packet, mode):
    packet = bytes(packet)
    if len(packet) < 11 or packet[:2] != b"#9" or not packet[2:11].isdigit():
        raise ValueError("USB-Antwort ohne erwarteten Datenkopf.")
    n = int(packet[2:11])
    if n == 0:
        raise NoMeasurement("Das Oszi liefert momentan keine Messdaten. Prüfe Run/Stop und die aktiven Kanäle.")
    # SCPI implementations may append a newline outside the announced packet.
    if len(packet) > n and packet[n:] in (b"\n", b"\r\n"):
        packet = packet[:n]
    if n != len(packet) or n < 29:
        raise ValueError("USB-Paket unvollständig oder Länge unplausibel.")
    if mode == "display" and n == 128 and packet[11:13] in (b"00", b"01", b"10", b"11"):
        # FW 2.0.0: first packet places run/trigger before total/offset;
        # subsequent packets use the traditional 29-byte wrapper.
        if packet[13:31].isdigit() and int(packet[22:31]) == 0:
            head = packet[11:13] + packet[31:128]
            metadata(head)  # distinguish an initial header from continuation data
            return int(packet[13:22]), 0, head
    if not packet[11:29].isdigit():
        raise ValueError("Die USB-Längenfelder sind unlesbar.")
    return int(packet[11:20]), int(packet[20:29]), packet[29:]


def receive_frame(query, mode, diagnostic):
    diagnostic["streams"] = []
    deadline = time.monotonic() + 15
    for attempt in range(3):
        stream = {"packets": []}
        diagnostic["streams"].append(stream)
        total = first = expected = None
        chunks = []
        for _ in range(2048):
            if time.monotonic() > deadline:
                raise TimeoutError("Messdatenübertragung dauert länger als 15 Sekunden.")
            p = query(COMMANDS[mode])
            stream["packets"].append({"hex": p.hex(), "observed_length": len(p)})
            announced, offset, payload = packet_parts(p, mode)
            if total is None:
                total, first, expected = announced, offset, offset
                stream.update(total_length=total, first_offset=first)
                if not 99 <= total <= MAX_BYTES:
                    raise ValueError("Unplausible Gesamtlänge der Messdaten.")
            if announced != total or offset != expected or not payload:
                raise ValueError("Messdaten fehlen, sind doppelt oder gehören zu einer anderen Aufnahme.")
            expected += len(payload)
            if expected > total:
                raise ValueError("Messdaten überschreiten die angekündigte Gesamtlänge.")
            chunks.append(payload)
            stream["end_offset"] = expected
            if expected == total:
                stream["reached_end"] = True
                break
        else:
            raise ValueError("Zu viele USB-Pakete.")
        if first != 0:
            continue  # finish a transfer left pending by an earlier program
        frame = b"".join(chunks)
        if len(frame) != total:
            raise ValueError("Die Aufnahme enthält eine Lücke.")
        diagnostic.update(frame_hex=frame.hex(), frame_length=len(frame),
                          capture_complete=True, resynchronization_count=attempt)
        return frame
    raise ValueError("Keine vollständige Aufnahme ab dem ersten Byte empfangen.")


def receive_auto(query, mode, diagnostic, allow_fallback):
    diagnostic["query"] = COMMANDS[mode]
    try:
        return receive_frame(query, mode, diagnostic), mode
    except NoMeasurement:
        if mode != "display" or not allow_fallback:
            raise
        diagnostic["display_attempt"] = {
            "query": COMMANDS[mode], "streams": diagnostic.get("streams", []),
            "reason": "Die Display-Abfrage lieferte ein leeres Datenpaket."}
        diagnostic["query"] = COMMANDS["legacy"]
        # This public command is also registered in firmware 2.0.0. A valid
        # empty response needs no USB reconnect or instrument reset.
        return receive_frame(query, "legacy", diagnostic), "legacy"


def view_frame(frame):
    meta = metadata(frame[:99])
    enabled = meta["enabled"]
    data = frame[99:]
    if not enabled or not data or len(data) % len(enabled):
        raise ValueError("Messwerte lassen sich nicht den aktiven Kanälen zuordnen.")
    n = len(data) // len(enabled)
    if n > 1000000:
        raise ValueError("Zu viele Werte für die Liveanzeige.")
    channels = []
    for i, channel in enumerate(enabled):
        raw = data[i*n:(i+1)*n]
        values = [v if v < 128 else v - 256 for v in raw]
        # Keep all supported 4k/16k/32k/64k records so mouse zoom exposes
        # actual samples. Preserve extrema for larger, unexpected records.
        if n <= 64000:
            points = list(enumerate(values))
        else:
            points = []
            step = math.ceil(n / 1800)
            for start in range(0, n, step):
                indices = range(start, min(n, start + step))
                low = min(indices, key=values.__getitem__)
                high = max(indices, key=values.__getitem__)
                points.extend((j, values[j]) for j in sorted({low, high}))
        channels.append({"channel": channel, "points": points, "minimum": min(values),
                         "maximum": max(values), "count": n})
    return {**meta, "channels": channels, "count_per_channel": n,
            "duration": (n - 1) * meta["dt"], "amplitude_unit": "signed_sample_code"}


def finite(value, label, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{label}: Wert außerhalb des zulässigen Bereichs.")
    return float(value)


def choice(value, allowed, label):
    if value not in allowed:
        raise ValueError(f"{label}: ungültige Auswahl.")
    return value


def normalize_settings(value):
    if not isinstance(value, dict) or set(value) - {"channels", "timebase", "trigger"}:
        raise ValueError("Ungültige Geräteeinstellungen.")
    out = {}
    for section, raw in value.items():
        if not isinstance(raw, dict):
            raise ValueError("Ungültige Einstellungsgruppe.")
        if section == "channels":
            channels = {}
            for channel, fields in raw.items():
                if channel not in ("1", "2", "3", "4") or not isinstance(fields, dict):
                    raise ValueError("Ungültiger Kanal.")
                allowed = {"enabled", "coupling", "probe", "scale", "offset", "bwlimit", "invert"}
                if set(fields) - allowed:
                    raise ValueError("Unbekannte Kanaleinstellung.")
                clean = {}
                for name, val in fields.items():
                    if name in ("enabled", "bwlimit", "invert"):
                        if type(val) is not bool:
                            raise ValueError("Kanal-Schalter muss ein Wahrheitswert sein.")
                        clean[name] = val
                    elif name == "coupling":
                        clean[name] = choice(val, ("AC", "DC", "GND"), "Kopplung")
                    elif name == "probe":
                        if type(val) is not int:
                            raise ValueError("Ungültiger Tastkopffaktor.")
                        clean[name] = choice(val, (1, 10, 100, 1000), "Tastkopf")
                    elif name == "scale":
                        scale = finite(val, "V/div", 0.0005, 10000)
                        mantissa = scale / 10 ** math.floor(math.log10(scale))
                        if not any(math.isclose(mantissa, x, rel_tol=1e-7) for x in (1, 2, 5)):
                            raise ValueError("V/div muss einem 1–2–5-Schritt entsprechen.")
                        clean[name] = scale
                    else:
                        clean[name] = finite(val, "Offset", -30000, 30000)
                if clean:
                    channels[channel] = clean
            if channels:
                out[section] = channels
        else:
            allowed = {"scale", "position"} if section == "timebase" else {"sweep", "source", "slope", "level"}
            if set(raw) - allowed:
                raise ValueError("Unbekannte Zeitbasis-/Trigger-Einstellung.")
            clean = {}
            for name, val in raw.items():
                if section == "timebase":
                    clean[name] = finite(val, "Zeitbasis", 2e-9 if name == "scale" else -1000,
                                         100 if name == "scale" else 1000)
                    if name == "scale":
                        mantissa = clean[name] / 10 ** math.floor(math.log10(clean[name]))
                        if not any(math.isclose(mantissa, x, rel_tol=1e-7) for x in (1, 2, 5)):
                            raise ValueError("Die Zeitbasis muss einem 1–2–5-Schritt entsprechen.")
                elif name == "sweep":
                    clean[name] = choice(val, ("AUTO", "NORMAL", "SINGLE"), "Triggerbetrieb")
                elif name == "source":
                    clean[name] = choice(val, ("CHANnel1", "CHANnel2", "CHANnel3", "CHANnel4", "EXT"), "Triggerquelle")
                elif name == "slope":
                    clean[name] = choice(val, ("RISING", "FALLING", "EITHER"), "Triggerflanke")
                else:
                    clean[name] = finite(val, "Triggerpegel", -30000, 30000)
            if clean:
                out[section] = clean
    return out


def normalize_view(raw):
    if not isinstance(raw, dict):
        raise ValueError("Ungültige Anzeigeeinstellungen.")
    out = {"x_zoom": finite(raw.get("x_zoom", 1), "Zeitzoom", 1, 512),
           "x_start": finite(raw.get("x_start", 0), "Zeitausschnitt", 0, 1),
           "y_zoom": finite(raw.get("y_zoom", 1), "Amplitudenzoom", 1, 64),
           "y_center": finite(raw.get("y_center", 0), "Amplitudenmitte", -128, 127)}
    out["x_start"] = min(out["x_start"], 1 - 1 / out["x_zoom"])
    shown = raw.get("shown", [True]*4)
    if not isinstance(shown, list) or len(shown) != 4 or any(type(x) is not bool for x in shown):
        raise ValueError("Ungültige Kanalauswahl für die Anzeige.")
    out["shown"] = shown[:]
    return out


def normalize_profile(raw):
    if not isinstance(raw, dict):
        raise ValueError("Ungültiges Profil.")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 80 or any(ord(c) < 32 for c in name):
        raise ValueError("Profilname: 1 bis 80 sichtbare Zeichen.")
    return {"name": name.strip(), "settings": normalize_settings(raw.get("settings", {})),
            "view": normalize_view(raw.get("view", {}))}


class Profiles:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()

    def read(self):
        if not self.path.exists():
            return []
        if self.path.stat().st_size > 1024*1024:
            raise ValueError("Die Profildatei ist zu groß.")
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("format") != "voltcraft-live-profiles-v1":
            raise ValueError("Unbekanntes Profilformat.")
        raw = value.get("profiles")
        if not isinstance(raw, list) or len(raw) > 100:
            raise ValueError("Ungültige Profilliste.")
        result = [normalize_profile(p) for p in raw]
        if len({p["name"] for p in result}) != len(result):
            raise ValueError("Die Profildatei enthält doppelte Namen.")
        return result

    def listing(self):
        with self.lock:
            return self.read()

    def change(self, action, body):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Coordinate multiple instances as well as browser requests. Read first;
        # a malformed existing file is never silently replaced with an empty one.
        with self.lock, (self.path.parent / "profiles.lock").open("a") as lockfile:
            fcntl.flock(lockfile, fcntl.LOCK_EX)
            profiles = self.read()
            if action == "save":
                incoming = [normalize_profile(body.get("profile"))]
            elif action == "import":
                bundle = body.get("bundle")
                if not isinstance(bundle, dict) or bundle.get("format") != "voltcraft-live-profiles-v1":
                    raise ValueError("Bitte einen Voltcraft-Profil-Export auswählen.")
                raw = bundle.get("profiles")
                if not isinstance(raw, list) or not 1 <= len(raw) <= 100:
                    raise ValueError("Ungültige Anzahl importierter Profile.")
                incoming = [normalize_profile(p) for p in raw]
            elif action == "delete":
                name = body.get("name")
                if not isinstance(name, str) or name not in {p["name"] for p in profiles}:
                    raise ValueError("Profil nicht gefunden.")
                profiles = [p for p in profiles if p["name"] != name]
                incoming = []
            else:
                raise ValueError("Unbekannte Profilaktion.")
            known = {p["name"] for p in profiles}
            for p in incoming:
                if p["name"] in known and not body.get("overwrite", False):
                    raise ValueError(f"Profil „{p['name']}“ existiert bereits. Überschreiben ausdrücklich auswählen.")
                profiles = [old for old in profiles if old["name"] != p["name"]]
                profiles.append(p)
                known.add(p["name"])
            if len(profiles) > 100:
                raise ValueError("Es sind höchstens 100 Profile möglich.")
            profiles.sort(key=lambda p: p["name"].casefold())
            data = {"format": "voltcraft-live-profiles-v1", "profiles": profiles}
            tmp = None
            try:
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent,
                                                 prefix="profiles-", suffix=".tmp", delete=False) as file:
                    tmp = Path(file.name)
                    json.dump(data, file, ensure_ascii=False, indent=2, allow_nan=False)
                    file.write("\n")
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(tmp, self.path)
                tmp = None
                directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                if tmp:
                    tmp.unlink(missing_ok=True)
            return profiles


CHANNEL_COMMANDS = {"enabled": "DISPlay", "coupling": "COUPling", "probe": "PROBe",
                    "scale": "SCALe", "offset": "OFFSet", "bwlimit": "BWLimit", "invert": "INVert"}
TIME_COMMANDS = {"scale": "SCALe", "position": "POSition"}
TRIGGER_COMMANDS = {"sweep": "SWEep", "source": "EDGe:SOURce", "slope": "EDGe:SLOPe", "level": "EDGe:LEVel"}


def decode_setting(data, name):
    raw = data.decode("ascii", errors="strict").strip("\x00 \r\n\t").strip('"')
    if name in ("enabled", "bwlimit", "invert", "running"):
        if raw.upper() not in ("0", "1", "OFF", "ON"):
            raise ValueError(f"Ungültige Schalterantwort: {raw!r}")
        return raw.upper() in ("1", "ON")
    if name in ("coupling", "sweep", "slope", "mode"):
        return raw.upper()
    if name == "source":
        m = re.fullmatch(r"CHAN(?:NEL)?([1-4])", raw, re.I)
        return f"CHANnel{m[1]}" if m else raw.upper()
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError("Nicht endliche Geräteantwort.")
    if name == "scale" and 0 < value < 1e-300:
        # Firmware printf bug: integer microvolts reinterpreted as a double.
        # The setting query has more digits than the waveform metadata.
        value = struct.unpack("<Q", struct.pack("<d", value))[0] / 1e6
        candidates = [m * 10.0**e for e in range(-5, 5) for m in (1, 2, 5)]
        nearest = min(candidates, key=lambda x: abs(x-value))
        if not math.isclose(value, nearest, rel_tol=0.005):
            raise ValueError("V/div-Rückmeldung kann nicht zuverlässig dekodiert werden.")
        value = nearest
    if name == "probe":
        if value not in (1, 10, 100, 1000):
            raise ValueError("Ungültiger Tastkopffaktor in der Geräteantwort.")
        return int(value)
    return value


def setting_targets(settings):
    for channel, fields in settings.get("channels", {}).items():
        for name in ("probe", "coupling", "bwlimit", "invert", "scale", "offset", "enabled"):
            if name in fields:
                yield ("channels", channel, name), f":CHANnel{channel}:{CHANNEL_COMMANDS[name]}", fields[name]
    for name in ("scale", "position"):
        if name in settings.get("timebase", {}):
            yield ("timebase", name), f":TIMebase:{TIME_COMMANDS[name]}", settings["timebase"][name]
    for name in ("source", "slope", "level", "sweep"):
        if name in settings.get("trigger", {}):
            yield ("trigger", name), f":TRIGger:{TRIGGER_COMMANDS[name]}", settings["trigger"][name]


def put_setting(result, path, value):
    node = result
    for part in path[:-1]:
        node = node.setdefault(part, {})
    node[path[-1]] = value


def read_settings(usb, wanted=None, progress=None):
    if wanted is None:
        wanted = {"channels": {str(i): {k: None for k in CHANNEL_COMMANDS} for i in range(1, 5)},
                  "timebase": {k: None for k in TIME_COMMANDS},
                  "trigger": {k: None for k in TRIGGER_COMMANDS}}
    result = {}
    targets = list(setting_targets(wanted))
    for index, (path, command, _) in enumerate(targets):
        if progress:
            progress(f"Einstellungen einlesen: {index+1}/{len(targets)}")
        put_setting(result, path, decode_setting(usb.query(command + "?"), path[-1]))
    if "trigger" in result:
        result["trigger_mode"] = decode_setting(usb.query(":TRIGger:MODE?"), "mode")
    return result


def apply_settings(usb, settings, progress=None):
    targets = list(setting_targets(settings))
    sent = []
    if any(k in settings.get("trigger", {}) for k in ("source", "slope", "level")):
        usb.write(":TRIGger:MODE EDGE")
        sent.append(":TRIGger:MODE EDGE")
    for index, (_, command, value) in enumerate(targets):
        if progress:
            progress(f"Einstellungen senden: {index+1}/{len(targets)}")
        if type(value) is bool:
            argument = "ON" if value else "OFF"
        elif type(value) in (float, int):
            argument = format(value, ".12g")
        else:
            argument = value
        wire = command + " " + argument
        usb.write(wire)
        sent.append(wire)
    # No write is retried automatically: a timeout may occur after a setting
    # has already changed. Read-back is the only acknowledgement available.
    actual = read_settings(usb, settings, progress)
    mismatches = []
    for path, command, expected in targets:
        node = actual
        for part in path:
            node = node[part]
        match = (math.isclose(node, expected, rel_tol=0.005, abs_tol=1e-9)
                 if type(expected) in (int, float) else node == expected)
        if not match:
            mismatches.append(f"{command}: Soll {expected}, Gerät meldet {node}")
    if "trigger_mode" in actual and any(k in settings.get("trigger", {}) for k in ("source", "slope", "level")):
        if actual["trigger_mode"] != "EDGE":
            mismatches.append("Das Gerät hat den Flankentrigger nicht übernommen.")
    return {"settings": actual, "sent": sent, "mismatches": mismatches}


class USB:
    def __init__(self, path):
        self.fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Linux usb/tmc.h: _IOW(91, 10, __u32), milliseconds.
            fcntl.ioctl(self.fd, 0x40045B0A, struct.pack("=I", 2000))
        except BaseException:
            os.close(self.fd)
            raise

    def write(self, command):
        wire = (command + "\n").encode("ascii")
        if os.write(self.fd, wire) != len(wire):
            raise OSError("USB-Befehl konnte nicht vollständig gesendet werden.")

    def query(self, command):
        self.write(command)
        return os.read(self.fd, MAX_BYTES)

    def close(self):
        os.close(self.fd)


class Live:
    def __init__(self, args):
        self.args = args
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.active = threading.Event()
        self.active.set()
        self.wake = threading.Event()
        self.requests = queue.Queue(maxsize=8)
        self.jobs = {}
        self.last_job = None
        self.connected = False
        self.settings = {}
        self.settings_updated = None
        self.frame = None
        self.diagnostic = None
        self.error = ""
        self.identity = "Verbindung wird aufgebaut"
        self.sequence = 0
        self.fps = 0
        self.updated = 0
        self.mode = ""
        self.replay = json.loads(args.replay.read_text()) if args.replay else None
        self.demo_settings = {"channels": {str(i): {"enabled": i <= 2, "coupling": "DC", "probe": 1,
                              "scale": 1.0, "offset": 0.0, "bwlimit": False, "invert": False} for i in range(1, 5)},
                              "timebase": {"scale": 0.0002, "position": 0.0},
                              "trigger": {"sweep": "AUTO", "source": "CHANnel1", "slope": "RISING", "level": 0.0}}
        self.demo_running = True
        self.thread = threading.Thread(target=self.run, daemon=True)

    def submit(self, body):
        if not isinstance(body, dict):
            raise ValueError("Ungültige Fernsteueranfrage.")
        action = body.get("action")
        if action not in ("read", "apply", "run", "stop", "single", "auto", "force", "unlock"):
            raise ValueError("Unbekannte Geräteaktion.")
        settings = normalize_settings(body.get("settings", {})) if action == "apply" else {}
        if action == "apply" and not settings:
            raise ValueError("Bitte mindestens eine Einstellung auswählen.")
        with self.lock:
            if self.replay:
                raise ValueError("Bei gespeicherten Aufnahmen ist die Fernsteuerung deaktiviert.")
            if not self.args.demo and not self.connected:
                raise ValueError("Das Oszi ist noch nicht verbunden.")
            job_id = secrets.token_hex(8)
            if len(self.jobs) >= 32:
                for old, job in list(self.jobs.items()):
                    if job["status"] in ("done", "error"):
                        del self.jobs[old]
                        break
            self.jobs[job_id] = {"id": job_id, "action": action, "status": "queued", "message": "Wartet auf freien USB-Zugriff"}
            try:
                self.requests.put_nowait((job_id, action, settings))
            except queue.Full:
                del self.jobs[job_id]
                raise ValueError("Die Steuerwarteschlange ist voll.")
            self.last_job = job_id
        self.wake.set()
        return job_id

    def job(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise ValueError("Steuerauftrag nicht gefunden.")
            return json.loads(json.dumps(self.jobs[job_id]))

    def progress(self, job_id, message):
        with self.lock:
            self.jobs[job_id].update(status="running", message=message)

    def perform(self, usb, job_id, action, settings):
        progress = lambda message: self.progress(job_id, message)
        progress("Geräteaktion wird ausgeführt")
        try:
            if action == "unlock":
                self.active.clear()
            if self.args.demo:
                if action == "apply":
                    for section, fields in settings.items():
                        if section == "channels":
                            for ch, values in fields.items():
                                self.demo_settings[section][ch].update(values)
                        else:
                            self.demo_settings[section].update(fields)
                elif action == "stop":
                    self.demo_running = False
                elif action in ("run", "single", "auto", "force"):
                    self.demo_running = True
                    if action == "single":
                        self.demo_settings["trigger"]["sweep"] = "SINGLE"
                result = {"settings": json.loads(json.dumps(self.demo_settings)), "sent": [], "mismatches": []}
                message = "DEMO: simulierte Einstellungen aktualisiert."
            elif action == "unlock":
                usb.write(":SYSTem:LOCKed OFF")
                # LOCKed? would lock the keys again before reporting ON.
                # End with the unlock write and keep acquisition polling paused.
                result = {"sent": [":SYSTem:LOCKed OFF"], "mismatches": []}
                message = "Entsperrbefehl gesendet. Übertragung pausiert; das Bedienfeld kann wieder am Oszi verwendet werden. Mit „Übertragung fortsetzen“ kommt die Livekurve zurück."
            elif action == "read":
                result = {"settings": read_settings(usb, progress=progress), "sent": [], "mismatches": []}
                message = "Geräteeinstellungen eingelesen."
            elif action == "apply":
                result = apply_settings(usb, settings, progress)
                message = ("Einstellungen gesendet und vom Gerät zurückgelesen." if not result["mismatches"]
                           else "Einige Einstellungen wurden vom Gerät nicht übernommen: " + "; ".join(result["mismatches"]))
            else:
                commands = {"run": [":RUNning ON"], "stop": [":STOP"],
                            "single": [":TRIGger:SWEep SINGle", ":RUNning ON"],
                            "auto": [":AUToset"], "force": [":TRIGger:FORCe"]}[action]
                for command in commands:
                    usb.write(command)
                result = {"sent": commands, "mismatches": []}
                if action in ("run", "stop"):
                    running = decode_setting(usb.query(":RUNning?"), "running")
                    result["running"] = running
                    if running != (action == "run"):
                        result["mismatches"].append("Run/Stop-Rückmeldung stimmt nicht mit dem Auftrag überein.")
                elif action == "single":
                    sweep = decode_setting(usb.query(":TRIGger:SWEep?"), "sweep")
                    result["settings"] = {"trigger": {"sweep": sweep}}
                    if sweep != "SINGLE":
                        result["mismatches"].append("Das Gerät meldet keinen Single-Triggerbetrieb.")
                elif action == "auto":
                    progress("Auto Scale läuft; anschließend Einstellungen einlesen")
                    self.stop.wait(1.0)
                    result["settings"] = read_settings(usb, progress=progress)
                message = "Geräteaktion gesendet. Die nächste Aufnahme zeigt den aktuellen Zustand."
                if result["mismatches"]:
                    message = "; ".join(result["mismatches"])
            with self.lock:
                if "settings" in result:
                    # Keep requested/read settings separate from waveform calibration.
                    self.settings = result["settings"]
                    self.settings_updated = dt.datetime.now().astimezone().isoformat()
                self.jobs[job_id].update(status="error" if result["mismatches"] else "done",
                                         message=message, result=result)
            return True
        except Exception as exc:
            with self.lock:
                self.jobs[job_id].update(status="error", message=f"Geräteaktion abgebrochen: {exc}. Bereits gesendete Einstellungen können wirksam sein; erneut einlesen.")
            raise

    def publish(self, frame, diagnostic, elapsed):
        view = view_frame(frame)
        with self.lock:
            self.sequence += 1
            if self.last_job:
                diagnostic["last_control"] = self.jobs.get(self.last_job)
            self.frame, self.diagnostic = view, diagnostic
            self.updated = time.time()
            self.fps = 1 / max(elapsed, self.args.interval)
            self.error = ""

    def run(self):
        usb = None
        one_capture = False
        try:
            while not self.stop.is_set():
                if not self.active.is_set() and self.requests.empty() and not one_capture:
                    self.wake.wait(0.15)
                    self.wake.clear()
                    continue
                started = time.monotonic()
                diagnostic = {"format": "voltcraft-live-capture-v1", "capture_complete": False,
                              "captured_at": dt.datetime.now().astimezone().isoformat(),
                              "device": self.args.device, "amplitude_calibrated": False}
                try:
                    if self.replay:
                        frame = bytes.fromhex(self.replay["frame_hex"])
                        self.identity = self.replay.get("identity", self.replay.get("settings", {}).get("*IDN?", "Aufnahme"))
                        self.mode = "replay"
                        diagnostic = dict(self.replay)
                        self.publish(frame, diagnostic, time.monotonic() - started)
                        self.active.clear()
                        continue
                    if self.args.demo:
                        self.mode, self.identity = "demo", "DEMO · simulierte Messdaten"
                    elif usb is None:
                        usb = USB(self.args.device)
                        idn = usb.query("*IDN?").decode("ascii", errors="replace").strip()
                        if not idn or "DSO1084F" not in idn.upper().replace("-", ""):
                            raise ValueError("Keine passende DSO1084F-Gerätekennung empfangen.")
                        self.identity = idn
                        version = idn.split(",")[-1].strip()
                        self.mode = self.args.command if self.args.command != "auto" else (
                            "display" if version.startswith("2.") else "legacy")
                        with self.lock:
                            self.connected = True
                    if not self.requests.empty():
                        job_id, action, settings = self.requests.get_nowait()
                        try:
                            self.perform(usb, job_id, action, settings)
                            one_capture = action != "unlock"
                        finally:
                            self.requests.task_done()
                        continue
                    if self.args.demo:
                        frame = demo_frame(time.monotonic(), self.demo_settings, self.demo_running)
                    else:
                        diagnostic.update(identity=self.identity, query=COMMANDS[self.mode])
                        frame, self.mode = receive_auto(usb.query, self.mode, diagnostic, self.args.command == "auto")
                    diagnostic.update(identity=self.identity, frame_hex=frame.hex(), frame_length=len(frame), capture_complete=True)
                    self.publish(frame, diagnostic, time.monotonic() - started)
                    one_capture = False
                except NoMeasurement as exc:
                    # Empty data is a valid instrument reply, not a USB failure.
                    # Keep remote controls available when all channels are off.
                    diagnostic["error"] = str(exc)
                    with self.lock:
                        self.error, self.diagnostic = str(exc), diagnostic
                        if self.last_job:
                            diagnostic["last_control"] = self.jobs.get(self.last_job)
                    one_capture = False
                except Exception as exc:
                    diagnostic["error"] = str(exc)
                    with self.lock:
                        self.error, self.diagnostic, self.connected = str(exc), diagnostic, False
                        if self.last_job:
                            diagnostic["last_control"] = self.jobs.get(self.last_job)
                        # Cancel pending operations after a connection error, so they
                        # cannot take effect unexpectedly after reconnecting.
                        while not self.requests.empty():
                            job_id, _, _ = self.requests.get_nowait()
                            self.jobs[job_id].update(status="error", message="Wegen Verbindungsfehler verworfen; bei Bedarf erneut auslösen.")
                            self.requests.task_done()
                    if usb:
                        usb.close()
                        usb = None
                    one_capture = False
                    self.stop.wait(1.5)
                finally:
                    if usb:
                        # Firmware 2.0.0 locks the keypad before every SCPI
                        # command except *IDN?. Release only after complete
                        # waveform/setting transactions, never between packets.
                        try:
                            usb.write(":SYSTem:LOCKed OFF")
                        except OSError as exc:
                            with self.lock:
                                self.error = f"Bedienfeld konnte nicht freigegeben werden: {exc}"
                                self.connected = False
                            usb.close()
                            usb = None
                self.wake.wait(max(0, self.args.interval - (time.monotonic() - started)))
                self.wake.clear()
        finally:
            if usb:
                usb.close()

    def state(self):
        with self.lock:
            return {"sequence": self.sequence, "identity": self.identity, "mode": self.mode,
                    "paused": not self.active.is_set(), "fps": round(self.fps, 2),
                    "age": round(time.time() - self.updated, 1) if self.updated else None,
                    "error": self.error, "frame": self.frame,
                    "remote_available": bool(self.args.demo or self.connected) and not bool(self.replay),
                    "last_job": (dict(self.jobs[self.last_job]) if self.last_job in self.jobs else None),
                    "settings": self.settings,
                    "settings_updated": self.settings_updated}


def demo_frame(t, settings=None, running=True):
    if settings is None:
        enabled = [1, 2]
        rate = 1e6
    else:
        enabled = [i for i in range(1, 5) if settings["channels"][str(i)]["enabled"]]
        if not enabled:
            raise NoMeasurement("DEMO: Alle Kanäle sind ausgeschaltet.")
        rate = min(1e9, 4000 / (16 * settings["timebase"]["scale"]))
    mask = "".join("1" if i in enabled else "0" for i in range(1, 5))
    head = (f"{int(running)}00000000000000000" + "5.0e+00"*4 + mask +
            f"{rate:.3e}000001+0.00e+00+0.00e+00000000").encode().ljust(99, b"\0")
    samples = bytearray()
    for channel in enabled:
        cfg = settings["channels"][str(channel)] if settings else {}
        for i in range(4000):
            value = round(52 * math.sin(i * 0.017 + (t if running else 0) * 0.7 + channel * 1.1))
            if cfg.get("invert"):
                value = -value
            samples.append(value % 256)
    return head + samples


HTML = r'''<!doctype html><html lang="de"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Voltcraft Live · Fernsteuerung</title><style>
:root{color-scheme:dark;font-family:system-ui,sans-serif;background:#10151e;color:#edf2fa}*{box-sizing:border-box}body{margin:0}main{max-width:1480px;margin:auto;padding:24px}h1{font-size:25px;margin:0 0 4px}h2{font-size:18px;margin:0 0 14px}p{margin:5px 0;color:#aab6c8}header{display:flex;justify-content:space-between;gap:16px;align-items:center;margin-bottom:20px}.badge{border:1px solid #344156;border-radius:20px;padding:7px 13px;white-space:nowrap}.toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:8px 0 14px}button,select,input,a.action{font:inherit;color:#e8effb;background:#253249;border:1px solid #465873;border-radius:8px;padding:8px 11px}button,a.action{cursor:pointer;text-decoration:none}button:hover,a.action:hover{background:#334866}button:disabled{opacity:.4;cursor:default}input[type=checkbox]{accent-color:#78bbff}input[type=number]{width:110px}label{color:#bac6d8}.panel{border:1px solid #303e54;background:#141d2b;border-radius:14px;padding:18px;margin-bottom:18px}canvas{width:100%;height:470px;display:block;background:#101822;border-radius:8px;touch-action:none;cursor:crosshair}.legend{display:flex;gap:16px;flex-wrap:wrap;margin:13px 0}.legend label{cursor:pointer}.stats{display:flex;gap:24px;flex-wrap:wrap;font-variant-numeric:tabular-nums}.error{display:none;background:#492932;color:#ffe1e6;border:1px solid #86515c;border-radius:8px;padding:12px;margin-bottom:14px}.note{font-size:13px;line-height:1.6}.hint{margin:8px 0 12px}.status{font-size:14px;min-height:23px}#identity{word-break:break-word;font-size:13px}.columns{display:grid;grid-template-columns:1.7fr 1fr;gap:18px}.fields{display:flex;align-items:end;gap:12px;flex-wrap:wrap;margin:12px 0}.fields label{display:flex;flex-direction:column;gap:5px;font-size:13px}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:7px 6px;border-bottom:1px solid #303e54;text-align:left;white-space:nowrap}th{color:#aab6c8;font-weight:500}td select{max-width:145px;font-size:13px}td input[type=number]{width:95px;font-size:13px}#remote-message{padding:10px 12px;border-radius:8px;background:#1c2b3c;min-height:40px;margin-top:10px;font-size:14px;line-height:1.5;white-space:pre-line}#remote-message[data-error=true]{background:#492932;color:#ffe1e6}#profile-name{width:100%;max-width:330px}#profile-select{width:100%;max-width:330px}.muted{color:#aab6c8}#zoom-status{font-variant-numeric:tabular-nums}footer{padding:0 4px 16px}@media(max-width:1000px){.columns{grid-template-columns:1fr}}@media(max-width:650px){main{padding:14px}header{align-items:flex-start;flex-direction:column}canvas{height:360px}.stats{gap:10px;font-size:13px}.panel{padding:13px}}
</style><main><header><div><h1>Voltcraft Live</h1><p id="identity">Verbindung wird aufgebaut …</p></div><span class="badge" id="badge">Verbinden</span></header>
<div class="error" id="error"></div><section class="panel"><div class="toolbar"><button id="pause">Übertragung pausieren</button><label>Amplitude <select id="zoom"><option value="1">1×</option><option value="2">2×</option><option value="4">4×</option><option value="8">8×</option><option value="16">16×</option><option value="32">32×</option><option value="64">64×</option></select></label><button id="reset-zoom">Zoom zurücksetzen</button><button id="png" disabled>Bild speichern</button><a class="action" href="/capture.json">Messdaten / Diagnose</a></div>
<div class="status" id="status">Warte auf die erste vollständige Aufnahme.</div><div class="legend" id="legend"></div><p class="note">Häkchen blenden vorhandene Kurven ein oder aus. Graue Kanäle liefern keine Messdaten; unten bei „Messung“ einschalten.</p><canvas id="plot" aria-label="Messkurven: Mausrad zoomt Zeit, Umschalt und Mausrad zoomt Amplitude, Ziehen verschiebt den Ausschnitt"></canvas><p class="note hint">Mausrad: Zeitzoom · Umschalt + Mausrad: Amplitude · Ziehen: Ausschnitt verschieben · Doppelklick: zurücksetzen. <span id="zoom-status"></span></p><div class="stats"><span id="samples">— Messpunkte</span><span id="rate">—</span><span id="updates">—</span></div></section>
<div class="columns"><section class="panel"><h2>Oszi fernsteuern</h2><div class="toolbar" id="device-actions"><button data-device="run">Start</button><button data-device="stop">Stop</button><button data-device="single">Einzelaufnahme</button><button data-device="auto">Auto Scale</button><button data-device="force">Trigger auslösen</button><button data-device="read">Vom Oszi einlesen</button><button data-device="unlock">Bedienfeld entsperren</button></div>
<p class="note">„Nicht ändern“ lässt eine Einstellung aus. „Einlesen“ übernimmt die aktuellen Gerätewerte in die Eingabefelder. Start/Stop steuert die Messung am Oszi. „Bedienfeld entsperren“ pausiert die PC-Übertragung und gibt die Gerätetasten frei.</p>
<div class="fields"><label>Zeitbasis / div<select id="time-scale"></select></label><label>Zeitversatz (µs)<input id="time-position" type="number" step="any" placeholder="Nicht ändern"></label></div>
<div class="table-wrap"><table><thead><tr><th>Kanal</th><th>Messung</th><th>V/div</th><th>Offset (V)</th><th>Kopplung</th><th>Tastkopf</th><th>20 MHz</th><th>Invertiert</th></tr></thead><tbody id="channel-controls"></tbody></table></div>
<div class="fields"><label>Triggerbetrieb<select id="trigger-sweep"><option value="">Nicht ändern</option><option value="AUTO">Auto</option><option value="NORMAL">Normal</option><option value="SINGLE">Single</option></select></label><label>Flankenquelle<select id="trigger-source"><option value="">Nicht ändern</option><option value="CHANnel1">CH1</option><option value="CHANnel2">CH2</option><option value="CHANnel3">CH3</option><option value="CHANnel4">CH4</option><option value="EXT">Extern</option></select></label><label>Flanke<select id="trigger-slope"><option value="">Nicht ändern</option><option value="RISING">Steigend</option><option value="FALLING">Fallend</option><option value="EITHER">Beide</option></select></label><label>Triggerpegel (V)<input id="trigger-level" type="number" step="any" placeholder="Nicht ändern"></label></div>
<p class="note">Quelle, Flanke und Pegel wählen den Flankentrigger (Edge). Ein Profil umfasst diese Bedienelemente; andere Triggerarten werden damit nicht gespeichert.</p><div class="toolbar"><button id="apply-settings" data-device="apply">Einstellungen anwenden</button><button id="clear-settings">Felder leeren</button></div><div id="remote-message" role="status" aria-live="polite">Bereit. Geräteeinstellungen werden erst beim Anwenden verändert.</div></section>
<section class="panel"><h2>Eigene Profile</h2><label for="profile-select">Gespeichertes Profil</label><div class="fields"><select id="profile-select"><option value="">Profil auswählen …</option></select></div><p class="note">Auswählen lädt das Profil in die Eingabefelder und stellt den Anzeigezoom ein.</p><div class="toolbar"><button id="apply-profile" data-device="profile">Profil am Oszi anwenden</button><button id="delete-profile">Profil löschen</button></div>
<label for="profile-name">Profilname</label><div class="fields"><input id="profile-name" maxlength="80" placeholder="z. B. Radio · NF an CH1"></div><div class="fields"><label style="display:block"><input type="checkbox" id="overwrite-profile"> Vorhandenen Namen überschreiben</label></div><button id="save-profile">Felder und Anzeige als Profil speichern</button><p class="note hint">Profile bleiben nach einem Neustart erhalten. Speichern allein verändert keine Geräteeinstellungen.</p><div class="toolbar"><a class="action" href="/profiles.json">Profile exportieren</a><button id="import-profile">Profile importieren</button><input type="file" id="profile-file" accept=".json,application/json" hidden></div><p class="note" id="profile-message" role="status"></p></section></div>
<footer><p class="note"><strong>Die Kurvenamplitude zeigt Rohwerte, noch keine kalibrierten Volt.</strong> V/div, Offset und Triggerpegel in der Fernsteuerung sind Geräteeinstellungen. Der Anzeigezoom verändert nur die Darstellung auf dem PC.</p><p class="note">Beenden: Strg+C im Terminal. Fernsteuerung und Profile laufen lokal auf diesem Computer.</p><p class="note">Voltcraft Live 0.1.0 · Copyright © 2026 Voltcraft Live contributors · <a href="/license" target="_blank" rel="noopener">GNU GPLv3 oder neuer</a> · Freie Software, ohne Gewährleistung.</p></footer></main>
<script>
const key=__TOKEN__, $=id=>document.getElementById(id), colors=['#ffe66b','#78bbff','#ec8dbd','#8ce8b1'];
let state=null,last=-1,legendKey='',remoteBusy=false,pendingRead=null,profiles=[],drag=null;
let view={x_zoom:1,x_start:0,y_zoom:1,y_center:0,shown:[true,true,true,true]};
const clamp=(v,lo,hi)=>Math.max(lo,Math.min(hi,v));
function number(v,unit){if(v===0)return '0 '+unit;let a=Math.abs(v);const ps=[[1e9,'G'],[1e6,'M'],[1e3,'k'],[1,''],[1e-3,'m'],[1e-6,'µ'],[1e-9,'n'],[1e-12,'p']];let p=ps.find(x=>a>=x[0])||ps.at(-1);return (v/p[0]).toLocaleString('de-DE',{maximumSignificantDigits:4})+' '+p[1]+unit}
function options(el,values,unit){el.replaceChildren();el.add(new Option('Nicht ändern',''));for(const v of values)el.add(new Option(number(v,unit),String(v)))}
function sequence(low,high){const out=[];for(let e=-10;e<=5;e++)for(const m of [1,2,5]){let v=Number((m*10**e).toPrecision(12));if(v>=low&&v<=high)out.push(v)}return out}
const scales=sequence(.0005,10000);options($('time-scale'),sequence(2e-9,100),'s/div');
for(let i=1;i<=4;i++){const row=document.createElement('tr');row.innerHTML=`<td style="color:${colors[i-1]}">CH${i}</td><td><select id="ch${i}-enabled" aria-label="CH${i} Messung"><option value="">Nicht ändern</option><option value="true">Ein</option><option value="false">Aus</option></select></td><td><select id="ch${i}-scale" aria-label="CH${i} V/div"></select></td><td><input id="ch${i}-offset" aria-label="CH${i} Offset in Volt" type="number" step="any" placeholder="—"></td><td><select id="ch${i}-coupling" aria-label="CH${i} Kopplung"><option value="">Nicht ändern</option><option>DC</option><option>AC</option><option>GND</option></select></td><td><select id="ch${i}-probe" aria-label="CH${i} Tastkopffaktor"><option value="">Nicht ändern</option><option value="1">1×</option><option value="10">10×</option><option value="100">100×</option><option value="1000">1000×</option></select></td><td><select id="ch${i}-bwlimit" aria-label="CH${i} 20-MHz-Limit"><option value="">—</option><option value="true">Ein</option><option value="false">Aus</option></select></td><td><select id="ch${i}-invert" aria-label="CH${i} invertieren"><option value="">—</option><option value="true">Ja</option><option value="false">Nein</option></select></td>`;$('channel-controls').append(row);options($(`ch${i}-scale`),scales,'V/div')}
function getSettings(){const result={},channels={};for(let i=1;i<=4;i++){let fields={};for(const k of ['enabled','scale','offset','coupling','probe','bwlimit','invert']){let el=$(`ch${i}-${k}`),v=el.value;if(v==='')continue;if(!el.checkValidity())throw Error(`CH${i}: Eingabe prüfen.`);fields[k]=['enabled','bwlimit','invert'].includes(k)?v==='true':['scale','offset','probe'].includes(k)?Number(v):v}if(Object.keys(fields).length)channels[String(i)]=fields}if(Object.keys(channels).length)result.channels=channels;
let timebase={};if($('time-scale').value!=='')timebase.scale=Number($('time-scale').value);if($('time-position').value!==''){if(!$('time-position').checkValidity())throw Error('Zeitversatz prüfen.');timebase.position=Number($('time-position').value)/1e6}if(Object.keys(timebase).length)result.timebase=timebase;
let trigger={};for(const k of ['sweep','source','slope','level']){let el=$('trigger-'+k);if(el.value!==''){if(!el.checkValidity())throw Error('Triggerpegel prüfen.');trigger[k]=k==='level'?Number(el.value):el.value}}if(Object.keys(trigger).length)result.trigger=trigger;return result}
function setValue(id,value){let el=$(id);if(value===undefined||value===null){el.value='';return}let v=String(value);if(el.tagName==='SELECT'&&![...el.options].some(o=>o.value===v))el.add(new Option(v,v));el.value=v}
function fillSettings(s={}){for(let i=1;i<=4;i++)for(const k of ['enabled','scale','offset','coupling','probe','bwlimit','invert'])setValue(`ch${i}-${k}`,s.channels?.[String(i)]?.[k]);setValue('time-scale',s.timebase?.scale);setValue('time-position',s.timebase?.position===undefined?undefined:s.timebase.position*1e6);for(const k of ['sweep','source','slope','level'])setValue('trigger-'+k,s.trigger?.[k])}
function message(text,error=false){$('remote-message').textContent=text;$('remote-message').dataset.error=String(error)}
async function api(path,body){let r=await fetch(path,body===undefined?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json','X-Voltcraft-Key':key},body:JSON.stringify(body)});let data=await r.json();if(!r.ok)throw Error(data.error||'HTTP '+r.status);return data}
function buttons(){for(const b of document.querySelectorAll('[data-device]'))b.disabled=remoteBusy||!state?.remote_available;$('apply-profile').disabled||=!$('profile-select').value}
async function device(action,settings){if(remoteBusy)return;remoteBusy=true;buttons();message('Auftrag wird gesendet …');try{let data=await api('/device',{action,settings});pendingRead=['read','auto'].includes(action)?data.job_id:null;for(let attempt=0;attempt<400;attempt++){const job=await api('/job/'+data.job_id);message(job.message,job.status==='error');if(['done','error'].includes(job.status)){if(job.status==='done'&&pendingRead===job.id&&job.result?.settings){fillSettings(job.result.settings);if(job.result.settings.trigger_mode&&job.result.settings.trigger_mode!=='EDGE')message(job.message+' Aktuell ist '+job.result.settings.trigger_mode+' aktiv; die Eingabefelder speichern nur den Flankentrigger.')}return job}await new Promise(resolve=>setTimeout(resolve,250))}throw Error('Geräteaktion dauert ungewöhnlich lange. Das Terminal und die Diagnose prüfen.')}catch(e){message(e.message,true)}finally{remoteBusy=false;pendingRead=null;buttons()}}
for(const b of document.querySelectorAll('#device-actions button'))b.onclick=()=>device(b.dataset.device);
$('apply-settings').onclick=()=>{try{device('apply',getSettings())}catch(e){message(e.message,true)}};$('clear-settings').onclick=()=>{fillSettings();message('Felder geleert. Geräteinstellungen bleiben unverändert.')};
function geometry(){let r=$('plot').getBoundingClientRect();return {r,L:66,R:18,T:28,B:48,W:r.width-84,H:r.height-76}}
function syncZoom(){setValue('zoom',Number(view.y_zoom.toFixed(3)));$('zoom-status').textContent='Zeit '+view.x_zoom.toLocaleString('de-DE',{maximumFractionDigits:1})+'× · Amplitude '+view.y_zoom.toLocaleString('de-DE',{maximumFractionDigits:1})+'×'}
function resetZoom(){view.x_zoom=1;view.x_start=0;view.y_zoom=1;view.y_center=0;syncZoom();draw()}
function draw(){const c=$('plot'),g=geometry(),d=window.devicePixelRatio||1;c.width=Math.round(g.r.width*d);c.height=Math.round(g.r.height*d);const ctx=c.getContext('2d');ctx.scale(d,d);const w=g.r.width,h=g.r.height,{L,T,W,H}=g;ctx.fillStyle='#101822';ctx.fillRect(0,0,w,h);ctx.font='12px system-ui';ctx.lineWidth=1;const limit=128/view.y_zoom;
for(let i=0;i<=8;i++){let y=T+H*i/8;ctx.strokeStyle=i===4?'#51667c':'#26394b';ctx.beginPath();ctx.moveTo(L,y);ctx.lineTo(L+W,y);ctx.stroke();ctx.fillStyle='#a1b3c7';ctx.textAlign='right';ctx.fillText((view.y_center+limit*(1-i/4)).toLocaleString('de-DE',{maximumFractionDigits:1}),L-8,y+4)}
for(let i=0;i<=10;i++){let x=L+W*i/10;ctx.strokeStyle='#26394b';ctx.beginPath();ctx.moveTo(x,T);ctx.lineTo(x,T+H);ctx.stroke();if(state?.frame&&i%2===0){ctx.fillStyle='#a1b3c7';ctx.textAlign=i===0?'left':i===10?'right':'center';ctx.fillText(number(state.frame.duration*(view.x_start+i/10/view.x_zoom),'s'),x,T+H+22)}}
ctx.textAlign='left';ctx.fillStyle='#becbdd';ctx.fillText('Rohwert',8,16);ctx.textAlign='center';ctx.fillText('Zeit seit erstem Messpunkt',L+W/2,h-9);
if(!state?.frame){ctx.fillStyle='#93a9c1';ctx.fillText('Warte auf Messdaten …',w/2,h/2);return}const f=state.frame;ctx.save();ctx.beginPath();ctx.rect(L,T,W,H);ctx.clip();
for(const ch of f.channels){if(!view.shown[ch.channel-1])continue;ctx.strokeStyle=colors[ch.channel-1];ctx.lineWidth=1.3;ctx.beginPath();let first=true;for(const [index,value] of ch.points){const fraction=f.count_per_channel>1?index/(f.count_per_channel-1):0,x=L+(fraction-view.x_start)*view.x_zoom*W,y=T+H/2-(value-view.y_center)/(2*limit)*H;if(first){ctx.moveTo(x,y);first=false}else ctx.lineTo(x,y)}ctx.stroke()}ctx.restore();if(state.mode==='demo'||state.mode==='replay'){ctx.font='bold 15px system-ui';ctx.fillStyle='#ffe66b';ctx.textAlign='right';ctx.fillText(state.mode==='demo'?'DEMO · simuliert':'GESPEICHERTE AUFNAHME',L+W-10,T+20)}}
function renderLegend(s){const channels=new Set((s?.frame?.channels||[]).map(ch=>ch.channel));const ready=Boolean(s?.frame),lk=(ready?'ready:':'waiting:')+[...channels].join(',')+':'+(s?.mode||'')+':'+view.shown.join(',');if(lk===legendKey)return;legendKey=lk;$('legend').replaceChildren();for(let channel=1;channel<=4;channel++){const available=channels.has(channel),label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.checked=available&&view.shown[channel-1];input.disabled=!available;input.setAttribute('aria-label','CH'+channel+' Kurve anzeigen');input.onchange=()=>{view.shown[channel-1]=input.checked;draw()};label.style.color=colors[channel-1];label.style.opacity=available?'1':'.55';label.title=available?'Kurve auf dem PC ein- oder ausblenden':!ready?'Warte auf Messdaten':s.mode==='replay'?'Dieser Kanal ist in der gespeicherten Aufnahme nicht enthalten':'Zum Aktivieren unten bei CH'+channel+' „Messung: Ein“ auswählen und Einstellungen anwenden.';label.append(input,' CH'+channel+(available?'':!ready?' · wartet':s.mode==='replay'?' · nicht aufgenommen':' · keine Messdaten'));$('legend').append(label)}}
function render(s){state=s;renderLegend(s);$('identity').textContent=s.identity;$('badge').textContent=s.mode==='demo'?'Demo':s.mode==='replay'?'Aufnahme':s.error?'Verbindungsfehler':s.paused?'Pausiert':s.frame?'Live':'Verbinden';$('pause').textContent=s.paused?'Übertragung fortsetzen':'Übertragung pausieren';$('pause').disabled=s.mode==='replay';$('error').style.display=s.error?'block':'none';$('error').textContent=s.error;$('png').disabled=!s.frame;buttons();
if(s.frame){const f=s.frame,age=s.age||0;$('status').textContent=(s.mode==='demo'?'Simulierte Daten':s.mode==='replay'?'Gespeicherte Aufnahme':s.error?'Letzte gültige Aufnahme':s.paused?'Übertragung pausiert':age>3?'Letzte Aufnahme · keine neuen Daten':'Messkurven werden aktualisiert')+' · '+(f.running?'Oszi: Run':'Oszi: Stop');$('samples').textContent=f.count_per_channel.toLocaleString('de-DE')+' Messpunkte / Kanal';$('rate').textContent=number(f.sample_rate,'Sa/s')+' · Schritt '+number(f.dt,'s');$('updates').textContent=s.mode==='replay'?'Aufnahme vom Datenträger':s.fps.toLocaleString('de-DE')+' Aufnahmen/s · Alter '+age.toLocaleString('de-DE')+' s';if(s.sequence!==last){last=s.sequence;draw()}}
}
async function poll(){try{render(await api('/state'))}catch(e){$('badge').textContent='Verbindung verloren';$('error').style.display='block';$('error').textContent='Das Programm antwortet nicht. Prüfe das Terminal.'}setTimeout(poll,250)}
$('pause').onclick=async()=>{try{render(await api('/control',{paused:!state?.paused}))}catch(e){message(e.message,true)}};
$('zoom').onchange=()=>{view.y_zoom=Number($('zoom').value);syncZoom();draw()};$('reset-zoom').onclick=resetZoom;window.addEventListener('resize',draw);$('plot').addEventListener('dblclick',resetZoom);
$('plot').addEventListener('wheel',e=>{if(!state?.frame||!e.deltaY)return;const g=geometry(),px=(e.clientX-g.r.left-g.L)/g.W,py=(e.clientY-g.r.top-g.T)/g.H;if(px<0||px>1||py<0||py>1)return;e.preventDefault();const delta=clamp(e.deltaY*(e.deltaMode===1?16:e.deltaMode===2?g.r.height:1),-100,100),factor=Math.exp(-delta*.0025);if(e.shiftKey){const at=view.y_center+(1-2*py)*128/view.y_zoom;view.y_zoom=clamp(view.y_zoom*factor,1,64);view.y_center=clamp(at-(1-2*py)*128/view.y_zoom,-128,127);if(view.y_zoom===1)view.y_center=0}else{const at=view.x_start+px/view.x_zoom;view.x_zoom=clamp(view.x_zoom*factor,1,Math.min(512,Math.max(1,state.frame.count_per_channel-1)));view.x_start=clamp(at-px/view.x_zoom,0,1-1/view.x_zoom)}syncZoom();draw()},{passive:false});
$('plot').addEventListener('pointerdown',e=>{if(e.button!==0||!state?.frame)return;const g=geometry();if(e.clientX<g.r.left+g.L||e.clientX>g.r.right-g.R||e.clientY<g.r.top+g.T||e.clientY>g.r.bottom-g.B)return;drag={x:e.clientX,y:e.clientY,start:view.x_start,center:view.y_center};$('plot').setPointerCapture(e.pointerId);$('plot').style.cursor='grabbing'});
$('plot').addEventListener('pointermove',e=>{if(!drag)return;const g=geometry();view.x_start=clamp(drag.start-(e.clientX-drag.x)/g.W/view.x_zoom,0,1-1/view.x_zoom);if(view.y_zoom>1)view.y_center=clamp(drag.center+(e.clientY-drag.y)/g.H*256/view.y_zoom,-128,127);draw()});
for(const name of ['pointerup','pointercancel','lostpointercapture'])$('plot').addEventListener(name,()=>{drag=null;$('plot').style.cursor='crosshair'});
$('png').onclick=()=>{$('plot').toBlob(blob=>{if(!blob)return;const a=document.createElement('a'),url=URL.createObjectURL(blob);a.href=url;a.download='voltcraft-'+new Date().toISOString().replace(/[:.]/g,'-')+'.png';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)},'image/png')};
function profileList(list,selected=$('profile-select').value){profiles=list;$('profile-select').replaceChildren(new Option('Profil auswählen …',''));for(const p of profiles)$('profile-select').add(new Option(p.name,p.name));$('profile-select').value=profiles.some(p=>p.name===selected)?selected:'';buttons()}
$('profile-select').onchange=()=>{const p=profiles.find(p=>p.name===$('profile-select').value);if(p){fillSettings(p.settings);view=JSON.parse(JSON.stringify(p.view));legendKey='';if(state)render(state);syncZoom();draw();$('profile-name').value=p.name;$('profile-message').textContent='Profil geladen. Gerätewerte erst mit „Profil am Oszi anwenden“ senden.'}buttons()};
$('apply-profile').onclick=()=>{const p=profiles.find(p=>p.name===$('profile-select').value);if(!p)return;if(!Object.keys(p.settings).length){message('Dieses Profil enthält nur Anzeigeeinstellungen. Der Zoom ist bereits geladen.');return}device('apply',p.settings)};
$('save-profile').onclick=async()=>{try{const profile={name:$('profile-name').value,settings:getSettings(),view};let data=await api('/profiles',{action:'save',profile,overwrite:$('overwrite-profile').checked});profileList(data.profiles,profile.name.trim());$('profile-message').textContent='Profil gespeichert. Geräteinstellungen wurden dabei nicht verändert.';$('overwrite-profile').checked=false}catch(e){$('profile-message').textContent=e.message}};
$('delete-profile').onclick=async()=>{const name=$('profile-select').value;if(!name)return;if(!window.confirm(`Profil „${name}“ löschen?`))return;try{let data=await api('/profiles',{action:'delete',name});profileList(data.profiles);$('profile-message').textContent='Profil gelöscht.'}catch(e){$('profile-message').textContent=e.message}};
$('import-profile').onclick=()=>$('profile-file').click();$('profile-file').onchange=async()=>{const file=$('profile-file').files[0];if(!file)return;try{if(file.size>262144)throw Error('Profil-Export ist zu groß.');let data=await api('/profiles',{action:'import',bundle:JSON.parse(await file.text()),overwrite:$('overwrite-profile').checked});profileList(data.profiles);$('profile-message').textContent='Profile importiert. Zum Laden ein Profil auswählen.';$('overwrite-profile').checked=false}catch(e){$('profile-message').textContent=e.message}finally{$('profile-file').value=''}};
renderLegend(null);api('/profiles').then(data=>profileList(data.profiles)).catch(e=>{$('profile-message').textContent='Profile konnten nicht geladen werden: '+e.message});syncZoom();draw();poll();
</script></html>
'''

def handler(live, token, profiles):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def send(self, data, content_type, status=200, filename=None):
            if isinstance(data, str):
                data = data.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def json_reply(self, value, status=200):
            self.send(json.dumps(value, ensure_ascii=False, allow_nan=False), "application/json", status)

        def allowed_host(self):
            return self.headers.get("Host") in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}")

        def do_GET(self):
            if not self.allowed_host():
                self.send("Nicht erlaubt.", "text/plain", 403)
                return
            try:
                if self.path == "/":
                    self.send(HTML.replace("__TOKEN__", json.dumps(token)), "text/html; charset=utf-8")
                elif self.path == "/license":
                    license_path = Path(__file__).resolve().with_name("LICENSE")
                    if license_path.is_file():
                        self.send(license_path.read_text(encoding="utf-8"), "text/plain; charset=utf-8")
                    else:
                        self.send("Voltcraft Live is licensed under GNU GPL version 3 or later.\n"
                                  "Copyright (C) 2026 Voltcraft Live contributors.\n"
                                  "This program comes with ABSOLUTELY NO WARRANTY.\n"
                                  "You may redistribute and modify it under the GNU GPL.\n"
                                  "Full license: https://www.gnu.org/licenses/gpl-3.0.html\n",
                                  "text/plain; charset=utf-8")
                elif self.path == "/state":
                    self.json_reply(live.state())
                elif self.path.startswith("/job/"):
                    self.json_reply(live.job(self.path[5:]))
                elif self.path in ("/profiles", "/profiles.json"):
                    data = {"format": "voltcraft-live-profiles-v1", "profiles": profiles.listing()}
                    if self.path.endswith(".json"):
                        self.send(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), "application/json", filename="voltcraft-profile.json")
                    else:
                        self.json_reply(data)
                elif self.path == "/capture.json":
                    with live.lock:
                        data = live.diagnostic
                    if data is None:
                        self.send("Noch keine Aufnahme oder Diagnose verfügbar.", "text/plain; charset=utf-8", 503)
                        return
                    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
                    self.send(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), "application/json", filename=f"voltcraft-live-{stamp}.json")
                else:
                    self.send("Nicht gefunden.", "text/plain", 404)
            except (ValueError, OSError) as exc:
                self.json_reply({"error": str(exc)}, 400)

        def do_POST(self):
            if not self.allowed_host() or self.headers.get("X-Voltcraft-Key") != token:
                self.json_reply({"error": "Nicht erlaubt."}, 403)
                return
            origin = self.headers.get("Origin")
            if origin and origin not in (f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"):
                self.json_reply({"error": "Nicht erlaubter Ursprung."}, 403)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 262144:
                    raise ValueError("Ungültige Anfragelänge.")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("Ungültige Anfrage.")
                if self.path == "/control":
                    if type(body.get("paused")) is not bool:
                        raise ValueError("Ungültige Pause-Einstellung.")
                    if body["paused"]:
                        live.active.clear()
                    elif not live.replay:
                        live.active.set()
                    live.wake.set()
                    self.json_reply(live.state())
                elif self.path == "/device":
                    self.json_reply({"job_id": live.submit(body)}, 202)
                elif self.path == "/profiles":
                    action = body.get("action")
                    self.json_reply({"profiles": profiles.change(action, body)})
                else:
                    self.json_reply({"error": "Nicht gefunden."}, 404)
            except (ValueError, TypeError, OSError) as exc:
                self.json_reply({"error": str(exc)}, 400)
    return Handler


def main():
    p = argparse.ArgumentParser(description="Voltcraft-Messkurven live im Browser anzeigen")
    p.add_argument("--version", action="version", version="Voltcraft Live " + __version__)
    p.add_argument("--device", default="/dev/usbtmc0")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--interval", type=float, default=0.35, help="Abstand zwischen Aufnahmen in Sekunden")
    p.add_argument("--command", choices=["auto", "display", "legacy"], default="auto")
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--profiles", type=Path, default=Path.home() / ".config" / "voltcraft-live" / "profiles.json",
                   help="Datei für eigene Profile")
    sample = p.add_mutually_exclusive_group()
    sample.add_argument("--demo", action="store_true", help="Anzeige mit simulierten Daten testen")
    sample.add_argument("--replay", type=Path, help="Gespeicherte Aufnahme anzeigen")
    args = p.parse_args()
    if not math.isfinite(args.interval) or not 0.1 <= args.interval <= 60:
        p.error("Das Intervall muss zwischen 0.1 und 60 Sekunden liegen.")
    if not 0 <= args.port <= 65535:
        p.error("Ungültiger Port.")
    try:
        live = Live(args)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler(live, secrets.token_urlsafe(24), Profiles(args.profiles)))
    except (OSError, ValueError) as exc:
        p.exit(1, f"Start fehlgeschlagen: {exc}\n")
    live.thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"Voltcraft Live {__version__}: {url}", flush=True)
    print("Copyright (C) 2026 Voltcraft Live contributors. GNU GPLv3 oder neuer; ohne Gewährleistung.", flush=True)
    print("Beenden mit Strg+C. Amplitude zunächst als Rohwerte.", flush=True)
    if not args.demo and not args.replay:
        print("USB-Kabel am rückseitigen Geräteanschluss; andere Aufnahmeprogramme schließen.", flush=True)
    if not args.no_browser:
        threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        live.stop.set()
        live.active.set()
        live.wake.set()
        server.server_close()
        live.thread.join(timeout=3)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
