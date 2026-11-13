"""Unattended native round trips and evidence-based active-low sequence checks."""

import json
import time
import uuid
from pathlib import Path

from .model import CircuitError, atomic_write, digest, json_bytes
from .native_backend import circuit_signature, native_read


def assess_sequence(sample, minimum_wraps=1):
    """Decode only the calibrated STATE codes, without assuming wall-clock speed."""
    count = len(sample["native_nets"])
    sequence, invalid = [], []
    for index, transition in enumerate(sample["transitions"]):
        values = transition["states"]
        if len(values) != count or any(value not in (5, 7) for value in values) or values.count(5) != 1:
            invalid.append({"transition": index, "states": values})
            continue
        active = values.index(5)
        if not sequence or sequence[-1] != active:
            sequence.append(active)
    skips = [{"from": a, "to": b} for a, b in zip(sequence, sequence[1:]) if b != (a+1) % count]
    wraps = sum(a == count-1 and b == 0 for a, b in zip(sequence, sequence[1:]))
    passed = not sample.get("missing_nets") and not sample.get("unstable_snapshot_count") and not invalid and not skips and wraps >= minimum_wraps and set(sequence) == set(range(count))
    return {"passed": passed, "active_indices_zero_based": sequence, "observed_wraps": wraps,
            "invalid_states": invalid, "skipped_or_reversed_transitions": skips,
            "calibration": {"logic_low": 5, "logic_high": 7},
            "scope": "live digital net sequence; LED optical output and analog current are not sampled",
            "timing": "sampling wall_time is not simulator time"}


def verify_native(service, path, nets=None, seconds=8):
    path = Path(path).resolve(strict=True)
    if type(seconds) is not int or not 3 <= seconds <= 25:
        raise CircuitError("verification seconds must be 3..25")
    nets = nets or [f"LED{i}_K" for i in range(1, 9)]
    identifier = time.strftime("%Y%m%d-%H%M%S-")+uuid.uuid4().hex[:8]
    evidence = service.store.root/"_verification"/identifier
    report = {"path": str(path), "started_epoch": time.time(), "desktop_input": False,
              "status": "running", "checks": {}, "sessions": [], "errors": []}
    report_path = evidence/"report.json"
    before = native_read(path)
    atomic_write(evidence/"before.pdsprj", path.read_bytes())
    report["before"] = before
    current = None

    def check(name, passed, details=None):
        report["checks"][name] = {"passed": bool(passed), "details": details}
        atomic_write(report_path, json_bytes(report))
        if not passed:
            raise CircuitError("NATIVE_VERIFICATION_FAILED: " + name)

    try:
        opened = service.runtime.open(path, 7)
        current = opened.get("session")
        if current:
            report["sessions"].append(current)
        check("open", opened.get("status") == "project_window_observed", opened)
        saved = service.bridge.control(current, "save")
        check("save_and_preserve_circuit", saved["status"] == "verified", saved)
        closed = service.runtime.close(current)
        check("normal_close", closed["status"] == "closed", closed)
        first_diagnostics = service.runtime.diagnostics(current)
        check("first_session_no_crash", not first_diagnostics["crash_dumps"] and
              not first_diagnostics["events"] and not first_diagnostics["collection_errors"], first_diagnostics)
        current = None
        opened = service.runtime.open(path, 7)
        current = opened.get("session")
        if current:
            report["sessions"].append(current)
        check("reopen", opened.get("status") == "project_window_observed", opened)
        saved = service.bridge.control(current, "save")
        after = saved["native_readback"]
        check("reopen_parameters_and_connectivity", saved["status"] == "verified" and circuit_signature(before) == circuit_signature(after), saved)
        run = service.bridge.control(current, "run")
        check("run", run["status"] == "verified", run)
        log = service.bridge.log(current)
        messages = [row[0] for row in log["rows"] if row]
        check("actual_firmware_load_log", any("Loading HEX file" in text for text in messages) and
              any("Read total of" in text and "bytes from file" in text for text in messages), log)
        sample = service.bridge.sample(current, nets, seconds=seconds, interval_ms=20)
        sequence = assess_sequence(sample)
        check("live_sequence", sequence["passed"], {"assessment": sequence, "sample": sample})
        paused = service.bridge.control(current, "pause")
        check("pause", paused["status"] == "verified", paused)
        frozen = service.bridge.sample(current, nets, seconds=1, interval_ms=25)
        check("paused_nets_frozen", not frozen["missing_nets"] and len(frozen["transitions"]) == 1, frozen)
        resumed = service.bridge.control(current, "run")
        check("resume", resumed["status"] == "verified", resumed)
        again = service.bridge.sample(current, nets, seconds=3, interval_ms=20)
        check("resumed_nets_change", not again["missing_nets"] and len(again["transitions"]) >= 3, again)
        stopped = service.bridge.control(current, "stop")
        check("stop", stopped["status"] == "verified", stopped)
        report.update(status="passed", native_reopen_verified=True, simulation_verified=True,
                      simulation_scope=sequence["scope"], after_sha256=digest(path.read_bytes()))
    except Exception as error:
        report.update(status="failed", native_reopen_verified=False, simulation_verified=False)
        report["errors"].append(f"{type(error).__name__}: {error}")
    finally:
        if current:
            try:
                status = service.bridge.status(current)
                if status["simulation_state"] in ("running", "paused"):
                    service.bridge.control(current, "stop")
                report["final_close"] = service.runtime.close(current)
                diagnostics = service.runtime.diagnostics(current)
                report["final_diagnostics"] = diagnostics
                check_closed = report["final_close"]["status"] == "closed"
                no_crash = not diagnostics["events"] and not diagnostics["crash_dumps"] and not diagnostics["collection_errors"]
                report["checks"]["final_normal_close_and_no_crash"] = {"passed": check_closed and no_crash}
                if not check_closed or not no_crash:
                    report["status"] = "failed"
                    report["simulation_verified"] = False
            except Exception as error:
                report["errors"].append(f"cleanup: {type(error).__name__}: {error}")
                report["status"] = "failed"
        if report["status"] != "passed":
            report["simulation_verified"] = False
        report.update(finished_epoch=time.time(), report_path=str(report_path))
        atomic_write(report_path, json_bytes(report))
    return {key: report.get(key) for key in ("status", "path", "report_path", "sessions", "native_reopen_verified", "simulation_verified", "simulation_scope", "errors")} | {
        "checks": {key: value["passed"] for key, value in report["checks"].items()}}
