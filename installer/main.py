#!/usr/bin/env python3
"""klargoring installer entrypoint.

Flow (plan.txt "Installer Execution Flow" / initrd-plan.txt Stage 2):
  load TOML -> validate -> detect hardware -> confirm-wipe gate ->
  partition disks -> create ZFS pool -> debootstrap target -> chroot
  configuration -> Proxmox repo+packages -> GRUB+hostid -> first-boot
  service -> export pool -> reboot.

Runs as klargoring-installer.service (After=network-online.target), so DHCP is
already up. toml_url= and the safety flags come from /proc/cmdline, same
place the site-specific iPXE menu.ipxe already puts toml_url for the
Stage-0 build (see project_provisioning_estate memory / initrd-plan.txt).
"""
import subprocess
import sys

import config as config_mod
import logger as logger_mod
from storage import detect, partition, zfs
from osutil import debootstrap, chroot as chroot_mod
from proxmox import repository, packages
from boot import grub
from firstboot import systemd as firstboot


def read_cmdline():
    with open("/proc/cmdline") as f:
        return f.read().split()


def cmdline_arg(name):
    prefix = f"{name}="
    for tok in read_cmdline():
        if tok.startswith(prefix):
            return tok[len(prefix):]
    return None


def cmdline_flag(name):
    return name in read_cmdline()


def fetch_toml(url, dest="/run/answer.toml"):
    subprocess.run(["curl", "-fsSL", url, "-o", dest], check=True)
    return dest


def main(log):
    toml_url = cmdline_arg("toml_url")
    if not toml_url:
        log.info("no toml_url= on kernel cmdline -- nothing to do")
        return

    log.info(f"fetching answer file: {toml_url}")
    config = config_mod.load(fetch_toml(toml_url))

    disk_names = config["disk-setup"]["disk-list"]
    disks = detect.detect(disk_names, log)

    unattended = cmdline_flag("unattended")
    confirm_wipe = cmdline_flag("confirm-wipe")
    if not (unattended or confirm_wipe):
        log.error("refusing to wipe disks: neither 'unattended' nor 'confirm-wipe' "
                  "was passed on the kernel cmdline.")
        log.error(f"disks that would be wiped: {[d['path'] for d in disks]}")
        log.error("pass confirm-wipe (interactive) or unattended (PXE/production) to proceed.")
        sys.exit(1)

    disks = partition.partition_disks(disks, log)
    target = zfs.create_pool(disks, log)

    debootstrap.bootstrap(target, log)

    with chroot_mod.Chroot(target, log) as ch:
        fqdn = config["global"]["fqdn"]
        hostname = chroot_mod.write_hostname(target, fqdn)
        ip = chroot_mod.primary_ipv4(log)
        chroot_mod.write_hosts(target, fqdn, hostname, ip, log)
        chroot_mod.write_timezone(ch, config["global"]["timezone"], log)
        chroot_mod.write_locale(ch, config["global"]["country"], log)
        chroot_mod.write_keyboard(ch, config["global"]["keyboard"], log)
        chroot_mod.write_fstab(target, log)
        chroot_mod.inject_root_password(target, config["global"]["root-password-hashed"], log)
        chroot_mod.inject_ssh_keys(target, config["global"]["root-ssh-keys"], log)

        repository.add_repository(target, log)
        packages.install(ch, log)
        # proxmox-ve's own postinst is what generates /etc/network/interfaces
        # in the first place -- must run after packages.install(), not before.
        chroot_mod.write_network(target, log)
        grub.install(ch, disks, zfs.ROOT_DATASET, log)
        firstboot.install(ch, config["first-boot"], log)

    zfs.export_pool(log)

    if cmdline_flag("no-reboot"):
        log.info("install complete -- no-reboot passed, staying up for inspection")
        return

    log.info("install complete -- rebooting")
    subprocess.run(["systemctl", "reboot"], check=False)


if __name__ == "__main__":
    log = logger_mod.setup()
    try:
        main(log)
    except Exception:
        log.exception("installer failed")
        sys.exit(1)
