"""Pass B: debootstrap the target Debian Trixie system onto the freshly
created ZFS root at /target. Same debootstrap binary as Pass A (which built
this initrd's own rootfs).

--variant=minbase: apt-get install proxmox-ve (in proxmox/packages.py)
pulls in its own full dependency tree regardless of what's already
present, so there's nothing minbase omits here that doesn't just get
installed anyway once Proxmox actually needs it -- no point paying for a
fuller Debian base first and then largely re-covering the same ground
during the proxmox-ve install.
"""
import subprocess

SUITE = "trixie"
MIRROR = "http://deb.debian.org/debian"


def bootstrap(target, log):
    # This installer only ever runs on the same real hardware it's
    # bootstrapping for (the initrd was built for and boots on one specific
    # architecture) -- so the running environment's own architecture is
    # always the correct one to debootstrap, never a fixed assumption. Same
    # principle as boot/grub.py's UEFI-vs-BIOS detection: ask the real
    # environment, don't hardcode.
    arch = subprocess.run(["dpkg", "--print-architecture"], capture_output=True,
                           text=True, check=True).stdout.strip()
    log.info(f"debootstrap --arch={arch} --variant=minbase {SUITE} -> {target}")
    subprocess.run(["debootstrap", f"--arch={arch}", "--variant=minbase", SUITE, target, MIRROR],
                   check=True)
