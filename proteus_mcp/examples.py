"""Reproducible, isolated first acceptance circuit."""

from .firmware import LED_SOURCE
from .model import atomic_write
from .led_layout import PLACEMENTS, ROTATIONS


def led_chaser(store, project):
    store.create(project)
    revision = 0

    def add(ref, device, x, y, **properties):
        nonlocal revision
        position = PLACEMENTS[ref]
        result = store.add(project, revision, ref, device, [position[0]*10, -position[1]*10],
                           properties, rotation=ROTATIONS.get(ref, 0))
        revision = result["revision"]

    def net(name, *pins):
        nonlocal revision
        result = store.connect(project, revision, name, list(pins))
        revision = result["revision"]

    add("U1", "AT89C51", 250, 300, clock_hz=12000000)
    for i in range(8):
        add(f"D{i+1}", "LED-RED", 620, 100+i*70)
        add(f"R{i+1}", "RES", 790, 100+i*70, value="330")
        net(f"LED{i+1}_K", f"U1.P1.{i}", f"D{i+1}.K")
        net(f"LED{i+1}_A", f"D{i+1}.A", f"R{i+1}.1")
    add("X1", "CRYSTAL", 160, 720, value="12MHz")
    add("C1", "CAP", 60, 820, value="22p")
    add("C2", "CAP", 260, 820, value="22p")
    add("C3", "CAP-ELEC", 60, 80, value="10u")
    add("R9", "RES", 60, 260, value="10k")
    add("C4", "CAP", 900, 720, value="100n")
    net("XTAL1", "U1.XTAL1", "X1.1", "C1.1")
    net("XTAL2", "U1.XTAL2", "X1.2", "C2.1")
    net("RESET", "U1.RST", "C3.-", "R9.1")
    net("GND", "U1.GND", "R9.2", "C1.2", "C2.2", "C4.2")
    net("VCC", "U1.VCC", "U1.EA", "C3.+", "C4.1", *(f"R{i+1}.2" for i in range(8)))
    atomic_write(store.folder(project) / "main.c", LED_SOURCE.encode("ascii"))
    return store.read(project)
