#!/bin/bash
# Wraps an already-built kernel + initrd (from build-installer-initrd.sh)
# into a bootable hybrid BIOS+UEFI ISO via GRUB2 (grub-mkrescue), using
# the menu in grub.cfg (separate local-console/serial-console entries).
# Does not build the kernel/initrd itself.
#
# Needs: grub-pc-bin grub-efi-amd64-bin xorriso mtools (grub-mkrescue's
# own dependencies). Does not need root, unlike build-installer-initrd.sh.
#
# Usage: bash build/build-installer-iso.sh <initrd-build-output-dir> [iso-output-dir]

set -euo pipefail

SRCDIR="$(realpath "${1:?usage: $0 <initrd-build-output-dir> [iso-output-dir]}")"
OUTDIR="$(realpath -m "${2:-$(pwd)/output}")"
WORKDIR="$(mktemp -d /var/tmp/lods-iso-build.XXXXXX)"
ISOROOT="$WORKDIR/isoroot"

log() { echo "[lods-iso-build] $*" >&2; }

trap 'rm -rf "$WORKDIR"' EXIT

for f in vmlinuz installer-initrd.img; do
  if [ ! -f "$SRCDIR/$f" ]; then
    echo "error: $SRCDIR/$f not found -- run build-installer-initrd.sh first" >&2
    exit 1
  fi
done

mkdir -p "$ISOROOT/boot/grub" "$OUTDIR"
cp "$SRCDIR/vmlinuz" "$ISOROOT/boot/vmlinuz"
cp "$SRCDIR/installer-initrd.img" "$ISOROOT/boot/installer-initrd.img"
cp "$(dirname "$(realpath "$0")")/grub.cfg" "$ISOROOT/boot/grub/grub.cfg"

log "grub-mkrescue -> $OUTDIR/lods-installer.iso"
grub-mkrescue -o "$OUTDIR/lods-installer.iso" "$ISOROOT" >&2

log "done."
du -h "$OUTDIR/lods-installer.iso"
