"""Tracked process launches and Win32 command messages, never input emulation."""

import base64
import ctypes
import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path

from .model import CircuitError, atomic_write, digest, json_bytes


def powershell(script, timeout=20):
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                            capture_output=True, timeout=timeout, creationflags=0x08000000)
    if result.returncode:
        raise CircuitError(result.stderr.decode("utf-8", errors="replace")[-3000:])
    output = result.stdout.decode("utf-8-sig", errors="replace").strip()
    return json.loads(output) if output else None


def ps_string(value):
    return "'" + str(value).replace("'", "''") + "'"


def launcher_info(executable):
    if os.name != "nt":
        raise CircuitError("Proteus runtime requires Windows")
    override = os.getenv("PROTEUS_MCP_LAUNCHER")
    shortcut = Path(os.environ.get("PUBLIC", "C:/Users/Public")) / "Desktop/Proteus.lnk"
    if override:
        launch = Path(override).resolve()
        directory = Path(executable).parent
        source = "PROTEUS_MCP_LAUNCHER"
    elif shortcut.is_file():
        data = powershell("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
                          "$s=(New-Object -ComObject WScript.Shell).CreateShortcut(" + ps_string(shortcut) + "); "
                          "$s | Select-Object TargetPath,Arguments,WorkingDirectory | ConvertTo-Json -Compress")
        target = Path(data["TargetPath"]).resolve()
        if target != Path(executable).resolve():
            launch, directory, source = Path(executable), Path(executable).parent, "configured_executable (unrelated shortcut ignored)"
        elif data.get("Arguments"):
            raise CircuitError("Shortcut has arguments; configure PROTEUS_MCP_LAUNCHER explicitly")
        else:
            launch = target
            directory = Path(data["WorkingDirectory"] or str(Path(executable).parent))
            source = str(shortcut)
    else:
        launch, directory, source = Path(executable), Path(executable).parent, "configured_executable"
    # An advertised MSI shortcut may report its icon resource as TargetPath.
    if launch.is_file() and launch.read_bytes()[:2] != b"MZ" and not override:
        launch, directory, source = Path(executable), Path(executable).parent, "configured_executable (MSI icon target ignored)"
    if not launch.is_file() or not directory.is_dir():
        raise CircuitError("Proteus launcher or working directory does not exist")
    return {"executable": str(launch), "cwd": str(directory), "source": source,
            "sha256": digest(launch.read_bytes())}


def process_list():
    result = powershell("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
                        "@(Get-CimInstance Win32_Process -Filter \"Name='PDS.exe'\" | "
                        "Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine,"
                        "@{n='created';e={$_.CreationDate.ToUniversalTime().ToString('o')}}) | ConvertTo-Json -Compress")
    return result if isinstance(result, list) else [result] if result else []


def windows_for(pid):
    """Read window metadata and menus. No focus, screenshots or synthesized input."""
    if os.name != "nt":
        return []
    from ctypes import wintypes as w
    api = ctypes.WinDLL("user32", use_last_error=True)
    api.GetWindowTextLengthW.argtypes = [w.HWND]
    api.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
    api.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
    api.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
    api.GetMenu.argtypes, api.GetMenu.restype = [w.HWND], w.HMENU
    api.GetSubMenu.argtypes, api.GetSubMenu.restype = [w.HMENU, ctypes.c_int], w.HMENU
    api.GetMenuItemCount.argtypes = [w.HMENU]
    api.GetMenuItemID.argtypes, api.GetMenuItemID.restype = [w.HMENU, ctypes.c_int], w.UINT
    api.GetMenuState.argtypes, api.GetMenuState.restype = [w.HMENU, w.UINT, w.UINT], w.UINT
    api.GetMenuStringW.argtypes = [w.HMENU, w.UINT, w.LPWSTR, ctypes.c_int, w.UINT]
    api.IsWindowVisible.argtypes = [w.HWND]
    api.IsWindowEnabled.argtypes = [w.HWND]
    callback_type = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    api.EnumWindows.argtypes = [callback_type, w.LPARAM]
    api.EnumChildWindows.argtypes = [w.HWND, callback_type, w.LPARAM]

    def info(hwnd):
        buf = ctypes.create_unicode_buffer(min(api.GetWindowTextLengthW(hwnd)+1, 8192))
        api.GetWindowTextW(hwnd, buf, len(buf))
        cls = ctypes.create_unicode_buffer(256)
        api.GetClassNameW(hwnd, cls, len(cls))
        return {"hwnd": int(hwnd), "title": buf.value, "class": cls.value,
                "visible": bool(api.IsWindowVisible(hwnd)), "enabled":bool(api.IsWindowEnabled(hwnd))}

    def menu_items(menu, parent="", depth=0):
        if not menu or depth > 8:
            return []
        items = []
        for index in range(min(api.GetMenuItemCount(menu), 200)):
            label = ctypes.create_unicode_buffer(512)
            api.GetMenuStringW(menu, index, label, len(label), 0x400)
            path = (parent + "/" + label.value).strip("/")
            sub = api.GetSubMenu(menu, index)
            if sub:
                items.extend(menu_items(sub, path, depth+1))
            elif label.value:
                state = api.GetMenuState(menu, index, 0x400)
                items.append({"path": path, "id": api.GetMenuItemID(menu, index),
                              "enabled": not bool(state & 3), "checked": bool(state & 8)})
        return items

    windows = []

    @callback_type
    def collect(hwnd, _):
        owner = w.DWORD()
        api.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            item = info(hwnd)
            children = []

            @callback_type
            def child(ch, _):
                entry = info(ch)
                if entry["title"] or entry["class"].lower() in ("msctls_statusbar32", "afxmdiframe140u"):
                    children.append(entry)
                return len(children) < 250

            api.EnumChildWindows(hwnd, child, 0)
            item.update(children=children, menus=menu_items(api.GetMenu(hwnd)))
            windows.append(item)
        return True

    api.EnumWindows(collect, 0)
    return windows


def diagnostic_events(since_epoch):
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(since_epoch))
    result = powershell("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
                      "$e=Get-WinEvent -FilterHashtable @{LogName='Application';Id=1000,1001,1002;StartTime=[datetime]" +
                      ps_string(since) + "} -ErrorAction SilentlyContinue; "
                      "@($e | Where-Object {$_.Message -match '(?i)PDS.exe|ISIS.dll|Prims.dll'} | "
                      "Select-Object -First 20 Id,RecordId,ProviderName,"
                      "@{n='time';e={$_.TimeCreated.ToUniversalTime().ToString('o')}},Message) | ConvertTo-Json -Compress")
    return result if isinstance(result, list) else [result] if result else []


def project_windows(process, data):
    """Keep the observed main HWND when a native report changes its title."""
    previous = {data['project_hwnd']} if data.get('project_hwnd') else set()
    for observation in data.get('observations',[]):
        for window in observation.get('windows',{}).get(str(process['ProcessId']),[]):
            if '.pdsprj' in window.get('title','').casefold():
                previous.add(window['hwnd'])
    anchored = [w for w in process['windows'] if w['visible'] and w['hwnd'] in previous]
    if anchored:
        return anchored
    return [w for w in process['windows'] if w['visible'] and '.pdsprj' in w['title'].casefold()]


def normal_close_targets(state,data):
    """Never close an owned Qt dialog just because it is a visible window."""
    targets = [w for process in state['processes'] for w in project_windows(process,data)]
    return targets if len(targets)==1 and targets[0].get('enabled',False) else []


class Runtime:
    def __init__(self, root, executable):
        self.root, self.executable = Path(root) / "_runtime", Path(executable)

    def session_path(self, session):
        if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[a-f0-9]{8}", session):
            raise CircuitError("Invalid runtime session ID")
        return self.root / session / "session.json"

    def load(self, session):
        return json.loads(self.session_path(session).read_text(encoding="utf-8"))

    def save(self, data):
        atomic_write(self.session_path(data["session"]), json_bytes(data))

    def status(self, session=None):
        processes = process_list()
        if session:
            data = self.load(session)
            owners = {p["ProcessId"]: p for p in data.get("owned_processes", [])}
            processes = [p for p in processes if p["ProcessId"] in owners and p["created"] == owners[p["ProcessId"]]["created"]]
        else:
            data = {}
        for process in processes:
            process["windows"] = windows_for(process["ProcessId"])
        return {"session": session, "processes": processes, "alive": bool(processes),
                "project": data.get("path"), "simulation_verified": False}

    def diagnostics(self, session):
        data = self.load(session)
        folder = Path(os.environ.get("LOCALAPPDATA", "")) / "CrashDumps"
        dumps = [{"path": str(p), "bytes": p.stat().st_size, "mtime": p.stat().st_mtime}
                 for p in folder.glob("PDS.exe.*.dmp") if p.stat().st_mtime >= data["started_epoch"]-1]
        errors = []
        try:
            events = diagnostic_events(data["started_epoch"])
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            events = []
            errors.append(str(error))
        result = {"session": session, "events": events, "crash_dumps": dumps,
                  "collection_errors": errors, "runtime": self.status(session)}
        atomic_write(self.session_path(session).parent / "diagnostics.json", json_bytes(result))
        return result

    def open(self, path, wait_seconds=12):
        if type(wait_seconds) is not int or not 1 <= wait_seconds <= 30:
            raise CircuitError("wait_seconds must be an integer between 1 and 30")
        path = Path(path).resolve(strict=True)
        if path.suffix.lower() != ".pdsprj":
            raise CircuitError("Expected a .pdsprj file")
        before = process_list()
        if before:
            return {"status": "busy", "code": "EXISTING_PROTEUS_PROCESS", "processes": before,
                    "reason": "Close the current instance or use its tracked session before launching another project"}
        launch = launcher_info(self.executable)
        session = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
        data = {"session": session, "path": str(path), "project_sha256": digest(path.read_bytes()),
                "started_epoch": time.time(), "launcher": launch, "owned_processes": []}
        self.save(data)
        logs = self.session_path(session).parent
        try:
            with (logs/"stdout.log").open("wb") as out, (logs/"stderr.log").open("wb") as err:
                process = subprocess.Popen([launch["executable"], str(path)], cwd=launch["cwd"], stdout=out, stderr=err)
        except OSError as error:
            data.update(status="failed", launch_error=str(error), native_reopen_verified=False, simulation_verified=False)
            self.save(data)
            return data
        data["launcher_pid"] = process.pid
        self.save(data)
        deadline, observations = time.monotonic()+wait_seconds, []
        matched_count = 0
        while time.monotonic() < deadline:
            current = process_list()
            for item in current:
                known = {process.pid, *(p["ProcessId"] for p in data["owned_processes"])}
                if item["ProcessId"] not in known and item["ParentProcessId"] not in known:
                    continue
                if Path(item["ExecutablePath"]).resolve() != Path(launch["executable"]).resolve():
                    continue
                if not any(p["ProcessId"] == item["ProcessId"] for p in data["owned_processes"]):
                    data["owned_processes"].append(item)
            windows = {str(p["ProcessId"]): windows_for(p["ProcessId"]) for p in current}
            observations.append({"epoch": time.time(), "pids": [p["ProcessId"] for p in current], "windows": windows})
            matched = any(window["visible"] and path.stem.casefold() in window["title"].casefold()
                          for entries in windows.values() for window in entries)
            matched_count = matched_count+1 if matched else 0
            if matched_count >= 2:
                break
            if process.poll() is not None and not current and len(observations) > 2:
                break
            time.sleep(0.6)
        data.update(launcher_exit_code=process.poll(), observations=observations)
        self.save(data)
        diagnostics = self.diagnostics(session)
        # A saved PCB may show this same informational notice during loading,
        # before any bridge is attached. Handle only owned workspace copies.
        if path.is_relative_to(self.root.parent.resolve()) and any(
                w['visible'] and w['title'] in ('PCB Layout','PCB \u5e03\u5c40')
                for p in diagnostics['runtime']['processes'] for w in p['windows']):
            from .notices import drc_notice_watcher
            try:
                with drc_notice_watcher(self,session) as notice:
                    until = time.monotonic()+4
                    while time.monotonic()<until:
                        current = self.status(session)
                        if not any(w['visible'] and w['title'] in ('PCB Layout','PCB \u5e03\u5c40')
                                   for p in current['processes'] for w in p['windows']):
                            break
                        time.sleep(.1)
                data['known_notice_handling'] = notice
            except CircuitError as error:
                data['known_notice_handling'] = {'errors':[str(error)],'desktop_input':False}
            diagnostics = self.diagnostics(session)
        alive = diagnostics["runtime"]["alive"]
        visible = [w for p in diagnostics["runtime"]["processes"] for w in p["windows"] if w["visible"]]
        matching = [w for w in visible if path.stem.casefold() in w["title"].casefold()]
        state = "project_window_observed" if matching else "process_alive" if alive else "failed"
        data.update(status=state, project_window_observed=bool(matching), native_reopen_verified=False,
                    simulation_verified=False, evidence_dir=str(logs))
        if len(matching) == 1:
            data['project_hwnd'] = matching[0]['hwnd']
        self.save(data)
        return {k: v for k, v in data.items() if k != "observations"} | {"diagnostics": diagnostics}

    def close(self, session):
        state = self.status(session)
        if not state["alive"]:
            return {"status": "already_closed", "session": session}
        from ctypes import wintypes as w
        api = ctypes.WinDLL("user32", use_last_error=True)
        api.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
        sent = []
        targets = normal_close_targets(state,self.load(session))
        if not targets:
            return {'status':'close_blocked','session':session,'runtime':state,
                    'reason':'No unique enabled project main window; no dialogs were dismissed'}
        for window in targets:
            if api.PostMessageW(window['hwnd'],0x0010,0,0):
                sent.append(window['hwnd'])
        # DSIM teardown and project-save bookkeeping can outlive the QAction
        # state transition. Wait for the owned process, never force it closed.
        deadline = time.monotonic()+20
        current = self.status(session)
        while current["alive"] and time.monotonic() < deadline:
            if any(w["visible"] and w["class"] == "#32770" for p in current["processes"] for w in p["windows"]):
                break
            time.sleep(0.4)
            current = self.status(session)
        result = {"status": "closed" if not current["alive"] else "close_requested", "session": session,
                "close_sent_to": sent, "runtime": current,
                "reason": "Unsaved-change dialogs are never dismissed automatically"}
        atomic_write(self.session_path(session).parent/"close.json", json_bytes(result))
        return result
