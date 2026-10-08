"""Boot real firmware in QEMU against our server: iPXE (BIOS) and EDK2 UEFI PXE.

Not collected by pytest (no ``test_`` prefix): it needs root, Linux,
qemu-system-x86_64, the iPXE ROMs, OVMF and dnsmasq, and takes minutes under
emulation. Run::

    sudo python tests/integration/firmware_boot.py [ipxe|uefi ...]

It builds a small boot network: a tap interface (192.168.77.1/24), dnsmasq
doing **DHCP only** (its TFTP is off) and pointing PXE at 192.168.77.1, and
our server listening on 192.168.77.1:69. QEMU's user-mode network cannot be
used: it answers port 69 on its gateway itself. Prints each transfer the
server completed, then a verdict per firmware.

Both pass, measured 2026-10-08 on a KVM host (QEMU 11.0.3, dnsmasq 2.91):

``ipxe``: iPXE fetches its script and a 2 MB file with ``blksize=1432`` and
``tsize=0``.

``uefi``: EDK2's PXE client, from the OVMF of pve-edk2-firmware 4.2026.08
(``OVMF_CODE.fd``, the 2 MB image) with a virtio-net device and the
IPv4PXESupport fw_cfg knob. It sends two requests for the boot file. The first
carries ``tsize=0 blksize=1468 windowsize=4`` and is ended by the client with
ERROR 8 as soon as the OACK has told it the size; the second, without
``tsize``, downloads the file in blocks of 1468 octets and windows of four.
The first one is therefore reported as a failed transfer, and is not one.

Not every OVMF build boots from the network. Fedora 44's edk2-ovmf 20260812
(2M and 4M images, virtio-net and e1000, with and without the NIC option ROM,
with the same knob) prints "No bootable option or device was found" and sends
no DHCP request. Name another image with ``OVMF_CODE`` in the environment.
With ``/dev/kvm`` usable a boot takes seconds; emulated, about three minutes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import tftp

TAP = "tftptap0"
HOST = "192.168.77.1"
#: Where a firmware image is looked for, in order. ``OVMF_CODE`` in the
#: environment names one outright. Not every build can boot from the network:
#: see the module docstring for which did.
OVMF_CANDIDATES = [
    os.environ.get("OVMF_CODE", ""),
    "/usr/share/pve-edk2-firmware/OVMF_CODE.fd",
    "/usr/share/edk2/ovmf/OVMF_CODE.fd",
    "/usr/share/OVMF/OVMF_CODE.fd",
    "/usr/share/ovmf/OVMF.fd",
]


def _accel() -> list:
    """Hardware virtualisation where the host offers it: a boot takes seconds, not minutes."""
    return ["-accel", "kvm"] if os.access("/dev/kvm", os.R_OK | os.W_OK) else []


class Lab:
    """Tap interface + dnsmasq (DHCP) + our server, torn down on exit."""

    def __init__(self, workdir: str, bootfile: str) -> None:
        self.workdir = workdir
        self.bootfile = bootfile
        self.results: list = []
        self.events: list = []

    def __enter__(self) -> "Lab":
        run = lambda *a: subprocess.run(a, check=True, capture_output=True)  # noqa: E731
        subprocess.run(["ip", "link", "del", TAP], capture_output=True)
        run("ip", "tuntap", "add", "dev", TAP, "mode", "tap")
        run("ip", "addr", "add", HOST + "/24", "dev", TAP)
        run("ip", "link", "set", TAP, "up")
        self.dhcp = subprocess.Popen(
            [
                "dnsmasq",
                "--no-daemon",
                "--port=0",
                "--interface=" + TAP,
                "--bind-interfaces",
                "--dhcp-range=192.168.77.50,192.168.77.99,1h",
                "--dhcp-boot=%s,,%s" % (self.bootfile, HOST),
                "--conf-file=/dev/null",
                "--pid-file=",
                "--dhcp-leasefile=" + os.path.join(self.workdir, "leases"),
                "--log-dhcp",
                "--log-facility=" + os.path.join(self.workdir, "dhcp.log"),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.server = tftp.TFTPServer(
            self.workdir,
            host="0.0.0.0",
            port=69,
            on_complete=self.results.append,
            trace=self.events.append,
            timeout=1.0,
        ).start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.close()
        self.dhcp.terminate()
        self.dhcp.wait(5)
        subprocess.run(["ip", "link", "del", TAP], capture_output=True)
        try:
            with open(os.path.join(self.workdir, "dhcp.log"), errors="replace") as handle:
                self.dhcp_log = [line.rstrip() for line in handle if "DHCP" in line]
        except OSError:
            self.dhcp_log = []


def boot(args, seconds: float, until: str = "") -> str:
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    output = []

    def pump():
        for line in iter(proc.stdout.readline, b""):
            output.append(line.decode(errors="replace"))

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    deadline = time.monotonic() + seconds
    while proc.poll() is None and time.monotonic() < deadline:
        if until and until in "".join(output):
            break
        time.sleep(0.5)
    if proc.poll() is None:
        proc.kill()
    proc.wait()
    reader.join(2)
    return "".join(output)


def _fmt(path: str) -> str:
    return "qcow2" if path.endswith(".qcow2") else "raw"


def _net(device: str) -> list:
    return ["-netdev", "tap,id=n0,ifname=%s,script=no,downscript=no" % TAP, "-device", device]


def _report(name: str, lab: Lab) -> None:
    # What the firmware asked the DHCP server: none at all means it never tried the network.
    for line in lab.dhcp_log[:12]:
        print("  %s dhcp: %s" % (name, line.split(": ", 1)[-1]))
    for result in lab.results:
        print("  %s: %r" % (name, result))
    requests = [e for e in lab.events if e.opcode_name in ("RRQ", "WRQ")]
    for event in requests:
        print("  %s request: %s" % (name, event.summary))


def ipxe(workdir: str) -> bool:
    payload = os.urandom(2_000_000)
    with open(os.path.join(workdir, "boot.ipxe"), "w", newline="\n") as handle:
        handle.write(
            "#!ipxe\necho pytftp: script loaded\nimgfetch tftp://%s/big.bin && echo pytftp: FETCHED\npoweroff\n"
            % HOST
        )
    with open(os.path.join(workdir, "big.bin"), "wb") as handle:
        handle.write(payload)
    with Lab(workdir, "boot.ipxe") as lab:
        out = boot(
            [
                "qemu-system-x86_64",
                *_accel(),
                "-nographic",
                "-m",
                "256",
                "-boot",
                "n",
                "-no-reboot",
                *_net("e1000,netdev=n0"),
            ],
            240,
            until="pytftp: FETCHED",
        )
        time.sleep(0.5)
    _report("ipxe", lab)
    ok = any(r.filename == "big.bin" and r.is_ok and r.bytes == len(payload) for r in lab.results)
    if not ok:
        print(out[-2500:])
    return ok and "pytftp: FETCHED" in out


def uefi(workdir: str) -> bool:
    ovmf = next((p for p in OVMF_CANDIDATES if p and os.path.exists(p)), None)
    if ovmf is None:
        print("  uefi: no OVMF found")
        return False
    payload = os.urandom(3_000_000)  # not a valid EFI image: the firmware downloads it, then gives up
    with open(os.path.join(workdir, "bootx64.efi"), "wb") as handle:
        handle.write(payload)
    vars_template = ovmf.replace("OVMF_CODE", "OVMF_VARS")
    vars_file = os.path.join(workdir, "vars" + os.path.splitext(ovmf)[1])
    shutil.copy(vars_template if os.path.exists(vars_template) else ovmf, vars_file)
    with Lab(workdir, "bootx64.efi") as lab:
        out = boot(
            [
                "qemu-system-x86_64",
                *_accel(),
                "-nographic",
                "-m",
                "512",
                "-machine",
                "q35",
                "-no-reboot",
                "-drive",
                "if=pflash,format=%s,readonly=on,file=%s" % (_fmt(ovmf), ovmf),
                "-drive",
                "if=pflash,format=%s,file=%s" % (_fmt(vars_file), vars_file),
                # Recent OVMF builds ship with network boot off; this knob turns it on.
                "-fw_cfg",
                "name=opt/org.tianocore/IPv4PXESupport,string=yes",
                # The NIC's option ROM (iPXE's EFI build) only supplies the network
                # driver (SNP); OVMF has none of its own for virtio-net. The PXE
                # boot itself -- DHCP, then MTFTP -- is EDK2's PxeBcDxe on top.
                *_net(os.environ.get("UEFI_NIC", "virtio-net-pci") + ",netdev=n0,bootindex=1"),
                "-boot",
                "n",
            ],
            170,
            until="Access Denied",
        )
        time.sleep(0.5)
    _report("uefi", lab)
    ok = any(r.filename == "bootx64.efi" and r.is_ok and r.bytes == len(payload) for r in lab.results)
    if not ok:
        print(out[-2500:])
    return ok


def main() -> int:
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        print("needs root on Linux")
        return 2
    for tool in ("qemu-system-x86_64", "dnsmasq", "ip"):
        if shutil.which(tool) is None:
            print("needs", tool)
            return 2
    wanted = sys.argv[1:] or ["ipxe", "uefi"]
    verdicts = {}
    for name in wanted:
        with tempfile.TemporaryDirectory() as workdir:
            os.chmod(workdir, 0o755)
            print("== %s" % name, flush=True)
            verdicts[name] = {"ipxe": ipxe, "uefi": uefi}[name](workdir)
    print("verdicts:", verdicts)
    return 0 if all(verdicts.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
