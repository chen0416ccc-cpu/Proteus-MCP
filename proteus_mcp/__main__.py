import argparse
import json
import os
import sys

from .server import Server
from .service import Service
from .model import CircuitError


def run():
    from .discovery import default_workspace
    parser = argparse.ArgumentParser(description="Local Proteus MCP interface")
    parser.add_argument("--workspace", default=str(default_workspace()))
    parser.add_argument("--keil")
    parser.add_argument("--proteus")
    parser.add_argument("--library")
    parser.add_argument("--calibration")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve")
    commands.add_parser("doctor")
    setup = commands.add_parser('setup', help='Prepare local native writer resources without manual placement')
    setup.add_argument('--verify-native', action='store_true', help='Also open, save and reopen the workspace template')
    call = commands.add_parser("call")
    call.add_argument("tool")
    call.add_argument("arguments", nargs="?", default="{}")
    args = parser.parse_args()
    service = Service(args.workspace, args.keil, args.proteus, args.library, args.calibration)
    if args.command == "serve":
        Server(service).serve()
        return 0
    if args.command == "doctor":
        value = service.doctor()
    elif args.command == 'setup':
        value = service.call('native_initialize', {'verify_native': args.verify_native})
    else:
        from .server import TOOLS, check_schema
        definition = next((tool for tool in TOOLS if tool["name"] == args.tool), None)
        if definition is None:
            parser.error("Unknown tool")
        arguments = json.loads(args.arguments)
        check_schema(arguments, definition["inputSchema"])
        value = service.call(args.tool, arguments)
    print(json.dumps(value, ensure_ascii=True, indent=2))
    return 1 if isinstance(value, dict) and value.get("status") in ("failed", "partial", "unsupported", "unconfirmed", "busy", "error") else 0


def main():
    try:
        try:
            return run()
        except (OSError, CircuitError) as error:
            print(json.dumps({'status': 'error', 'reason': str(error)}, ensure_ascii=True, indent=2))
            return 1
    finally:
        from .bridge import shutdown_bridge
        shutdown_bridge()


if __name__ == "__main__":
    sys.exit(main())
