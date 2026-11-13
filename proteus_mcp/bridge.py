"""Version-pinned direct Qt adapter. Optional Frida dependency is isolated."""

import importlib
import json
import os
import re
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .model import CircuitError, atomic_write, digest, json_bytes
from .build_profiles import ADAPTER_SOURCES, select_profile

BASE = Path(__file__).resolve().parents[1]
FRIDA_USED = False
LABELS = {"run": ["\u8fd0\u884c", "Run"], "pause": ["\u6682\u505c", "Pause"],
          "stop": ["\u505c\u6b62", "Stop"], "save": ["&\u4fdd\u5b58\u5de5\u7a0b", "&Save Project"],
          "simulation_log": ["\u67e5\u770b\u4eff\u771f\u65e5\u5fd7", "Simulation Log"]}


def frida_module():
    try:
        return importlib.import_module("frida")
    except ModuleNotFoundError:
        # Developer/standalone installs remain confined to the project.
        folder = BASE / "research/deps"
        if str(folder) not in sys.path:
            sys.path.append(str(folder))
        try:
            return importlib.import_module("frida")
        except ModuleNotFoundError as error:
            raise CircuitError("OPTIONAL_BRIDGE_DEPENDENCY_MISSING: install the package [bridge] extra in this Python environment") from error


def isolate_standard_handles():
    if os.name == "nt":
        import msvcrt
        # Frida's helper must not keep a one-shot client's redirected pipes open.
        for stream in (sys.stdin, sys.stdout, sys.stderr):
            try:
                os.set_handle_inheritable(msvcrt.get_osfhandle(stream.fileno()), False)
            except (OSError, ValueError):
                pass


def profile_check(folder, live=False, pcb=False, simulation=False):
    return select_profile(folder, live=live, pcb=pcb, simulation=simulation)[1]


def simulation_state(snapshot):
    def action(key):
        items = [a for a in snapshot["actions"] if a["text"] in LABELS[key]]
        return items[0] if len(items) == 1 else None
    run, pause, stop = (action(key) for key in ("run", "pause", "stop"))
    if not all((run, pause, stop)):
        return "unknown"
    if not stop["enabled"]:
        return "stopped"
    if pause["checked"] or run["enabled"]:
        return "paused"
    return "running"


def upgrade_confirmation_allowed(modal, path, workspace):
    """Only a calibrated save-format notice on an owned workspace copy is safe."""
    path, workspace = Path(path).resolve(), Path(workspace).resolve()
    if not path.is_relative_to(workspace) or path.suffix.lower() != '.pdsprj':
        return False
    if not modal or modal.get('class') != 'Appln::MessageBox' or modal.get('title') != 'Proteus 9 Professional':
        return False
    if modal.get('standard_buttons') != 0x400400 or modal.get('informative_text'):
        return False
    body = modal.get('text', '').replace('\0', '').replace('\r\n', '\n')
    pattern = (r"文件将以 9\.0\s+版本格式保存。\n此格式无法被旧版 Proteus 加载。\n\n"
               + r"先前版本将另存为 '" + re.escape(path.stem) + r" \[Proteus (?:8\.\d+|9\.0)\]\.pdsprj'。")
    return re.fullmatch(pattern, body) is not None


def shutdown_bridge():
    """Close Frida's device manager before a one-shot CLI process exits."""
    if FRIDA_USED and "frida" in sys.modules:
        sys.modules["frida"].shutdown()


class Bridge:
    def __init__(self, runtime):
        self.runtime = runtime

    @contextmanager
    def attach(self, session, live=False, pcb=False, simulation=False):
        live = live or simulation
        state = self.runtime.status(session)
        if len(state["processes"]) != 1:
            raise CircuitError("Tracked Proteus process is not alive or is ambiguous")
        process = state["processes"][0]
        profile, _ = select_profile(Path(process["ExecutablePath"]).parent, live=live, pcb=pcb, simulation=simulation)
        from .runtime import project_windows
        candidates = project_windows(process,self.runtime.load(session))
        if len(candidates) != 1:
            raise CircuitError("No unique Proteus project window in this session")
        frida = frida_module()
        isolate_standard_handles()
        global FRIDA_USED
        FRIDA_USED = True
        attached = frida.attach(process["ProcessId"])
        try:
            resources = ADAPTER_SOURCES[profile['adapter']]
            files = list(resources['qt'])
            if live:
                files.extend(resources['live'])
            if pcb:
                files.extend(resources['pcb'])
            if simulation:
                files.extend(resources['simulation'])
            source = '\n'.join((BASE/'proteus_mcp'/name).read_text(encoding='utf-8') for name in files)
            script = attached.create_script(source)
            script.load()
            yield script.exports_sync, str(candidates[0]["hwnd"])
        except Exception as error:
            raise CircuitError(f"QT_BRIDGE_ERROR: {error}") from error
        finally:
            attached.detach()

    def status(self, session):
        with self.attach(session) as (api, hwnd):
            snapshot = api.snapshot(hwnd)
        result = {"session": session, "backend": "experimental_qt_function_bridge", "desktop_input": False,
                  "simulation_state": simulation_state(snapshot), "snapshot": snapshot,
                  "simulation_verified": False}
        atomic_write(self.runtime.session_path(session).parent/"bridge-status.json", json_bytes(result))
        return result

    def log(self, session):
        status = self.status(session)
        rows = [row for item in status["snapshot"]["texts"] if item.get("name") == "logPopupView"
                for row in item.get("rows", []) if any(cell.strip() for cell in row)]
        unique = []
        for row in rows:
            if row not in unique:
                unique.append(row)
        result = {"status": "read", "session": session, "simulation_state": status["simulation_state"],
                  "rows": unique, "source": "Proteus native simulation log item model", "desktop_input": False}
        target = self.runtime.session_path(session).parent/(time.strftime("simulation-log-%Y%m%d-%H%M%S-")+uuid.uuid4().hex[:6]+".json")
        atomic_write(target, json_bytes(result))
        return {**result, "evidence_path": str(target)}

    def control(self, session, action):
        if action not in LABELS:
            raise CircuitError("Unregistered native command")
        folder = self.runtime.session_path(session).parent
        live = folder/'live-state.json'
        pending_live = live.is_file() and json.loads(live.read_text()).get('pending')
        if action == 'save' and not pending_live and (folder/'pcb-state.json').is_file():
            from .pcb import PCB
            return PCB(self,None).save(session)
        from .live import Live
        with Live(self,None).lock(session):
            return self._control(session,action)

    def _control(self, session, action):
        data = self.runtime.load(session)
        path = Path(data["path"])
        before_hash = digest(path.read_bytes())
        before_stat = path.stat()
        live_receipt = self.runtime.session_path(session).parent/'live-state.json'
        live_data = json.loads(live_receipt.read_text()) if action == 'save' and live_receipt.is_file() else {}
        pending_live = bool(live_data.get('pending'))
        if action == "save":
            from .native_backend import native_read, circuit_signature
            before_read = native_read(path)
            atomic_write(self.runtime.session_path(session).parent/f"before-save-{before_hash}.pdsprj", path.read_bytes())
        with self.attach(session, live=pending_live) as (api, hwnd):
            before = api.snapshot(hwnd)
            cancellations = []
            continuing_save = action == 'save' and upgrade_confirmation_allowed(before['modal'], path, self.runtime.root.parent)
            if action == 'save' and before['modal'] and not continuing_save:
                # Only the calibrated save-on-close prompt is cancellable.
                # Cancel preserves edits; the explicit save below is still verified.
                cancellations.append(api.cancelcloseforsave(hwnd,before['modal']))
                before = api.snapshot(hwnd)
            before_live = api.liveread(hwnd, live_data['property_keys']) if pending_live else None
            before_state = simulation_state(before)
            epoch_path = self.runtime.session_path(session).parent/'simulation-epoch.json'
            if action == 'stop' or (action == 'run' and before_state == 'stopped'):
                atomic_write(epoch_path, json_bytes({'epoch':uuid.uuid4().hex,'state':'unconfirmed',
                                                     'action':action,'session':session}))
            response = ({'dispatched':True, 'continued_prior_save':True} if continuing_save else
                        api.command(hwnd, LABELS[action], "Appln::Action" if action == "simulation_log" else ""))
            deadline = time.monotonic()+12
            confirmations = []
            previous_file, stable = None, 0
            while True:
                time.sleep(0.2)
                after = api.snapshot(hwnd)
                if action == 'save' and not confirmations and upgrade_confirmation_allowed(after['modal'], path, self.runtime.root.parent):
                    confirmations.append(api.confirmupgrade(hwnd, after['modal']))
                    continue
                after_state = simulation_state(after)
                if action == "save":
                    stat = path.stat()
                    signature = (stat.st_mtime_ns, stat.st_size)
                    stable = stable+1 if signature == previous_file else 0
                    previous_file = signature
                    done = stable >= 2 and signature != (before_stat.st_mtime_ns, before_stat.st_size)
                else:
                    done = after_state == {"run": "running", "pause": "paused", "stop": "stopped"}.get(action, after_state)
                if done or after["modal"] or time.monotonic() >= deadline:
                    break
            after_live = api.liveread(hwnd, live_data['property_keys']) if pending_live and not after['modal'] else None
        expected = {"run": "running", "pause": "paused", "stop": "stopped"}.get(action)
        success = not after["modal"] and (response.get("dispatched", False) or (expected is not None and before_state == expected)) and (expected is None or after_state == expected)
        if action in ('run','stop') and epoch_path.is_file():
            epoch = json.loads(epoch_path.read_text())
            atomic_write(epoch_path,json_bytes(epoch | {'state':after_state if success else 'unconfirmed'}))
        result = {"session": session, "action": action, "status": "verified" if success else "unconfirmed",
                  "command": response, "before_state": before_state, "after_state": after_state,
                  "desktop_input": False, "simulation_verified": False,
                  "before_sha256": before_hash, "after_sha256": digest(path.read_bytes()),
                  "texts": after["texts"], "modal": after["modal"]}
        result['known_dialog_confirmations'] = confirmations
        result['known_dialog_cancellations'] = cancellations
        if action == "save":
            # A save command needs an archive we can read after the write settles.
            read = native_read(path)
            if pending_live:
                from .live import disk_matches, state_token
                same = after_live is not None and state_token(before_live) == state_token(after_live) and disk_matches(after_live, read)
            else:
                same = circuit_signature(before_read) == circuit_signature(read)
            file_written = path.stat().st_mtime_ns != before_stat.st_mtime_ns or read["sha256"] != before_hash
            success = success and done and file_written and same and read["database_consistent"]
            result.update(native_readback=read, save_command_dispatched=response.get("dispatched", False),
                          save_file_written=file_written, circuit_preserved=same,
                          database_consistent=read["database_consistent"],
                          status="verified" if success else "unconfirmed")
            if pending_live:
                result.update(save_comparison='live_memory_before_after_and_disk_component_pin_projection',
                              live_memory_preserved=before_live == after_live)
                if success:
                    atomic_write(live_receipt, json_bytes({**live_data, 'pending':False, 'saved':True,
                                                         'saved_sha256':read['sha256']}))
        name = time.strftime("command-%Y%m%d-%H%M%S-") + action + "-" + uuid.uuid4().hex[:6] + ".json"
        atomic_write(self.runtime.session_path(session).parent/name, json_bytes(result))
        return result

    def sample(self, session, nets, seconds=5, interval_ms=25):
        if not isinstance(nets, list) or not 1 <= len(nets) <= 64 or any(not isinstance(n, str) or not re.fullmatch(r"[A-Za-z0-9_#+.:-]{1,64}", n) for n in nets):
            raise CircuitError("Provide 1 to 64 native net names")
        if type(seconds) is not int or not 1 <= seconds <= 25 or type(interval_ms) is not int or not 10 <= interval_ms <= 1000:
            raise CircuitError("Sampling bounds: 1..25 seconds, 10..1000 milliseconds")
        samples = []
        with self.attach(session) as (api, hwnd):
            before = api.snapshot(hwnd)
            initial_state = simulation_state(before)
            if initial_state not in ("running", "paused"):
                raise CircuitError("Run or pause an active native simulation before sampling")
            deadline = time.monotonic()+seconds
            while time.monotonic() < deadline:
                samples.append({"wall_time": time.time(), "nets": api.nets(nets)})
                time.sleep(interval_ms/1000)
        transitions = []
        for item in samples:
            values = [n["state"] for n in item["nets"]]
            if not transitions or transitions[-1]["states"] != values:
                transitions.append({"wall_time": item["wall_time"], "states": values})
        result = {"status": "sampled", "session": session, "native_nets": nets, "sample_count": len(samples),
                  "simulation_state": initial_state,
                  "missing_nets": sorted({n["name"] for item in samples for n in item["nets"] if not n["found"]}),
                  "unstable_snapshot_count": sum(any(not n.get("stable_snapshot", False) for n in item["nets"]) for item in samples),
                  "snapshot_method": "resolve all native pointers, then repeat complete vector reads until equal (maximum 10 passes)",
                  "transitions": transitions, "source": "DSIM.dll::dsim_linknet, live simulator STATE values",
                  "simulation_verified": False, "note": "Raw states include strength bits; wall_time is not simulation time"}
        target = self.runtime.session_path(session).parent/(time.strftime("samples-%Y%m%d-%H%M%S-")+uuid.uuid4().hex[:6]+".json")
        atomic_write(target, json_bytes({**result, "samples": samples}))
        return {**result, "evidence_path": str(target)}
