#!/bin/bash
# Stage 1 build script.
#
# debootstraps a Debian Trixie rootfs, installs install-time tooling into it,
# runs systemd as PID 1 (/init -> /sbin/init, networking via systemd-networkd)
# with a oneshot klargoring-installer.service running ../installer/main.py -- the
# real Stage 2 installer (disk detection, ZFS mirror, target debootstrap,
# Proxmox repo+packages, GRUB, first-boot service) -- then packs the whole
# rootfs into an xz'd cpio initrd alongside its matching kernel. This script
# just copies installer/ in verbatim; the installer logic itself lives
# there, not in this build script.
#
# PID 1 is systemd, not a hand-rolled init script: it sets a sane $PATH for
# every service it starts (the earlier hand-rolled /init didn't, which is why
# dhcpcd -- living in /usr/sbin, outside Python's subprocess fallback PATH --
# failed to exec), brings up networking itself via systemd-networkd, and
# gives you a real agetty login (job control included) on tty1 and ttyS0 for
# free, plus journalctl for pulling logs instead of relying on console
# scrollback.
#
# Must run as root (debootstrap/chroot/mount all require it):
#   sudo bash build/build-installer-initrd.sh [output-dir]
#
# ARCH=amd64|arm64 (env var, default amd64) selects the target architecture.
# Output lands in <output-dir>/<ARCH>/, so amd64 and arm64 builds never
# collide. Cross-building (ARCH different from this host's own architecture)
# needs qemu-user-static installed on the build host -- see the debootstrap
# step below.
#
# Idempotent-ish: each run debootstraps fresh into a new tempdir and only
# touches the given output directory.

set -euo pipefail

# The build host's own locale (e.g. en_GB.UTF-8) otherwise leaks into every
# chroot invocation below via inherited environment variables. installer-
# rootfs never generates that locale (it's the build environment, not the
# shipped target -- unlike the target's own locale handling in
# installer/osutil/chroot.py, which does properly install+generate one) so
# perl/dpkg hooks fall back to C and print a "Setting locale failed"
# warning on every single apt-get call inside the chroot. Harmless, but
# noisy -- force C for this script's own environment instead.
export LC_ALL=C
export LANGUAGE=
export LANG=C

if [ "$(id -u)" -ne 0 ]; then
  echo "error: must run as root -- sudo bash $0 [output-dir]" >&2
  exit 1
fi

SUITE="${SUITE:-trixie}"
ARCH="${ARCH:-amd64}"
MIRROR="http://deb.debian.org/debian/"
# Per-architecture output subfolder -- amd64 and arm64 builds never overwrite
# each other, and every downstream write below just uses $OUTDIR unchanged.
OUTDIR="$(realpath -m "${1:-$(pwd)/output}")/$ARCH"
WORKDIR="$(mktemp -d /var/tmp/klargoring-initrd-build.XXXXXX)"
ROOTFS="$WORKDIR/rootfs"

log() { echo "[klargoring-build] $*" >&2; }

cleanup() {
  log "cleaning up bind mounts under $ROOTFS"
  for m in dev/pts dev proc sys; do
    if mountpoint -q "$ROOTFS/$m" 2>/dev/null; then
      umount -lf "$ROOTFS/$m" || true
    fi
  done
}
trap cleanup EXIT

mkdir -p "$OUTDIR"
mkdir -p "$ROOTFS"

# Cross-building for a different architecture than this build host's own
# needs qemu-user-static's binfmt registration so the target-arch postinst
# scripts debootstrap runs during its second stage can actually execute.
# qemu-debootstrap (from the qemu-user-static package) is the standard tool
# for this -- it's a thin wrapper that copies the right static qemu-*-static
# binary into the chroot before running the normal second stage. Only used
# when actually cross-building; a native build uses plain debootstrap.
HOST_ARCH="$(dpkg --print-architecture)"
if [ "$ARCH" != "$HOST_ARCH" ]; then
  log "cross-building $ARCH on a $HOST_ARCH host -- using qemu-debootstrap"
  DEBOOTSTRAP_CMD="qemu-debootstrap"
else
  DEBOOTSTRAP_CMD="debootstrap"
fi

log "[1/10] $DEBOOTSTRAP_CMD --arch=$ARCH $SUITE -> $ROOTFS"
"$DEBOOTSTRAP_CMD" --arch="$ARCH" --variant=minbase "$SUITE" "$ROOTFS" "$MIRROR"

log "[2/10] bind-mounting virtual filesystems for chroot package installs"
mount -t proc proc "$ROOTFS/proc"
mount -t sysfs sysfs "$ROOTFS/sys"
mount --bind /dev "$ROOTFS/dev"
mkdir -p "$ROOTFS/dev/pts"
mount -t devpts devpts "$ROOTFS/dev/pts"
cp /etc/resolv.conf "$ROOTFS/etc/resolv.conf"

# debootstrap only enables 'main' by default. zfsutils-linux/zfs-dkms are
# CDDL-licensed and live in 'contrib'; non-free-firmware covers NIC/RAID
# firmware blobs the installer may need on real hardware at PXE-boot time.
cat > "$ROOTFS/etc/apt/sources.list" <<EOF
deb $MIRROR $SUITE main contrib non-free-firmware
EOF

log "[3/10] installing install-time tooling into rootfs"
chroot "$ROOTFS" apt-get update
DEBIAN_FRONTEND=noninteractive chroot "$ROOTFS" apt-get install -y --no-install-recommends \
  systemd systemd-sysv systemd-resolved udev dbus \
  "linux-image-$ARCH" "linux-headers-$ARCH" \
  iproute2 iputils-ping \
  curl wget ca-certificates openssh-client openssh-server w3m procps \
  gdisk parted dosfstools util-linux e2fsprogs kpartx \
  zfsutils-linux zfs-dkms \
  debootstrap debian-archive-keyring gnupg \
  python3 vim-tiny grc \
  keyboard-configuration console-setup whiptail kbd

log "[4/10] verifying zfs kernel module was actually built before stripping headers"
if ! find "$ROOTFS/lib/modules" -iname 'zfs.ko*' | grep -q .; then
  echo "error: zfs.ko not found under $ROOTFS/lib/modules -- refusing to strip linux-headers-$ARCH" >&2
  exit 1
fi

log "[5/10] purging linux-headers-* (build-only, not needed once zfs.ko exists) to shrink the image"
# linux-headers-$ARCH is a thin metapackage -- the actual header files live
# in versioned packages (linux-headers-<kver>-common, -$ARCH) that
# --autoremove doesn't reliably cascade into removing on its own. Purge
# every installed linux-headers-* package explicitly instead of guessing.
HEADER_PKGS="$(chroot "$ROOTFS" dpkg-query -W -f='${Package}\n' 'linux-headers-*' 2>/dev/null || true)"
if [ -n "$HEADER_PKGS" ]; then
  DEBIAN_FRONTEND=noninteractive chroot "$ROOTFS" apt-get purge -y --autoremove $HEADER_PKGS
fi
if ! find "$ROOTFS/lib/modules" -iname 'zfs.ko*' | grep -q .; then
  echo "error: zfs.ko disappeared after purging linux-headers-$ARCH -- aborting, image would boot without ZFS" >&2
  exit 1
fi

log "[6/10] configuring systemd as PID 1: /init, networking, getty, installer service"

# initramfs entrypoint: the kernel execs /init directly, and since this cpio
# IS the only root this environment will ever have (no switch_root, no real
# disk root to hand off to -- Stage 2 ends with a plain reboot once the
# target disk is provisioned), point it straight at systemd itself.
ln -sf /sbin/init "$ROOTFS/init"

# transient machine-id per boot, not one fixed value baked into every image
: > "$ROOTFS/etc/machine-id"

# DHCP on any interface -- systemd-networkd replaces the earlier hand-rolled
# dhcpcd subprocess call entirely.
mkdir -p "$ROOTFS/etc/systemd/network"
cat > "$ROOTFS/etc/systemd/network/20-dhcp.network" <<'EOF'
[Match]
Name=en* eth*

[Network]
DHCP=yes
EOF

# DNS via systemd-resolved's stub -- needed once Stage 2 debootstraps against
# deb.debian.org by name, not just VRK-answer.toml's http://<ip>/... URL.
rm -f "$ROOTFS/etc/resolv.conf"
ln -sf /run/systemd/resolve/stub-resolv.conf "$ROOTFS/etc/resolv.conf"

chroot "$ROOTFS" systemctl enable systemd-networkd systemd-networkd-wait-online systemd-resolved

# real login shell (job control included) on both the framebuffer console
# and serial, so a stuck boot is always debuggable without extra steps
for unit in getty@tty1 serial-getty@ttyS0; do
  mkdir -p "$ROOTFS/etc/systemd/system/${unit}.service.d"
  cat > "$ROOTFS/etc/systemd/system/${unit}.service.d/autologin.conf" <<'EOF'
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin root --noclear %I $TERM
EOF
done

# Remote debug access via sshd, deliberately username+password
# (root/klargoring) rather than a baked-in key: this project is intended
# to go public, and a private key baked into a publicly-distributed image
# either exposes an estate-internal credential or is useless to anyone
# outside that estate. A documented, fixed default credential -- for this
# transient, PXE-booted installer environment only, never the installed
# target -- works for everyone who has the image, which is the actual
# point of it being public.
log "setting root:klargoring and enabling sshd for remote debug access"
chroot "$ROOTFS" bash -c 'echo "root:klargoring" | chpasswd'
mkdir -p "$ROOTFS/etc/ssh/sshd_config.d"
cat > "$ROOTFS/etc/ssh/sshd_config.d/klargoring-debug.conf" <<'EOF'
PermitRootLogin yes
PasswordAuthentication yes
EOF
# openssh-server's own postinst already generated host keys during package
# install above -- baked into the image, every deployed instance would
# otherwise share the exact same SSH host keys. Remove them; ssh.service's
# own dependency chain (ssh-keygen@ units) regenerates fresh ones the
# first time each actual instance boots.
rm -f "$ROOTFS"/etc/ssh/ssh_host_*_key*
chroot "$ROOTFS" systemctl enable ssh.service

# Random hostname per boot: the initrd is the same static cpio image every
# time, so this has to happen at runtime, not build time, or every booted
# instance would share one fixed hostname. Runs as early as possible
# (sysinit.target, Before= anything that might display/advertise it) so
# getty/sshd/klargoring-installer all see the real value, not a placeholder.
cat > "$ROOTFS/etc/systemd/system/klargoring-random-hostname.service" <<'EOF'
[Unit]
Description=klargoring random hostname
Before=getty@tty1.service serial-getty@ttyS0.service ssh.service klargoring-installer.service
DefaultDependencies=no
Conflicts=shutdown.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c 'h="klargoring-$(head -c4 /dev/urandom | od -An -tx1 | tr -d " \n")"; echo "$h" > /etc/hostname; hostname "$h"'

[Install]
WantedBy=sysinit.target
EOF
chroot "$ROOTFS" systemctl enable klargoring-random-hostname.service

# No automatic interactive keyboard-layout picker: every real deployment
# of this initrd runs unattended over PXE, so a prompt requiring physical
# interaction has no business blocking boot by default. If the console
# layout is ever wrong during manual debugging, README.md documents the
# fix: `ckbcomp -layout gb | loadkeys -` (or `dpkg-reconfigure
# keyboard-configuration` interactively) -- kbd is installed for exactly
# this, on demand, not on every boot.

log "[7/10] installing the klargoring installer package + service unit"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$ROOTFS/opt/klargoring"
cp -a "$REPO_ROOT/installer" "$ROOTFS/opt/klargoring/installer"
chmod +x "$ROOTFS/opt/klargoring/installer/main.py"

cat > "$ROOTFS/etc/systemd/system/klargoring-installer.service" <<'EOF'
[Unit]
Description=klargoring installer
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/python3 /opt/klargoring/installer/main.py
StandardOutput=journal+console
StandardError=journal+console

[Install]
WantedBy=multi-user.target
EOF
chroot "$ROOTFS" systemctl enable klargoring-installer.service

log "[8/10] extracting matching kernel"
KERNEL="$(ls "$ROOTFS"/boot/vmlinuz-* | sort -V | tail -1)"
cp "$KERNEL" "$OUTDIR/vmlinuz"
KVER="$(basename "$KERNEL" | sed 's/^vmlinuz-//')"
echo "$KVER" > "$OUTDIR/KERNEL_VERSION"

log "[9/10] stripping dead weight before packing (apt cache, docs/man, redundant boot/ copies)"
chroot "$ROOTFS" apt-get clean
rm -rf "$ROOTFS"/usr/share/doc/* "$ROOTFS"/usr/share/man/* "$ROOTFS"/usr/share/info/*
# vmlinuz is already copied out above; the initramfs-tools-generated
# /boot/initrd.img-* is a full separate initramfs we never boot (we ARE the
# initrd) -- both are dead weight sitting inside our own outer cpio otherwise
rm -f "$ROOTFS"/boot/vmlinuz-* "$ROOTFS"/boot/initrd.img* "$ROOTFS"/initrd.img "$ROOTFS"/initrd.img.old "$ROOTFS"/vmlinuz "$ROOTFS"/vmlinuz.old

log "[10/10] unmounting and packing initrd (xz -- noticeably smaller than gzip for this content)"
for m in dev/pts dev proc sys; do
  if mountpoint -q "$ROOTFS/$m" 2>/dev/null; then
    umount -lf "$ROOTFS/$m"
  fi
done

# --check=crc32: the kernel's in-tree XZ decoder (used to unpack the
# initramfs at boot) only supports CRC32 or no checksum at all, not xz's
# own default of CRC64 -- a stream with a check type it can't verify fails
# to unpack, and the kernel falls through to "mount a real root", i.e.
# exactly the "VFS: Unable to mount root fs on unknown-block(0,0)" panic.
# -T0: use all available cores. xz auto-scales the actual thread count down
# to fit available RAM (each thread needs ~674MiB for -9e's dictionary), so
# this is safe even on a memory-constrained build host -- it just won't
# spawn more threads than it can afford, rather than needing that tuned by hand.
( cd "$ROOTFS" && find . -mindepth 1 -print0 | cpio --null -o -H newc 2>/dev/null | xz -9e -T0 --check=crc32 ) > "$OUTDIR/installer-initrd.img"

rm -rf "$WORKDIR"

if [ -n "${SUDO_UID:-}" ]; then
  chown "${SUDO_UID}:${SUDO_GID}" "$OUTDIR" "$OUTDIR"/* 2>/dev/null || true
fi

log "done. output in $OUTDIR:"
du -h "$OUTDIR/installer-initrd.img" "$OUTDIR/vmlinuz"
echo "kernel version: $KVER"
