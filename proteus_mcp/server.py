"""Newline-delimited stdio MCP; direct runtime bridge uses optional Frida."""

import json
import sys

from . import __version__


def obj(properties=None, required=()):
    return {"type": "object", "properties": properties or {}, "required": list(required),
            "additionalProperties": False}


STR = {"type": "string"}
PROJECT = {"project": STR}
REVISION = {**PROJECT, "expected_revision": {"type": "integer", "minimum": 0}}
POSITION = {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2}
PROPERTIES = {"type": "object"}
ORIENTATION = {'rotation':{'type':'integer','enum':[0,90,180,270]},
               'mirror':{'type':'integer','minimum':0,'maximum':3}}


def tool(name, description, properties=None, required=(), read_only=False):
    return {"name": name, "description": description, "inputSchema": obj(properties, required),
            "annotations": {"readOnlyHint": read_only, "destructiveHint": not read_only,
                            "openWorldHint": False}}


TOOLS = [
    tool("proteus_doctor", "Report capability-specific health. Installed is not verified working.", read_only=True),
    tool("catalog_list", "List supported logical devices and pins; library_path explicitly selects ambiguous installed devices. NOT placement verification.", {"device": STR, 'library_path':STR}, read_only=True),
    tool("library_search", "Search the actual installed Proteus .LIB device index without mouse or screenshots. Finding a device does not imply native placement support.",
         {"query": STR, "limit": {"type": "integer", "minimum": 1}}, ["query"], True),
    tool('library_device', 'Read real installed device pins, geometry, hidden supplies, properties and source library. Optional explicit unit A..Z resolves supported homogeneous multi-unit packaging to physical pin numbers.',
         {'device':STR,'library_path':STR,'unit':STR}, ['device'], True),
    tool("project_create", "Create a NEW semantic circuit directory; never overwrite existing directories.", PROJECT, PROJECT),
    tool("project_read", "Read circuit revision and source hash.", PROJECT, PROJECT, True),
    tool("component_add", "Add a logical component; does NOT draw in Proteus. Use current expected_revision.",
         {**REVISION, "ref": STR, "device": STR, "position": POSITION, "properties": PROPERTIES, 'library_path':STR, **ORIENTATION},
         [*REVISION, "ref", "device", "position"]),
    tool("component_set", "Update logical properties or placement; native status becomes not_applied.",
         {**REVISION, "ref": STR, "properties": PROPERTIES, "position": POSITION, **ORIENTATION}, [*REVISION, "ref", "properties"]),
    tool("circuit_edit", "Apply an atomic revision-checked batch: add, set (properties/position), remove, connect, disconnect, merge_nets. Any invalid edit rolls back the entire batch. Export native afterward.",
         {**REVISION, "operations": {"type": "array", "items": {"type": "object"}, "minItems": 1, "maxItems": 1000}}, [*REVISION, "operations"]),
    tool("connect_pins", "Connect named pins in the model, e.g. U1.P1.0 to D1.K. Rejects implicit shorts across nets.",
         {**REVISION, "name": STR, "pins": {"type": "array", "items": STR, "minItems": 2}}, [*REVISION, "name", "pins"]),
    tool("project_validate", "Check model pin existence, MCU supplies/reset and floating passive pins. NOT full ERC.", PROJECT, PROJECT, True),
    tool("project_plan", "Export deterministic plan.json. Status is planned, not applied to Proteus.", PROJECT, PROJECT),
    tool("export_svg", "Export named-net connectivity preview.svg. This is NOT a Proteus screenshot or .pdsprj.", PROJECT, PROJECT),
    tool("export_netlist", "Export portable semantic netlist.json, NOT an importable Proteus native file.", PROJECT, PROJECT),
    tool("example_led_chaser", "Create a NEW 12MHz 12T 8051, P1 active-low 8-LED circuit and C51 main.c.", PROJECT, PROJECT),
    tool('example_external_interrupt','Create a NEW C51 teaching circuit/source for external interrupts: two BUTTON inputs, 8 LEDs, multiplexed 4-digit CC display and transistor-driven buzzer. mode=level: idle chaser, held low-level input increments with alarm; mode=nesting: low-priority INT0 chaser can be preempted by high-priority INT1 increment. Diagnostic nets expose ISR activity/increment/wrap. initial_count defaults to 0000; other values support explicit boundary tests. Build/export/open/observe separately. Not physical driver or audio certification.',
         {**PROJECT,'mode':{'type':'string','enum':['level','nesting']},'initial_count':{'type':'integer','minimum':0,'maximum':9999}},PROJECT),
    tool("source_write", "Write main.c; existing source requires matching expected_sha256 and is backed up.",
         {**PROJECT, "source": STR, "expected_sha256": STR}, [*PROJECT, "source"]),
    tool("firmware_build", "Actually run installed C51, BL51 and OH51; preserve logs and verify Intel HEX. NOT simulation.", PROJECT, PROJECT),
    tool("firmware_inspect", "Read Intel HEX and validate checksums, addresses, overlap and reset vector.", {"path": STR}, ["path"], True),
    tool("native_inspect", "Read .pdsprj ZIP contents and hashes. Does NOT claim electrical verification.", {"path": STR}, ["path"], True),
    tool("native_read", "Read calibrated native components, properties and stored pin references directly from .pdsprj. No UI. Not simulation verification.",
         {"path": STR}, ["path"], True),
    tool("native_preview", "Render an SVG from actual calibrated native component/pin/wire/junction geometry. This is a reconstructed vector preview, not a screenshot. New output only.",
         {"path": STR, "output": STR}, ["path"]),
    tool("native_create", "Create a NEW experimental empty native .pdsprj within an existing semantic project. No UI input. Use project_open/save to verify the generated instance.", PROJECT, PROJECT),
    tool("native_set_component", "Modify calibrated native component values, clock_hz or program in a NEW output file, requiring source SHA-256. No UI; never overwrites output.",
         {"path": STR, "output": STR, "ref": STR, "properties": PROPERTIES, "expected_sha256": STR},
         ["path", "output", "ref", "properties", "expected_sha256"]),
    tool("native_export_led_chaser", "Generate a native .pdsprj, C51 source and HEX from the exact example_led_chaser topology. Calibrated explicit wires and named nets. Each export still needs its own runtime verification.",
         PROJECT, PROJECT),
    tool("native_export", "Draw a NEW native schematic with calibrated symbols, rotation and mirroring. Auto placement uses network/port relationships, symbol/text footprints and three bounded candidates, not example-specific coordinates. Default continuous half-grid signal wires never fall back to labels. Shared export AND file-readback gates check required pin wires, physical continuity, shorts, endpoint geometry, estimated full-text bounds/overlaps, bodies, crossings and detour budgets. Unsupplied auto sheet size may grow and is written to the actual file. Explicit sheet/model coordinates and placements are respected; failure returns diagnostics, not a broken drawing. Net-label mode remains opt-in. Quality reports are not native visual or simulation verification.",
         {**PROJECT, "layout": {"type": "string", "enum": ["auto", "model"]},
          "routing": {"type": "string", "enum": ["auto", "wires", "labels", "schematic"]}, "placements": PROPERTIES,
          "sheet_size": {"type":"array", "items":{"type":"integer"}, "minItems":2, "maxItems":2},
          "wire_plan": {"type":"object", "description":"Ordered explicit orthogonal channels: junctions (@id -> half-grid pair), terminals (@id -> {net:VCC/GND,at:pair,label:optional layout}), wires ({net,ends:[pin or @id,pin or @id],points:[pairs]}), labels (net -> pair or layout), component_labels (component ref -> {ref:layout,value:layout}). Layout permits at (half-grid pair), height/width (0.4..4 grid), rotation (0/90/180/270), visible (boolean). Hiding preserves label/net identity, including supply terminals. All physical nets must still match the circuit model."}}, PROJECT),
    tool("project_open", "Open a .pdsprj directly, return once the project window settles, record processes and crash evidence. With session, reuse the same project or stop/save/close that owned session before switching files. No input emulation.",
         {"path": STR, "session": STR, "wait_seconds": {"type": "integer", "minimum": 1, "maximum": 30}}, ["path"]),
    tool("runtime_status", "Read running Proteus process, window and menu metadata. No screenshots or input emulation.", {"session": STR}, read_only=True),
    tool("runtime_diagnostics", "Collect Windows crash events, dump paths and process state for a tracked launch, saving diagnostics.json.", {"session": STR}, ["session"]),
    tool("project_close", "Request normal WM_CLOSE for a tracked Proteus session. Does not force termination or dismiss unsaved-change dialogs.", {"session": STR}, ["session"]),
    tool("project_reopen", "Save, verify circuit preservation, close normally, then reopen the same tracked project. Stops if saving or normal close is unconfirmed.", {"session": STR}, ["session"]),
    tool("native_status", "Read commands, logs, modal dialog and simulation state via version-pinned Qt function bridge. Optional Frida; no input emulation.", {"session": STR}, ["session"], True),
    tool('live_read', 'Read CURRENT owned ISIS sheet components, physical pin numbers/names, transforms and real wires. Returns revision token. No screenshots or input emulation.',
         {'session':STR}, ['session'], True),
    tool('live_edit', 'Edit the CURRENT owned ISIS sheet in the same PID/document using native functions. Stopped simulation and current live_read token required. Operations: add, transform, set, connect. Observed connected transforms are preflighted; a move producing diagonal/zero-length segments is rejected as LIVE_TRANSFORM_REQUIRES_REROUTE, not automatically rerouted. No live layout/font/frame certification. Deletion is a separate batch: {op:delete,ref,delete_connected_wires:false} permits direct attached component wires only when true; {op:delete_wire,wire_address} removes one exact wire including a branch; {op:delete_junction,junction_address,connected_wire_addresses:[all exact incident addresses]} removes a DOT and explicitly listed incident wires, never a whole net. Non-target geometry/labels are compared; native selections/tags are rejected. NOT atomic; partial/unconfirmed may have applied. Save/reopen for disk validation.',
         {'session':STR,'expected_token':STR,'operations':{'type':'array','items':{'type':'object'},'minItems':1,'maxItems':100}},
         ['session','expected_token','operations']),
    tool('live_history','Undo or redo exactly one recorded MCP live_edit batch in the same native document. Requires current live_read token, stopped simulation and matching semantic history. External edits, no-op or partial batches invalidate history. Does not emulate keyboard input.',
         {'session':STR,'expected_token':STR,'action':{'type':'string','enum':['undo','redo']}},['session','expected_token','action']),
    tool('native_commands','Discover native command IDs, owning modules, labels and enabled/checked states. Discovery is NOT verified execution. No arbitrary command execution is exposed.',
         {'session':STR,'query':STR,'module':STR},['session'],True),
    tool('native_check','Run a stopped-session native check. kind=erc requires a live_read token and a fresh electrical report. kind=drc requires a pcb_read token and refreshes the whole-design ARES physical clearance checker, reading real violations/ignored flags without a dialog. kind=preproduction runs all 23 native PPC checks, including connectivity CRC, on an owned workspace copy with a pcb_read token; restores original selections and closes only its own native PPC dialog. Cached/truncated/partial/disabled results, warnings, skips and ignored violations never count as clean. Current configured rules only, NOT fabrication certification or unrestricted full PCB DRC. Invocation status is separate from passed.',
         {'session':STR,'expected_token':STR,'kind':{'type':'string','enum':['erc','drc','preproduction']}},['session','expected_token']),
    tool('pcb_read', 'Export and read the CURRENT owned ARES PCB: native pad indices, nets, layers, board outline, rules, copper paths and calibrated through-vias including native drill/style/span. Stopped simulation required. Returns revision token; pad indices are NOT schematic physical pin numbers.',
         {'session':STR},['session'],True),
    tool('pcb_route', 'Add verified real TOP/BOT same-layer copper in the owned ARES board, preserving old wires and calibrated vias. Token, existing net and two native pad ids required. Explicit points_nm or conservative 8-direction A*. All pad/copper/via/edge clearance checked. Integer nm must be 10 nm aligned. Not full DRC or push/shove. Unknown geometry fails closed; NOT atomic. Save/reopen verifies disk copper.',
         {'session':STR,'expected_token':STR,'net':STR,'layer':STR,'from_pad':STR,'to_pad':STR,
          'width_nm':{'type':'integer','minimum':10000,'maximum':5000000},
          'clearance_nm':{'type':'integer','minimum':10000,'maximum':5000000},
          'grid_nm':{'type':'integer','minimum':10000,'maximum':1000000},
          'points_nm':{'type':'array','items':POSITION,'minItems':2,'maxItems':2048}},
         ['session','expected_token','net','layer','from_pad','to_pad']),
    tool('pcb_route_layers','Route between real through-hole pads across TOP/BOT with full-diameter calibrated through-vias. Requires fresh pcb_read token and native via_style. Automatic layer-state A* or explicit waypoints of {layer,position_nm}; a layer change repeats the same position. Preserves all old wires/vias and verifies exact native copper, style, hole, span and multiplicity. Only two-copper-layer rectangular boards with known geometry. NOT push/shove, blind/buried vias, full DRC or atomic. Save/reopen for disk verification.',
         {'session':STR,'expected_token':STR,'net':STR,'from_pad':STR,'to_pad':STR,
          'start_layer':{'type':'string','enum':['TOP','BOT']},'end_layer':{'type':'string','enum':['TOP','BOT']},
          'via_style':STR,'width_nm':{'type':'integer','minimum':10000,'maximum':5000000},
          'clearance_nm':{'type':'integer','minimum':10000,'maximum':5000000},
          'grid_nm':{'type':'integer','minimum':10000,'maximum':1000000},
          'max_vias':{'type':'integer','minimum':0,'maximum':8},
          'waypoints':{'type':'array','minItems':2,'maxItems':4096,'items':{'type':'object','additionalProperties':False,
              'properties':{'layer':{'type':'string','enum':['TOP','BOT']},'position_nm':POSITION},'required':['layer','position_nm']}}},
         ['session','expected_token','net','from_pad','to_pad']),
    tool('pcb_route_push','Bounded local push of ONE isolated MCP-created trace on TOP/BOT to clear an explicit new pad-to-pad path. mode=plan (default) does NOT change copper; mode=apply displaces an internal orthogonal segment, fixes both old pad endpoints, imports exact replacements and requires a fresh clean native clearance DRC. Supply current pcb_read token and explicit movable wire_ids from wire_inventory. No branches, via moves, cascades, arbitrary native/protected traces, native lock certification, or vendor interactive push engine. Non-atomic; an unconfirmed apply may have changed the board. Save/reopen for disk verification.',
         {'session':STR,'expected_token':STR,'net':STR,'layer':{'type':'string','enum':['TOP','BOT']},
          'from_pad':STR,'to_pad':STR,'mode':{'type':'string','enum':['plan','apply']},
          'points_nm':{'type':'array','items':POSITION,'minItems':2,'maxItems':2048},
          'movable_wire_ids':{'type':'array','items':STR,'minItems':1,'maxItems':16},
          'width_nm':{'type':'integer','minimum':10000,'maximum':5000000},
          'clearance_nm':{'type':'integer','minimum':10000,'maximum':5000000},
          'grid_nm':{'type':'integer','minimum':10000,'maximum':1000000},
          'max_displacement_nm':{'type':'integer','minimum':10000,'maximum':5000000}},
         ['session','expected_token','net','layer','from_pad','to_pad','points_nm','movable_wire_ids']),
    tool("project_save", "Queue native Save Project; back up and verify the scoped save. Confirm only the exact calibrated format-upgrade notice for workspace copies, never unknown dialogs. Schematic save compares disk circuit/cache; PCB save checks memory plus archive write and needs project_reopen for disk copper verification. No keyboard/mouse.", {"session": STR}, ["session"]),
    tool('pcb_route_push_chain','Plan/apply bounded multi-trace chain displacement on TOP/BOT. Up to eight explicitly allowed, current MCP-created isolated pad-to-pad traces may move internal orthogonal segments; fixed anchors and all other copper/vias are preserved. mode=plan is read-only, apply requires exact replacement and fresh native DRC. Not the vendor interactive engine; branch/via push and arbitrary locked traces remain unsupported. NOT atomic. Save/reopen for disk verification.',
         {'session':STR,'expected_token':STR,'net':STR,'layer':{'type':'string','enum':['TOP','BOT']},
          'from_pad':STR,'to_pad':STR,'mode':{'type':'string','enum':['plan','apply']},
          'points_nm':{'type':'array','items':POSITION,'minItems':2,'maxItems':2048},
          'movable_wire_ids':{'type':'array','items':STR,'minItems':1,'maxItems':8},
          'width_nm':{'type':'integer','minimum':10000,'maximum':5000000},
          'clearance_nm':{'type':'integer','minimum':10000,'maximum':5000000},
          'grid_nm':{'type':'integer','minimum':10000,'maximum':1000000},
          'max_displacement_nm':{'type':'integer','minimum':10000,'maximum':5000000}},
         ['session','expected_token','net','layer','from_pad','to_pad','points_nm','movable_wire_ids']),
    tool('manufacturing_bom','Export an audited UTF-8 CSV and JSON assembly BOM from a SAVED, DSN/CDB-consistent native project. Requires input SHA-256, counts each physical package once across units, reports unassigned packages separately, escapes CSV formulas, writes NEW workspace output only. Not unsaved-session data, Gerber/Excellon, verified PCB population or fabrication certification.',
         {'path':STR,'expected_sha256':STR},['path','expected_sha256']),
    tool("simulation_log", "Read actual Proteus simulation log rows directly from the native item model. No menu, screenshots or input emulation.", {"session": STR}, ["session"], True),
    tool('simulation_inputs','Read calibrated BUTTON/LOGICSTATE inputs in an owned RUNNING ISIS simulation. Returns epoch/context/EID-bound token. Start with simulation_control; restart invalidates old tokens. No desktop input.',
         {'session':STR},['session'],True),
    tool('simulation_stimulate','Deliver 1..32 calibrated native input events using a current simulation_inputs token. at_ms is a nondecreasing offset from the current native simulator time. Waits for actual time then dispatches at CURRENT time and records lateness. Delivery delays shift following events enough to preserve requested inter-event spacing, never compress a pulse or inject future events. Native clock is coarse, not hard realtime. BUTTON 0=release/1=press; LOGICSTATE 0=low/1=high. Runtime events only: persistent STATE/icon/disk is not edited. Non-atomic; partial/unconfirmed may have applied; no automatic restore. Dispatched does NOT prove electrical effect or ISR behavior. Saves complete sampled raw trace.',
         {'session':STR,'expected_token':STR,'events':{'type':'array','minItems':1,'maxItems':32,'items':obj(
             {'ref':STR,'value':{'type':'integer','enum':[0,1]},'at_ms':{'type':'integer','minimum':0,'maximum':20000}},['ref','value','at_ms'])},
          'nets':{'type':'array','items':STR,'minItems':1,'maxItems':64},
          'timeout_ms':{'type':'integer','minimum':100,'maximum':30000},
          'interval_ms':{'type':'integer','minimum':1,'maximum':1000}},['session','expected_token','events','nets']),
    tool('simulation_observe','Sample DSIM native network vectors with VSMDEBUG native simulation-time brackets. Strength-aware decoding only for calibrated state codes; unknowns remain unknown. Bounded polling, NOT a complete event waveform or analog/audio measurement. Saves full raw trace, returns summary/evidence. Running owned session only.',
         {'session':STR,'nets':{'type':'array','items':STR,'minItems':1,'maxItems':64},
          'wall_ms':{'type':'integer','minimum':1,'maximum':25000},'interval_ms':{'type':'integer','minimum':1,'maximum':1000},
          'max_samples':{'type':'integer','minimum':1,'maximum':20000}},['session','nets']),
    tool('simulation_decode_display','Decode a saved raw simulation_observe/stimulate JSON trace by actual segment/select net values. Supply source SHA-256, ordered A..G[,DP] segment_nets and left-to-right digit_nets. Requires exactly one selected digit and known stable states; reports composite frames, confirmations and rejected samples. This is sampled digital evidence, not optical output or complete transitions.',
         {'trace_path':STR,'expected_sha256':STR,'segment_nets':{'type':'array','items':STR,'minItems':7,'maxItems':8},
          'digit_nets':{'type':'array','items':STR,'minItems':1,'maxItems':8},
          'segment_active':{'type':'integer','enum':[0,1]},'digit_active':{'type':'integer','enum':[0,1]},
          'max_frame_ms':{'type':'integer','minimum':1,'maximum':2000}},
         ['trace_path','expected_sha256','segment_nets','digit_nets']),
    tool("simulation_sample", "Sample live native DSIM network STATE values and save evidence. Supply native network names, not semantic JSON net names. Raw states include strength bits.",
         {"session": STR, "nets": {"type": "array", "items": STR, "minItems": 1, "maxItems": 64},
          "seconds": {"type": "integer", "minimum": 1, "maximum": 25},
          "interval_ms": {"type": "integer", "minimum": 10, "maximum": 1000}}, ["session", "nets"]),
    tool("native_apply", "Export the semantic circuit into a NEW native file using general network/port-aware placement, continuous signal wires and shared quality/file-readback gates. No silent label fallback or example-specific auto layout. Does not modify a live session. Use project_open on its returned path; native visual/simulation verification remains separate.", PROJECT, PROJECT),
    tool("native_verify", "Autonomously open, save, close, reopen, compare circuit/cache, run and sample, pause/resume/stop, close and collect crash evidence. Net order is checked for calibrated active-low cyclic output. Creates file backups and reports.",
         {"path": STR, "nets": {"type": "array", "items": STR, "minItems": 2, "maxItems": 64},
          "seconds": {"type": "integer", "minimum": 3, "maximum": 25}}, ["path"]),
    tool("simulation_control", "Execute run, pause or stop using version-pinned Qt functions for an owned runtime session, then check native command state. A semantic project alone is insufficient.",
         {**PROJECT, "session": STR, "action": {"type": "string", "enum": ["run", "pause", "stop"]}}, ["action"]),
]


def check_schema(value, schema, path="arguments"):
    kind = schema.get("type")
    correct = {"object": lambda: isinstance(value, dict), "array": lambda: isinstance(value, list),
               "string": lambda: isinstance(value, str), "integer": lambda: type(value) is int,
               "boolean": lambda: type(value) is bool}
    if kind in correct and not correct[kind]():
        raise ValueError(f"{path}: expected {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path}: not in allowed enum")
    if kind == "integer" and value < schema.get("minimum", value):
        raise ValueError(f"{path}: below minimum")
    if kind == "integer" and value > schema.get("maximum", value):
        raise ValueError(f"{path}: above maximum")
    if kind == "object":
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False and value.keys() - properties.keys():
            raise ValueError(f"{path}: unknown fields {sorted(value.keys() - properties.keys())}")
        for key in schema.get("required", []):
            if key not in value:
                raise ValueError(f"{path}: missing {key}")
        for key, child in value.items():
            if key in properties:
                check_schema(child, properties[key], f"{path}.{key}")
    if kind == "array":
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 10000):
            raise ValueError(f"{path}: invalid item count")
        for child in value:
            check_schema(child, schema["items"], path + "[]")


class RpcError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message


class Server:
    def __init__(self, service):
        self.service = service
        self.initialized = False

    def dispatch(self, message):
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
            return self.error(None, -32600, "Invalid JSON-RPC request")
        method, request_id = message["method"], message.get("id")
        notification = "id" not in message
        if notification:
            return None
        try:
            params = message.get("params", {})
            if not isinstance(params, dict):
                raise RpcError(-32602, "params must be an object")
            if method == "initialize":
                self.initialized = True
                version = params.get("protocolVersion")
                supported = {"2024-11-05", "2025-03-26", "2025-06-18"}
                result = {"protocolVersion": version if version in supported else "2024-11-05",
                          "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": {"name": "local-proteus-mcp", "version": __version__},
                          "instructions": "Check proteus_doctor. Model edits are not native Proteus edits; do not claim simulation verification from compilation."}
            elif method == "ping":
                result = {}
            elif not self.initialized:
                raise RpcError(-32002, "Initialize first")
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                definition = next((t for t in TOOLS if t["name"] == params.get("name")), None)
                if definition is None:
                    raise RpcError(-32602, "Unknown tool")
                arguments = params.get("arguments", {})
                try:
                    check_schema(arguments, definition["inputSchema"])
                except ValueError as error:
                    raise RpcError(-32602, str(error)) from error
                try:
                    value = self.service.call(definition["name"], arguments)
                    failed = value.get("status") in ("failed", "partial", "unsupported", "unconfirmed", "busy", "error") if isinstance(value, dict) else False
                    result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=True)}],
                              "isError": failed}
                except (ValueError, OSError, KeyError) as error:
                    result = {"content": [{"type": "text", "text": json.dumps({"status": "error", "error": str(error)})}], "isError": True}
            else:
                raise RpcError(-32601, "Method not found")
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except RpcError as error:
            return self.error(request_id, error.code, error.message)
        except Exception as error:
            print(f"Internal error: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
            return self.error(request_id, -32603, "Internal error; inspect server stderr")

    @staticmethod
    def error(request_id, code, message):
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}

    def serve(self, stdin=None, stdout=None):
        stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
        for line in stdin:
            if not line.strip():
                continue
            try:
                if len(line) > 1024*1024:
                    raise ValueError("Request exceeds 1 MiB")
                message = json.loads(line)
                response = self.dispatch(message)
            except (ValueError, RecursionError) as error:
                response = self.error(None, -32700, f"Invalid JSON: {error}")
            if response is not None:
                stdout.write(json.dumps(response, ensure_ascii=True) + "\n")
                stdout.flush()
