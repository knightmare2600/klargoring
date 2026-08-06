#!/bin/bash
# Wraps an already-built kernel + initrd (from build-installer-initrd.sh)
# into a bootable ISO via GRUB2 (grub-mkrescue), using the menu in grub.cfg
# (separate local-console/serial-console entries). Does not build the
# kernel/initrd itself.
#
# ARCH=amd64|arm64 (env var, default amd64) -- must match the ARCH used for
# build-installer-initrd.sh, since it reads from and writes to the same
# per-arch subfolder convention (<dir>/<ARCH>/).
#
# amd64 produces a hybrid BIOS+UEFI ISO: needs grub-pc-bin grub-efi-amd64-bin
# xorriso mtools. arm64 Proxmox hosts are UEFI-only (no legacy BIOS at all),
# so an arm64 ISO only needs grub-efi-arm64-bin xorriso mtools -- grub-pc-bin
# is neither needed nor useful there. Does not need root, unlike
# build-installer-initrd.sh.
#
# Usage: bash build/build-installer-iso.sh <initrd-build-output-dir> [iso-output-dir]

set -euo pipefail

ARCH="${ARCH:-amd64}"
SRCDIR="$(realpath "${1:?usage: $0 <initrd-build-output-dir> [iso-output-dir]}")/$ARCH"
OUTDIR="$(realpath -m "${2:-$(pwd)/output}")/$ARCH"
WORKDIR="$(mktemp -d /var/tmp/klargoring-iso-build.XXXXXX)"
ISOROOT="$WORKDIR/isoroot"

log() { echo "[klargoring-iso-build] $*" >&2; }

trap 'rm -rf "$WORKDIR"' EXIT

for f in vmlinuz installer-initrd.img; do
  if [ ! -f "$SRCDIR/$f" ]; then
    echo "error: $SRCDIR/$f not found -- run build-installer-initrd.sh (with the same ARCH) first" >&2
    exit 1
  fi
done

# GRUB's platform directory naming (i386-pc, x86_64-efi, arm64-efi) is fixed
# by upstream GRUB, not this project. Check the platform this ARCH actually
# needs is installed before grub-mkrescue runs, rather than let it silently
# produce an ISO that can't actually boot on the target architecture --
# GRUB in the wrong platform mode can't exec a kernel of a different arch
# anyway, so a missing package here would otherwise surface as a confusing
# boot-time failure far away from this build step. Known caveat this does
# NOT guard against: grub-mkrescue embeds boot entries for every GRUB
# platform it finds installed, not just the one checked for here -- if a
# build host ever has both grub-efi-amd64-bin and grub-efi-arm64-bin
# installed at once (CI matrix runners are isolated per job and won't hit
# this; a shared local dev box might), an ISO built for one ARCH could pick
# up a stray boot entry for the other. Not handled here.
case "$ARCH" in
  amd64) GRUB_PLATFORM="x86_64-efi"; GRUB_PKG_HINT="grub-efi-amd64-bin" ;;
  arm64) GRUB_PLATFORM="arm64-efi"; GRUB_PKG_HINT="grub-efi-arm64-bin" ;;
  *) echo "error: unknown ARCH=$ARCH -- expected amd64 or arm64" >&2; exit 1 ;;
esac
if [ ! -d "/usr/lib/grub/$GRUB_PLATFORM" ]; then
  echo "error: /usr/lib/grub/$GRUB_PLATFORM not found -- install $GRUB_PKG_HINT" >&2
  exit 1
fi

mkdir -p "$ISOROOT/boot/grub" "$OUTDIR"
cp "$SRCDIR/vmlinuz" "$ISOROOT/boot/vmlinuz"
cp "$SRCDIR/installer-initrd.img" "$ISOROOT/boot/installer-initrd.img"
cp "$(dirname "$(realpath "$0")")/grub.cfg" "$ISOROOT/boot/grub/grub.cfg"

log "grub-mkrescue ($ARCH, $GRUB_PLATFORM) -> $OUTDIR/klargoring-installer.iso"
grub-mkrescue -o "$OUTDIR/klargoring-installer.iso" "$ISOROOT" >&2

log "done."
du -h "$OUTDIR/klargoring-installer.iso"
