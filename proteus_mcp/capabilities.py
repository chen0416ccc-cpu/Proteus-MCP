"""Agent-facing capability boundaries, not a blanket verification certificate."""
import copy
import json
from pathlib import Path

from .model import digest,json_bytes


COVERAGE = [
    {'area':'project_lifecycle','implemented':True,
     'tools':['project_create','native_create','project_open','project_save','project_reopen','project_close'],
     'scope':'New native files and owned session open/save/reopen/normal close; explicit save verification',
     'not_supported':['adopt_arbitrary_foreign_session','force_close_unknown_modals']},
    {'area':'device_library','implemented':True,'tools':['library_search','library_device'],
     'scope':'Real installed library index, parsed definitions, properties, physical pinout, source hashes',
     'not_supported':['guessed_ambiguous_library','automatic_download_or_model_install']},
    {'area':'multi_unit_and_custom_pin','implemented':True,'tools':['library_device','live_read','live_edit'],
     'scope':'Explicit homogeneous packaging unit; hidden supply mapping; native instance custom-glyph coordinates',
     'not_supported':['heterogeneous_multi_unit','guess_unresolved_static_glyph_geometry','arbitrary_recursive_symbol_dependencies']},
    {'area':'semantic_schematic','implemented':True,
     'tools':['component_add','component_set','circuit_edit','connect_pins','project_validate','native_export','native_apply'],
     'scope':'Revision-checked semantic edits and calibrated NEW native file export; default continuous schematic trees with no silent label fallback, authored LED parallel channels, audited unconnected crossings and mandatory sheet bounds; explicit component/net label placement, size, rotation and visibility preserving net identity; opt-in legacy orthogonal A* and named nets',
     'not_supported':['unrestricted_native_file_format','all_devices_in_offline_export','automatic_font_raster_collision_certification']},
    {'area':'live_schematic','implemented':True,'tools':['live_read','live_edit','live_history'],
     'scope':'Owned current sheet add/connect/transform/set, exact wire/branch/DOT deletion with explicit incident-wire consent, and native undo/redo',
     'not_supported':['implicit_entire_net_delete','arbitrary_sheet_hierarchy_edit','live_batch_atomic_rollback','simulation_running_edits']},
    {'area':'pcb_copper_and_vias','implemented':True,'tools':['pcb_read','pcb_route','pcb_route_layers'],
     'scope':'Known rectangular two-layer TOP/BOT board, orthogonal/45-degree routes, calibrated circular through-vias',
     'not_supported':['blind_buried_vias','four_or_more_copper_layers','complex_planes_keepouts','unknown_pad_shapes','arbitrary_footprint_transform']},
    {'area':'pcb_bounded_local_push','implemented':True,'tools':['pcb_read','pcb_route_push'],
     'scope':'Plan/apply ONE isolated MCP-created trace internal orthogonal segment; fixed pad endpoints and fresh native DRC',
     'not_supported':['vendor_interactive_push_engine','cascade_push','via_push','branch_push','arbitrary_native_lock_certification']},
    {'area':'pcb_bounded_chain_push','implemented':True,'tools':['pcb_read','pcb_route_push_chain'],
     'scope':'Plan/apply up to eight explicitly allowed isolated MCP traces; bounded multi-trace search, fixed pad anchors, exact replacement and fresh DRC',
     'not_supported':['vendor_interactive_push_engine','via_push','branch_push','arbitrary_native_lock_certification','unbounded_optimal_routing']},
    {'area':'manufacturing_bom','implemented':True,'tools':['native_read','manufacturing_bom'],
     'scope':'Audited CSV/JSON assembly BOM of saved consistent DSN/CDB; physical-package deduplication, unassigned list and source hash',
     'not_supported':['unsaved_session_BOM','verified_PCB_population','fabrication_certification','bom_library_edit']},
    {'area':'native_rules_checks','implemented':True,'tools':['native_check'],
     'scope':'Fresh native ERC, whole-design physical-clearance DRC, and all 23 PPC checks including connectivity CRC',
     'not_supported':['fabricator_certification','automatic_rule_configuration_certification','clean_with_warnings_skips_ignored_truncated_or_stale_results']},
    {'area':'firmware_and_simulation','implemented':True,
     'tools':['source_write','firmware_build','firmware_inspect','simulation_control','simulation_log','simulation_sample','native_verify'],
     'scope':'Actual installed C51 build, owned run/pause/stop, log model and live named DSIM digital net states',
     'not_supported':['analog_current_sampling','oscilloscope_waveform_control','physical_hardware_flash_or_measurement']},
    {'area':'interactive_inputs_and_native_time','implemented':True,
     'tools':['simulation_inputs','simulation_stimulate','simulation_observe','simulation_decode_display','example_external_interrupt'],
     'scope':'Owned running ISIS BUTTON/LOGICSTATE events with epoch/EID tokens, actual simulator-time scheduling and bracketed raw digital traces',
     'not_supported':['persistent_icon_STATE_edit','arbitrary_animated_model_input','complete_event_waveform','analog_audio_measurement','hard_realtime_schedule']},
    {'area':'diagnostics_and_discovery','implemented':True,
     'tools':['proteus_doctor','runtime_status','runtime_diagnostics','native_status','native_commands'],
     'scope':'Version fingerprints, owned process identity, modals, native command IDs/states, crash events and dumps',
     'not_supported':['execute_arbitrary_discovered_command','discovery_is_verified_execution']},
    {'area':'pcb_component_placement_and_rules_edit','implemented':False,'tools':[],
     'scope':'Native command discovery only; no verified structural writer exposed',
     'not_supported':['move_or_add_footprint','board_stack_edit','net_class_rule_edit','copper_pour_or_teardrop_edit']},
    {'area':'advanced_eda_and_outputs','implemented':False,'tools':[],
     'scope':'No calibrated production adapter yet',
     'not_supported':['interactive_vendor_autorouter','differential_pair_tuning','length_matching','3d_board_edit',
                      'gerber_odb_pick_place_export','bom_library_edit','instrument_configuration','subcircuit_hierarchy_edit']},
]


def functional_coverage():
    return {'coverage_complete':False,'meaning':'Implemented adapter boundaries, not a claim that every native function is supported',
            'installed_or_discovered_is_runtime_verified':False,
            'native_verification_evidence':'Independent timestamped reports under workspaces/_acceptance and workspaces/_runtime',
            'areas':copy.deepcopy(COVERAGE)}


def source_fingerprint(base=None):
    base = Path(base) if base is not None else Path(__file__).resolve().parents[1]
    files = [path for path in (base/'proteus_mcp').iterdir() if path.suffix in ('.py','.js','.ps1')]
    files.extend(base/name for name in ('pyproject.toml','run_server.py') if (base/name).is_file())
    manifest = {path.relative_to(base).as_posix():digest(path.read_bytes()) for path in sorted(files)}
    return {'sha256':digest(json_bytes(manifest)),'files':manifest}


def acceptance_evidence(root, fingerprint):
    """A report from another source build is history, not current verification."""
    folders = sorted((Path(root)/'_acceptance').glob('????????-??????'),reverse=True)
    for folder in folders:
        path = folder/'report.json'
        if not path.is_file():
            continue
        try:
            if path.stat().st_size > 16000000:
                continue
            report = json.loads(path.read_text(encoding='utf-8'))
            before,after = report.get('source_before',{}),report.get('source_after',{})
            source_matches = before.get('sha256') == after.get('sha256') == fingerprint['sha256']
            checks = report.get('checks',{})
            passed = (report.get('status')=='passed' and report.get('server_exit_code')==0 and report.get('stderr')=='' and
                      bool(checks) and all(value is True for value in checks.values()))
            return {'status':'verified_for_current_source' if passed and source_matches else 'historical_or_unconfirmed',
                    'source_matches':source_matches,'report_passed':passed,'report_path':str(path),
                    'check_count':len(checks),'verification_scope':'Only the explicitly enumerated acceptance checks; not complete Proteus coverage'}
        except (OSError,ValueError,TypeError,AttributeError):
            continue
    return {'status':'not_recorded','source_matches':False,'report_passed':False}
