"""Tool implementations. Unsupported native operations fail explicitly."""

import json
import os
import platform
import sys
from pathlib import Path

from . import __version__
from .catalog import catalog
from .examples import led_chaser
from .exports import export_netlist, export_plan, export_svg, inspect_native
from .firmware import build, inspect_hex
from .library import LibraryIndex, library_scope
from .model import CircuitError, Store, atomic_write, digest, json_bytes, validate
from .native_backend import native_create, native_export, native_export_led_chaser, native_read, native_set_component
from .runtime import Runtime
from .bridge import Bridge


class Service:
    def __init__(self, root, keil=None, proteus=None, library=None, calibration=None):
        from .discovery import discover
        from .initialization import NativeInitialization
        self.store = Store(root)
        self.config = discover(proteus, library, keil)
        self.keil = self.config['keil']
        self.proteus = self.config['proteus']
        self.library = LibraryIndex(self.config['library'])
        self.initialization = NativeInitialization(self.store.root, self.config, calibration)
        self.runtime = Runtime(self.store.root, self.proteus)
        self.bridge = Bridge(self.runtime)
        from .live import Live
        self.live = Live(self.bridge, self.library)
        from .pcb import PCB
        self.pcb = PCB(self.bridge,self.library)
        from .checks import NativeChecks
        self.checks = NativeChecks(self.bridge,self.library)
        from .simulation import Simulation
        self.simulation = Simulation(self.bridge)
        from .observations import Observations
        self.observations = Observations(self.bridge)

    def doctor(self):
        from .capabilities import functional_coverage,source_fingerprint,acceptance_evidence
        keil_available = all((self.keil / "C51/BIN" / name).is_file()
                             for name in ("C51.exe", "BL51.exe", "OH51.exe"))
        from .bridge import frida_module
        from .build_profiles import diagnose_build
        from .discovery import configuration_report
        native_build = diagnose_build(self.proteus.parent)
        try:
            if not native_build['capabilities']['qt']['available']:
                raise CircuitError('NATIVE_BUILD_NOT_CALIBRATED: inspect native_build.capabilities.qt.comparisons')
            dependency = frida_module()
            bridge = {"available": os.name == 'nt', "experimental": True, "frida_version": dependency.__version__,
                      "desktop_input": False, "version_profile_matches": True,
                      "profile": native_build['capabilities']['qt']['profile'],
                      "transport": "Qt event loop + registered QAction; DSIM live network reads"}
        except (OSError, CircuitError, ImportError) as error:
            bridge = {"available": False, "reason": str(error)}
        extended = {}
        for name, capability in (('native_live_edit', 'live'), ('native_pcb_routing', 'pcb'),
                                 ('native_simulation_inputs', 'simulation')):
            match = native_build['capabilities'][capability]
            extended[name] = {'available': bool(bridge.get('available')) and match['available'],
                              'experimental': True, 'version_profile_matches': match['available'],
                              'profile': match['profile'], 'desktop_input': False}
            if not extended[name]['available']:
                extended[name]['reason'] = bridge.get('reason') if match['available'] else (
                    'NATIVE_BUILD_NOT_CALIBRATED: inspect native_build.capabilities.' + capability + '.comparisons')
        extended['native_live_edit'].update(operations=['add','transform','set','connect','delete','delete_wire','delete_junction'],atomic=False,
                                           delete='component_direct_wires_or_exact_wire_branch_and_junction_incident_allowlist',
                                           undo='recorded_MCP_frames',redo='recorded_MCP_frames')
        extended['native_simulation_inputs'].update(tools=['simulation_inputs','simulation_stimulate','simulation_observe','simulation_decode_display','example_external_interrupt'],
            devices=['BUTTON','LOGICSTATE'],runtime_events_only=True,native_simulator_time=True,
            desktop_input=False,analog_current=False,complete_waveform_capture=False)
        extended['joint_simulation_evidence'] = {
            'available':extended['native_simulation_inputs']['available'],
            'tools':['simulation_audio','simulation_diagnose','simulation_cold_start'],
            'audio_meter_platform_available':os.name == 'nt', 'recording':False,
            'volume_changes':False, 'optical_verification':False, 'analog_current':False,
            'cold_start_requires_stopped':True, 'native_runtime_verified':False}
        extended['native_live_edit'].update(multi_unit='homogeneous_explicit_REF_colon_unit',
                                           unresolved_pin_geometry='native_instance_coordinates_not_static_guesses')
        extended['native_pcb_routing'].update(layers=['TOP','BOT'],routing='layer_state_Astar_or_explicit_path',
                                             full_drc=False,vias='calibrated_circular_through_vias',push_shove=False,requires_native_roundtrip=True)
        extended['native_pcb_routing'].update(bounded_local_push='one_isolated_MCP_trace_internal_orthogonal_segment',
                                             local_push_tool='pcb_route_push',vendor_interactive_push_engine=False)
        extended['native_pcb_routing'].update(bounded_chain_push='up_to_eight_explicit_MCP_traces_fixed_pad_anchors',
                                             chain_push_tool='pcb_route_push_chain',via_push=False)
        extended['manufacturing_bom'] = {'available':True,'tool':'manufacturing_bom',
            'source':'saved_DSN_CDB_consistent_project','physical_package_deduplication':True,
            'unsaved_session':False,'fabrication_certified':False,'gerber_export':False,'desktop_input':False}
        extended['native_rules_checks'] = {
            'erc_available':extended['native_live_edit']['available'],
            'pcb_clearance_drc_available':extended['native_pcb_routing']['available'],
            'experimental':True,'tool':'native_check','desktop_input':False,
            'erc':'fresh_native_report_current_sheet_rules',
            'drc':'whole_design_native_physical_clearance_current_configured_rules',
            'ignored_violations_are_clean':False,'disabled_or_truncated_can_pass':False,
            'full_pcb_drc':False,'pre_production_checks':extended['native_pcb_routing']['available'],
            'pre_production_check_count':23,'connectivity_crc':extended['native_pcb_routing']['available'],
            'fabrication_certified':False,'original_check_selections_restored':True}
        source = source_fingerprint()
        initialization = self.initialization.inspect()
        return {"server_version": __version__, "python": sys.version.split()[0],
                'functional_coverage':functional_coverage(),
                'adapter_source_sha256':source['sha256'],
                'latest_acceptance':acceptance_evidence(self.store.root,source),
                "platform": platform.platform(), "workspace": str(self.store.root),
                'installation': configuration_report(self.config), 'native_build': native_build,
                'native_initialization': initialization,
                "capabilities": {
                    **extended,
                    "semantic_circuit": {"available": True},
                    "pin_validation": {"available": True, "full_erc": False},
                    "svg_preview": {"available": True, "native_proteus": False},
                    "keil_build": {"available": keil_available, "path": str(self.keil)},
                    "native_container_inspection": {"available": True},
                    "native_library_search": {"available": self.library.root.is_dir(), "path": str(self.library.root)},
                    "native_file_generation": {"available": initialization['available'], "experimental": True,
                                               "initialization_status": initialization['status'],
                                               "desktop_input": False, "native_reopen_verified": False,
                                               "scope": "six baseline devices plus parsed installed single-unit definitions with calibrated pin glyphs; native wires, junctions, power and net labels",
                                               "rotation_degrees":[0,90,180,270],"mirror_flags":[0,1,2,3]},
                    "native_schematic_write": {"available": initialization['available'], "experimental": True,
                                               "routing": ["continuous_schematic_default", "explicit_ordered_channels", "opt_in_wires", "opt_in_labels"],
                                               "silent_label_fallback": False, "sheet_bounds_required": True,
                                               "scope":"new-file writer; current-session edits are native_live_edit",
                                               "live_edit": False},
                    "native_simulation_control": bridge,
                    "runtime_project_launch": {"available": os.name == "nt" and self.proteus.is_file(), "desktop_input": False}},
                "proteus": {"installed": self.proteus.is_file(), "path": str(self.proteus),
                            "running": "not_probed", "com_api": "not_assumed"}}

    def source_write(self, project, source, expected_sha256=None):
        folder = self.store.folder(project)
        self.store.read(project)
        if not isinstance(source, str) or not 1 <= len(source.encode("utf-8")) <= 128000:
            raise CircuitError("source must be nonempty and at most 128000 UTF-8 bytes")
        with self.store.lock(project):
            path = folder / "main.c"
            actual = digest(path.read_bytes()) if path.exists() else None
            if actual != expected_sha256:
                raise CircuitError(f"SOURCE_CONFLICT: current_sha256={actual}")
            if path.exists():
                atomic_write(folder / "source-backups" / f"{actual}.c", path.read_bytes())
            atomic_write(path, source.encode("utf-8"))
        return {"path": str(path), "sha256": digest(path.read_bytes()), "status": "source_written"}

    def open_project(self, path, wait_seconds=12, session=None):
        target = Path(path).resolve(strict=True)
        if not session:
            return self.runtime.open(target, wait_seconds)
        current = self.runtime.load(session)
        status = self.runtime.status(session)
        if not status["alive"]:
            return self.runtime.open(target, wait_seconds)
        if Path(current["path"]).resolve() == target:
            return {"status": "already_open", "session": session, "path": str(target), "runtime": status}
        state = self.bridge.status(session)
        if state["simulation_state"] in ("running", "paused"):
            stopped = self.bridge.control(session, "stop")
            if stopped["status"] != "verified":
                return {"status": "unconfirmed", "stop": stopped}
        saved = self.bridge.control(session, "save")
        if saved["status"] != "verified":
            return {"status": "unconfirmed", "save": saved}
        closed = self.runtime.close(session)
        if closed["status"] != "closed":
            return {"status": "busy", "close": closed}
        return {**self.runtime.open(target, wait_seconds), "previous_session": session}

    def call(self, name, arguments):
        with library_scope(self.library.root):
            return self._call(name, arguments)

    def _call(self, name, arguments):
        s = self.store
        if name == "proteus_doctor":
            return self.doctor()
        if name == 'native_initialize':
            return self.initialization.initialize(runtime=self.runtime, bridge=self.bridge, **arguments)
        if name == "catalog_list":
            return {"devices": catalog(arguments.get("device"), arguments.get('library_path')), "native_pin_mapping_verified": False}
        if name == "library_search":
            return self.library.search(**arguments)
        if name == 'library_device':
            from .devices import device_info
            return device_info(**arguments)
        if name == "project_create":
            return s.create(**arguments)
        if name == 'simulation_inputs':
            return self.simulation.inputs(**arguments)
        if name == 'simulation_audio':
            return self.observations.audio(**arguments)
        if name == 'simulation_diagnose':
            return self.observations.diagnose(**arguments)
        if name == 'simulation_cold_start':
            return self.observations.cold_start(**arguments)
        if name == 'simulation_stimulate':
            return self.simulation.stimulate(**arguments)
        if name == 'simulation_observe':
            return self.simulation.observe(**arguments)
        if name == 'simulation_decode_display':
            from .display import decode_display
            return decode_display(s.root,**arguments)
        if name == "project_read":
            value = s.read(**arguments)
            source = s.folder(arguments["project"]) / "main.c"
            return {**value, "source_sha256": digest(source.read_bytes()) if source.is_file() else None}
        if name == "component_add":
            return s.add(**arguments)
        if name == "component_set":
            return s.set_component(**arguments)
        if name == "circuit_edit":
            return s.edit(**arguments)
        if name == "connect_pins":
            return s.connect(**arguments)
        if name == "project_validate":
            return validate(s.read(**arguments))
        if name == "project_plan":
            return export_plan(s, **arguments)
        if name == "export_svg":
            return export_svg(s, **arguments)
        if name == "export_netlist":
            return export_netlist(s, **arguments)
        if name == "example_led_chaser":
            return led_chaser(s, **arguments)
        if name == 'example_external_interrupt':
            from .interrupts import external_interrupt
            return external_interrupt(s, **arguments)
        if name == "source_write":
            return self.source_write(**arguments)
        if name == "firmware_build":
            s.read(**arguments)
            with s.lock(arguments["project"]):
                return build(s.folder(arguments["project"]), self.keil)
        if name == "firmware_inspect":
            return inspect_hex(**arguments)
        if name == "native_inspect":
            return inspect_native(**arguments)
        if name == "native_read":
            return native_read(**arguments)
        if name == 'manufacturing_bom':
            from .manufacturing import export_bom
            return export_bom(s.root, **arguments)
        if name == 'pcb_route_push_chain':
            return self.pcb.route_push(**arguments, chain=True)
        if name == "native_preview":
            from .preview import native_preview
            return native_preview(**arguments)
        if name == "native_create":
            return native_create(s, calibration=self.initialization.ensure(), **arguments)
        if name == "native_set_component":
            return native_set_component(**arguments)
        if name == "native_export_led_chaser":
            return native_export_led_chaser(s, calibration=self.initialization.ensure(), **arguments)
        if name in ("native_export", "native_apply"):
            return native_export(s, calibration=self.initialization.ensure(), **arguments)
        if name == "project_open":
            return self.open_project(**arguments)
        if name == "runtime_status":
            return self.runtime.status(**arguments)
        if name == "runtime_diagnostics":
            return self.runtime.diagnostics(**arguments)
        if name == "project_close":
            return self.runtime.close(**arguments)
        if name == "project_reopen":
            session = arguments["session"]
            path = self.runtime.load(session)["path"]
            status = self.bridge.status(session)
            if status["simulation_state"] in ("running", "paused"):
                stopped = self.bridge.control(session, "stop")
                if stopped["status"] != "verified":
                    return {"status": "unconfirmed", "stop": stopped}
            saved = self.bridge.control(session, "save")
            if saved["status"] != "verified":
                return {"status": "unconfirmed", "save": saved}
            closed = self.runtime.close(session)
            if closed["status"] != "closed":
                return {"status": "busy", "close": closed}
            opened = self.runtime.open(path, 30)
            result = {**opened, 'previous_session':session, 'save':saved}
            live_receipt = self.runtime.session_path(session).parent/'live-state.json'
            if live_receipt.is_file() and opened['status'] == 'project_window_observed':
                previous_live = json.loads(live_receipt.read_text())
                atomic_write(self.runtime.session_path(opened['session']).parent/'live-state.json',
                             json_bytes({'session':opened['session'],'property_keys':previous_live.get('property_keys',[]),
                                         'pending':False,'saved':True,'history_reset_on_reopen':True}))
            receipt = self.runtime.session_path(session).parent/'pcb-state.json'
            if receipt.is_file() and opened['status'] == 'project_window_observed':
                previous = json.loads(receipt.read_text())
                try:
                    readback = self.pcb.read(opened['session'])
                except CircuitError as error:
                    # Keep ownership of the NEW session visible to the caller
                    # if verification is blocked by a modal or unknown layout.
                    return result | {'status':'unconfirmed','pcb_disk_roundtrip_verified':False,
                                     'verification_error':str(error)}
                verified = previous.get('saved_token') == readback['token']
                result.update(pcb_disk_roundtrip_verified=verified,pcb_readback=readback)
                if not verified:
                    result['status'] = 'unconfirmed'
                atomic_write(self.runtime.session_path(opened['session']).parent/'pcb-state.json',
                             json_bytes({**previous,'session':opened['session'],'disk_roundtrip_verified':verified}))
            return result
        if name == "native_status":
            return self.bridge.status(**arguments)
        if name == 'live_read':
            return self.live.read(**arguments)
        if name == 'live_edit':
            return self.live.edit(**arguments)
        if name == 'live_history':
            return self.live.history(**arguments)
        if name == 'native_commands':
            return self.checks.commands(**arguments)
        if name == 'native_check':
            return self.checks.check(**arguments)
        if name == 'pcb_read':
            return self.pcb.read(**arguments)
        if name == 'pcb_route':
            return self.pcb.route(**arguments)
        if name == 'pcb_route_layers':
            return self.pcb.route_layers(**arguments)
        if name == 'pcb_route_push':
            return self.pcb.route_push(**arguments)
        if name == "project_save":
            return self.bridge.control(action="save", **arguments)
        if name == "simulation_log":
            return self.bridge.log(**arguments)
        if name == "simulation_control":
            if "session" in arguments:
                return self.bridge.control(session=arguments["session"], action=arguments["action"])
        if name == "simulation_sample":
            return self.bridge.sample(**arguments)
        if name == "native_verify":
            from .verification import verify_native
            return verify_native(self, **arguments)
        if name == "simulation_control":
            return {"status": "unsupported", "code": "NATIVE_BACKEND_NOT_VERIFIED",
                    "applied": False, "simulation_verified": False,
                    "reason": "Semantic plans and previews do not modify Proteus. Native backend is not verified."}
        raise CircuitError(f"Unknown tool: {name}")
