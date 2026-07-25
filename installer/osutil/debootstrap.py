"""Pass B: debootstrap the target Debian Trixie system onto the freshly
created ZFS root at /target. Same debootstrap binary as Pass A (which built
this initrd's own rootfs) -- see initrd-plan.txt "Two debootstrap passes".

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
    log.info(f"debootstrap --variant=minbase {SUITE} -> {target}")
    subprocess.run(["debootstrap", "--arch=amd64", "--variant=minbase", SUITE, target, MIRROR],
                   check=True)
