"""Proxmox package installation, adapted from the verified upstream sequence
(pve.proxmox.com/wiki/Install_Proxmox_VE_on_Debian_13_Trixie) for a chroot
build rather than a live system:

Upstream: apt update && full-upgrade -> install proxmox-default-kernel ->
REBOOT into it -> install proxmox-ve postfix open-iscsi chrony -> remove
linux-image-amd64 + 'linux-image-6.12*' -> update-grub -> remove os-prober.

Here there's no live system to reboot mid-install -- the kernel is only
ever finalised once, in boot/grub.py's initramfs/grub regeneration at the
very end, after every package change below has already settled.
"""


def install(chroot, log):
    log.info("apt-get update && full-upgrade (target chroot)")
    chroot.run(["apt-get", "update"])
    chroot.run(["apt-get", "-y", "full-upgrade"])

    log.info("installing proxmox-default-kernel")
    chroot.run(["apt-get", "-y", "install", "proxmox-default-kernel"])

    log.info("installing proxmox-ve + postfix + open-iscsi + chrony + vim-tiny + nano")
    chroot.run(["apt-get", "-y", "install", "proxmox-ve", "postfix", "open-iscsi", "chrony",
                "vim-tiny", "nano"])

    log.info("removing the generic Debian kernel (proxmox-default-kernel replaces it)")
    result = chroot.run(["dpkg-query", "-W", "-f=${Package}\\n"], capture_output=True, text=True)
    debian_kernels = [p for p in result.stdout.splitlines()
                       if p.startswith("linux-image-") and "pve" not in p]
    if debian_kernels:
        chroot.run(["apt-get", "-y", "remove", "--purge", *debian_kernels])

    log.info("removing os-prober (per upstream install notes)")
    chroot.run(["apt-get", "-y", "remove", "--purge", "os-prober"], check=False)

    chroot.run(["apt-get", "-y", "autoremove", "--purge"])
