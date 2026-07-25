"""hostid, initramfs regeneration, and bootloader installation to every disk.

Verified against a real, working PVE install (2026-07-24) rather than
guessed: Proxmox never lets GRUB read the kernel/initrd off the ZFS root at
all. The real reference system's /boot/grub/grub.cfg says so explicitly:
"This system is booted via proxmox-boot-tool! ... /boot/grub/grub.cfg is NOT
read when booting from those disks!" -- the config actually used at boot
lives on the ESP itself, entirely separate from the ZFS-hosted /boot. Two
earlier theories tried here (restricting zpool feature flags for GRUB
compatibility; giving /boot its own ext4 partition) were both ruled out by
that same real system: it has a full/default ZFS feature set (`zpool get
compatibility` -> off) and only the same three partitions per disk
(bios_boot/ESP/zfs) this code already creates -- yet still boots fine,
because proxmox-boot-tool means GRUB never touches ZFS in the first place.

This is also why every run so far logged "No /etc/kernel/proxmox-boot-uuids
found, skipping ESP sync" during proxmox-kernel-helper's postinst -- that
was proxmox-boot-tool telling us the whole time that no ESP had been
registered with it yet.
"""
import os
import shutil
import subprocess
import time


def _write_grub_defaults(chroot, root_dataset):
    with open(f"{chroot.target}/etc/default/grub") as f:
        content = f.read()
    if "GRUB_CMDLINE_LINUX=" in content:
        lines = [
            l if not l.startswith("GRUB_CMDLINE_LINUX=")
            else f'GRUB_CMDLINE_LINUX="root=ZFS={root_dataset} boot=zfs"'
            for l in content.splitlines()
        ]
        content = "\n".join(lines) + "\n"
    else:
        content += f'\nGRUB_CMDLINE_LINUX="root=ZFS={root_dataset} boot=zfs"\n'
    with open(f"{chroot.target}/etc/default/grub", "w") as f:
        f.write(content)


def install(chroot, disks, root_dataset, log):
    # grub-pc's postinst asks a debconf question (grub-pc/install_devices --
    # which disks to install the legacy MBR bootloader to) with no default
    # answer; DEBIAN_FRONTEND=noninteractive means it can't prompt for one
    # either, so dpkg configuration fails outright without a preseeded
    # answer. Harmless no-op if grub-pc never ends up installed (UEFI mode).
    disk_paths = ",".join(d["path"] for d in disks)
    log.info(f"preseeding grub-pc/install_devices = {disk_paths}")
    chroot.run(["debconf-set-selections"],
               input=f"grub-pc grub-pc/install_devices multiselect {disk_paths}\n",
               text=True)

    # proxmox-ve's own dependency chain already pulls in ONE of grub-pc /
    # grub-efi-amd64 via an unconstrained "grub-pc | grub-efi-amd64"
    # alternative -- apt's solver picks without inspecting real firmware, so
    # whichever it landed on may not match this machine's actual boot mode.
    # Detect real firmware mode from THIS booted environment (running on
    # the same machine as the target) and make sure the matching package --
    # and only the matching one -- ends up installed; apt will remove the
    # other one automatically if it's already present, since only one is
    # being explicitly requested this time.
    uefi = os.path.exists("/sys/firmware/efi")
    log.info(f"firmware mode detected: {'UEFI' if uefi else 'legacy BIOS'}")
    grub_pkg = "grub-efi-amd64" if uefi else "grub-pc"

    # zfs-initramfs (not just zfsutils-linux) provides the initramfs-tools
    # hook that embeds ZFS-root boot support into the generated initrd --
    # without it, update-initramfs below wouldn't produce an initrd capable
    # of importing/mounting the root pool at the real first boot at all.
    # console-setup provides setupcon, which an initramfs-tools hook calls
    # during update-initramfs below -- without it, that hook just warns
    # ("setupcon is missing") rather than failing, but it's cheap to have.
    log.info(f"ensuring zfsutils-linux + zfs-initramfs + console-setup + {grub_pkg} "
             "are installed in target")
    chroot.run(["apt-get", "install", "-y",
                "zfsutils-linux", "zfs-initramfs", "console-setup", grub_pkg])

    # zpool create -R <altroot> (how storage/zfs.py mounted this pool under
    # /target) sets cachefile=none on the pool automatically -- documented
    # ZFS behaviour, not a bug -- so /etc/zfs/zpool.cache was never written
    # anywhere. Not the actual cause of "unknown filesystem" (see module
    # docstring), but proxmox-boot-tool's own internal grub-mkconfig call
    # still needs this to resolve root=ZFS=... for the kernel cmdline it
    # writes onto the ESP, so keep it regardless.
    pool = root_dataset.split("/")[0]
    log.info(f"setting {pool}'s cachefile and copying it into target")
    subprocess.run(["zpool", "set", "cachefile=/etc/zfs/zpool.cache", pool], check=True)
    os.makedirs(f"{chroot.target}/etc/zfs", exist_ok=True)
    shutil.copy2("/etc/zfs/zpool.cache", f"{chroot.target}/etc/zfs/zpool.cache")

    log.info("generating /etc/hostid (must be set before initramfs regen, or the pool "
             "won't reliably auto-import on the real first boot -- initrd-plan.txt Risk 4)")
    chroot.run(["zgenhostid", "-f"])

    _write_grub_defaults(chroot, root_dataset)

    log.info("regenerating initramfs for the installed kernel(s)")
    chroot.run(["update-initramfs", "-u", "-k", "all"])

    # proxmox-boot-tool owns getting a disk from "has an ESP" to "actually
    # bootable": it copies the current kernel+initrd onto the ESP itself and
    # writes a self-contained grub (or systemd-boot) config there that never
    # needs to read the ZFS root -- format+init per disk is the whole
    # replacement for what used to be a manual grub-install/update-grub
    # dance here, and it's the same tool that was already firing (as a
    # harmless no-op) during proxmox-kernel-helper's postinst in every run
    # so far.
    for disk in disks:
        esp = disk["partitions"]["esp"]
        log.info(f"proxmox-boot-tool format {esp}")
        chroot.run(["proxmox-boot-tool", "format", esp, "--force"])

        # format's own internal partition-type-change (setting the ESP GUID)
        # doesn't take effect from inside the chroot -- confirmed: udevadm
        # itself logged "Running in chroot, ignoring request." right after,
        # and the kernel warned it was "still using the old partition
        # table." The very next command (init) then queries that same
        # partition and sees stale/empty data, failing with "wrong
        # partition type" even though format just set the right one. Same
        # fix storage/partition.py already applies after its own sgdisk
        # calls: force a rescan from outside the chroot, where partprobe/
        # udevadm actually work, before querying the partition again.
        subprocess.run(["partprobe", disk["path"]], check=False)
        subprocess.run(["udevadm", "settle"], check=False)
        time.sleep(1)

        mode_args = [] if uefi else ["grub"]
        log.info(f"proxmox-boot-tool init {esp} ({'systemd-boot' if uefi else 'grub'})")
        chroot.run(["proxmox-boot-tool", "init", esp, *mode_args])

    # Cosmetic at this point -- the real reference system's own grub.cfg says
    # outright that this file "is NOT read when booting from those disks"
    # once proxmox-boot-tool owns boot -- but keeping it in sync matches
    # what a normal Debian system expects to find at /boot/grub/grub.cfg.
    log.info("update-grub (cosmetic; proxmox-boot-tool's ESP-resident config is what boots)")
    chroot.run(["update-grub"])
