import argparse
import json
import os
import sys
from pathlib import Path

from .server import Server
from .service import Service


def run():
    parser = argparse.ArgumentParser(description="Local Proteus semantic MCP; no assumed COM backend")
    parser.add_argument("--workspace", default=os.getenv("PROTEUS_MCP_WORKSPACE", str(Path(__file__).resolve().parents[1] / "workspaces")))
    parser.add_argument("--keil", default=os.getenv("PROTEUS_MCP_KEIL", "D:/KEIL5"))
    parser.add_argument("--proteus", default=os.getenv("PROTEUS_MCP_EXE"))
    parser.add_argument("--library", default=os.getenv("PROTEUS_MCP_LIBRARY", "D:/ProgramData/Labcenter Electronics/Proteus 9 Professional/Library"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve")
    commands.add_parser("doctor")
    call = commands.add_parser("call")
    call.add_argument("tool")
    call.add_argument("arguments", nargs="?", default="{}")
    args = parser.parse_args()
    service = Service(args.workspace, args.keil, args.proteus, args.library)
    if args.command == "serve":
        Server(service).serve()
        return 0
    if args.command == "doctor":
        value = service.doctor()
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
        return run()
    finally:
        from .bridge import shutdown_bridge
        shutdown_bridge()


if __name__ == "__main__":
    sys.exit(main())
