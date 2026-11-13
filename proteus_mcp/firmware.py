"""Keil C51 toolchain execution without a shell, plus strict Intel HEX checks."""

import json
import re
import subprocess
import time
import uuid
from pathlib import Path

from .model import CircuitError, atomic_write, digest, json_bytes


def inspect_hex(path):
    data = Path(path).read_text(encoding="ascii")
    memory, base, eof = {}, 0, False
    for number, line in enumerate(data.splitlines(), 1):
        if not line:
            continue
        if eof or not re.fullmatch(r":[0-9A-Fa-f]+", line):
            raise CircuitError(f"Invalid HEX record at line {number}")
        try:
            record = bytes.fromhex(line[1:])
        except ValueError:
            raise CircuitError(f"Invalid HEX record at line {number}") from None
        if len(record) < 5 or len(record) != record[0] + 5 or sum(record) & 255:
            raise CircuitError(f"HEX length/checksum mismatch at line {number}")
        count, address, kind = record[0], int.from_bytes(record[1:3], "big"), record[3]
        payload = record[4:-1]
        if kind == 0:
            for offset, byte in enumerate(payload):
                target = base + address + offset
                if target > 65535 or target in memory:
                    raise CircuitError("HEX is outside 8051 code space or has overlapping records")
                memory[target] = byte
        elif kind == 1 and count == 0 and address == 0:
            eof = True
        elif kind in (2, 4) and count == 2 and address == 0:
            base = int.from_bytes(payload, "big") << (4 if kind == 2 else 16)
        elif kind in (3, 5) and count == 4 and address == 0:
            continue
        else:
            raise CircuitError(f"Unsupported or malformed HEX record type {kind}")
    if not eof or not memory or 0 not in memory:
        raise CircuitError("HEX missing EOF, code, or reset vector at address 0")
    return {"valid": True, "code_bytes": len(memory), "lowest_address": min(memory),
            "highest_address": max(memory), "reset_bytes": [memory.get(i) for i in range(3)],
            "sha256": digest(Path(path).read_bytes())}


LED_SOURCE = r'''#include <REG51.H>
#include <intrins.h>

/* Timer 0: 12 MHz / 12, 50 ms per overflow, four overflows per LED. */
static void delay_200ms(void)
{
    unsigned char i;
    for (i = 0; i < 4; ++i) {
        TR0 = 0;
        TF0 = 0;
        TH0 = 0x3C;
        TL0 = 0xB0;
        TR0 = 1;
        while (!TF0) { }
        TR0 = 0;
        TF0 = 0;
    }
}

void main(void)
{
    unsigned char pattern = 0xFE;
    P1 = 0xFF;
    TMOD = (TMOD & 0xF0) | 0x01;
    for (;;) {
        P1 = pattern;
        delay_200ms();
        pattern = _crol_(pattern, 1);
    }
}
'''


def build(folder, keil):
    folder, keil = Path(folder).resolve(), Path(keil).resolve()
    source = folder / "main.c"
    if not source.is_file():
        raise CircuitError("main.c not found; create source first")
    executables = [keil / "C51/BIN" / name for name in ("C51.exe", "BL51.exe", "OH51.exe")]
    if not all(path.is_file() for path in executables):
        raise CircuitError(f"Incomplete Keil C51 toolchain: {keil}")
    build_dir = folder / "builds" / (time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8])
    build_dir.mkdir(parents=True)
    original = source.read_bytes()
    atomic_write(build_dir / "main.c", original)
    include = keil / "C51/INC"
    commands = [
        [str(executables[0]), "main.c", f'INCDIR({include})', "OBJECT(main.obj)", "PRINT(main.lst)", "OPTIMIZE(8,SPEED)"],
        [str(executables[1]), "main.obj", "TO", "firmware", "PRINT(firmware.m51)"],
        [str(executables[2]), "firmware", "HEXFILE(firmware.hex)"],
    ]
    report = {"status": "building", "source_sha256": digest(original), "stages": [],
              "build_dir": str(build_dir), "simulation_verified": False}
    try:
        for command in commands:
            result = subprocess.run(command, cwd=build_dir, capture_output=True, timeout=60,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), shell=False)
            output = (result.stdout + result.stderr).decode("gb18030", errors="replace")
            report["stages"].append({"command": command, "returncode": result.returncode, "output": output})
            if result.returncode != 0 or re.search(r"\*\*\* (FATAL )?ERROR", output):
                raise CircuitError(f"Keil build failed: {Path(command[0]).name}")
        report["hex"] = inspect_hex(build_dir / "firmware.hex")
        if digest(source.read_bytes()) != report["source_sha256"]:
            raise CircuitError("Source changed during compilation; result not promoted")
        report["status"] = "compiled"
        report["hex_path"] = str(build_dir / "firmware.hex")
    except (subprocess.TimeoutExpired, OSError, ValueError) as error:
        report["status"] = "failed"
        report["error"] = str(error)
    atomic_write(build_dir / "build.json", json_bytes(report))
    atomic_write(build_dir / "build.log", "\n\n".join(stage["output"] for stage in report["stages"]).encode("utf-8"))
    atomic_write(folder / "latest-build.json", json_bytes(report))
    return report
