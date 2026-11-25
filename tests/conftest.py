"""The tools' tests run with the other repositories beside this one (AAU-AASPIRE/): the line's specs
and simulator in ``aseptic-line``, the vocabulary in ``ontologies``, and for modlink the HMI's simulator
in ``iec61499-opcua-hmi`` (or ``$AASPIRE_HMI``). Live tests only launch an explicit executable, never
attach to a user's PLC."""
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent

# modgen's line: the aseptic line beside this repository; without it two of its modules (their specs
# of 24 Nov 2026, fixtures/), put into a folder of their own and generated there before modgen is
# imported, as a line's repository holds them (cell/modules, cell/control).
FIXTURE_CELL = None
if not os.environ.get("MODGEN_CELL") and not (WORKSPACE / "aseptic-line" / "cell" / "modules").is_dir():
    FIXTURE_CELL = Path(tempfile.mkdtemp(prefix="modgen-cell-")) / "cell"
    (FIXTURE_CELL / "modules").mkdir(parents=True)
    for spec in (Path(__file__).resolve().parent / "fixtures").glob("*.yaml"):
        shutil.copy(spec, FIXTURE_CELL / "modules" / spec.name)
    os.environ["MODGEN_CELL"] = str(FIXTURE_CELL)
    from modgen import SPECS, generate, load       # noqa: E402
    for spec in sorted(SPECS.glob("*.yaml")):
        generate(load(spec))

# modlink's tests run its modules against the HMI's simulator and read the HMI's profiles.
HMI = Path(os.environ.get("AASPIRE_HMI") or WORKSPACE / "iec61499-opcua-hmi")
sys.path.insert(0, str(ROOT))                      # this modlink, before the HMI's own copy
if HMI.is_dir():
    sys.path += [str(HMI), str(HMI / "tests")]
    collect_ignore = []
else:
    collect_ignore = ["test_modlink.py", "test_modlink_aas.py"]


def pytest_addoption(parser):
    """Register the live-test command line options."""
    parser.addoption("--module-forte-exe", help="FORTE built with every module (forte-runtime's build-modules.ps1)")
    parser.addoption("--pi-host", help="Run the live tests on this Raspberry Pi's FORTE (deploy's pi.py install)")
    parser.addoption("--pi-user", help="SSH login on the Pi (default: the pi target's user in the module's spec)")
    parser.addoption("--sim-host", help="This machine's address as the Pi sees it (the Pi's Modbus clients connect here)")
    parser.addoption("--pi-forte-exe", help="aarch64 FORTE (build-modules.ps1 -Config pi/modules-pi)")


def pytest_sessionfinish(session, exitstatus):
    if FIXTURE_CELL:
        shutil.rmtree(FIXTURE_CELL.parent, ignore_errors=True)


def free_port():
    """Return a free TCP port on localhost."""
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        return reservation.getsockname()[1]


@contextmanager
def launch_forte(executable, directory, runtime_dir=None, extra_args=()):
    """Start FORTE on a free loopback management port; yield the port; always terminate it."""
    environment = os.environ.copy()
    if runtime_dir:
        environment["PATH"] = str(Path(runtime_dir).resolve(strict=True)) + os.pathsep + environment.get("PATH", "")
    port = free_port()
    with (Path(directory) / "forte.log").open("w+b") as log:
        process = subprocess.Popen(
            [str(executable), "-c", f"127.0.0.1:{port}", *extra_args], cwd=directory, env=environment,
            stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            deadline = time.monotonic() + 15
            while True:
                if process.poll() is not None:
                    raise RuntimeError(f"FORTE exited with code {process.returncode}")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                        break
                except (ConnectionRefusedError, TimeoutError):
                    if time.monotonic() >= deadline:
                        raise TimeoutError("FORTE startup timed out; check DLL search PATH")
                    time.sleep(0.1)
            yield port
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            log.seek(0)
            print("FORTE log:", log.read().decode(errors="replace")[-3000:])
