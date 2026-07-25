# projekt-lods

An automated bare-metal installer that replaces the Proxmox ISO installer
workflow: PXE/iPXE boot a custom initrd, feed it a Proxmox-compatible
answer TOML, and it debootstraps Debian Trixie onto a ZFS mirror, installs
Proxmox VE, and reboots into a working node — no manual intervention, no
Debian Installer partman.

Full design rationale and decisions live in [`plan.txt`](plan.txt) (the
original spec) and [`initrd-plan.txt`](initrd-plan.txt) (the initrd-specific
addendum, including a running log of corrections made against real builds
and the verified upstream Proxmox process). This file is the practical
"how do I actually run this" reference; those two are the "why is it built
this way" reference.

## Status

- **Stage 1** (the initrd itself: debootstrap → systemd PID 1 → networking
  → TOML fetch/parse) — built, and confirmed booting on real hardware via
  PXE.
- **Stage 2** (disk detection → ZFS mirror → target debootstrap → chroot
  config → Proxmox install → GRUB → first-boot service → reboot) — written
  as a real Python package under [`installer/`](installer/), copied into
  the image by the build script and run as `lods-installer.service`.
  Confirmed end-to-end on real disks (2026-07-25): DHCP lease on the real
  interface, `first-boot.service` completed successfully, and SSH into the
  rebooted target worked using the key injected from the answer TOML.

## Repository layout

```
installer/          the Stage 2 installer, runs inside the booted initrd
  main.py              orchestrator: load TOML -> ... -> reboot
  config.py            TOML load + validation (parses the TOML directly,
                        no schema translation -- see plan.txt)
  logger.py             logs to /var/log/projekt-lods/install.log + console
  storage/
    detect.py            disk detection/validation (size floor, not mounted)
    partition.py         sgdisk GPT layout: bios_boot + ESP + zfs, per disk
    zfs.py                zpool/zfs dataset creation
  osutil/                (not os/ -- see initrd-plan.txt on why)
    debootstrap.py        Pass B: debootstrap onto /target
    chroot.py             bind-mount/chroot helper + target config
                          (hostname, hosts, timezone, locale, keyboard,
                          fstab, root password hash + SSH key injection)
  proxmox/
    repository.py         Proxmox apt repo + signing key (deb822, checksummed)
    packages.py           proxmox-ve install, kernel swap, os-prober removal
  boot/
    grub.py                hostid, initramfs, grub-install to every disk
  firstboot/
    systemd.py             fetches first-boot.sh, enables first-boot.service

build/
  build-installer-initrd.sh   Stage 1: builds the initrd + matching kernel

examples/
  answer.toml          generic template -- real per-site files are never
                        checked in here (see file header)

plan.txt               original project spec
initrd-plan.txt         initrd-specific design + corrections log
```

## Building the initrd

```
sudo bash build/build-installer-initrd.sh /path/to/output-dir
```

Needs root (debootstrap/chroot/mount all require it). Produces, in the
given output directory:

- `installer-initrd.img` — the packed, xz-compressed initrd
- `vmlinuz` — the matching kernel (module versions are pinned to this
  exact build; don't mix-and-match with a different build's initrd)
- `KERNEL_VERSION` — the kernel version string, for reference

Each run debootstraps a fresh Trixie rootfs from scratch (a few minutes,
needs network access to deb.debian.org), so expect it to take a while.

## Booting it via iPXE

```
#!ipxe
kernel http://<server>/lods/vmlinuz toml_url=<url-to-answer.toml>
initrd http://<server>/lods/installer-initrd.img
boot
```

`toml_url=` is read straight off `/proc/cmdline` by both the initrd's own
init sequence and `installer/main.py`. In production this comes from
`menu.ipxe`'s existing per-site `${boot-url}/proxmox/${site-prefix}-answer.toml`
resolution (see `initrd-plan.txt` / the provisioning-estate notes) — never
hardcode a single site's URL into a shared iPXE menu entry.

### Safety flags

The installer refuses to touch any disk unless one of these is also on the
kernel cmdline:

- `confirm-wipe` — for interactive/manual test runs
- `unattended` — for real PXE/production use

Add `no-reboot` to stay up for inspection after a successful install
instead of rebooting immediately (useful the first several times you run
this against real hardware).

Example full cmdline for a production PXE boot:
```
toml_url=http://192.168.139.50/proxmox/VRK-answer.toml unattended
```

## Debugging a booted instance

- Console login is automatic (`agetty --autologin root`) on both the local
  console (`tty1`) and serial (`ttyS0`) — no interactive prompts of any
  kind block boot, since every real deployment of this initrd runs
  unattended over PXE.
- Remote access: `ssh root@<hostname-or-ip>`, password `lods`. Deliberately
  username+password rather than a baked-in key — this project is meant to
  go public, and a private key baked into a publicly-distributed image
  either leaks an estate-internal credential or is useless to anyone
  outside that estate. This applies only to the transient, PXE-booted
  installer environment itself, never the installed target (which gets
  the real TOML-supplied password hash / SSH key instead). SSH host keys
  are *not* baked in — they're deliberately stripped after build time so
  each real boot generates its own fresh ones, rather than every deployed
  instance sharing identical host keys.
- The hostname is randomised per boot (`lods-<8 hex chars>`, set as early
  as possible by `lods-random-hostname.service`) since the initrd is the
  same static image every time — expect a different prompt/SSH target
  name on every instance, by design.
- `systemctl status lods-installer.service` / `journalctl -u lods-installer --no-pager`
  — the installer's own stdout/stderr, persisted regardless of console
  scrollback limits.
- `cat /run/answer.toml` — the fetched answer file, if you need to check
  exactly what was ingested.
- If the console keyboard layout is wrong: `ckbcomp -layout gb | loadkeys -`.
  Not `loadkeys gb` or `loadkeys uk` on their own — modern Debian's
  `console-setup` generates console keymaps dynamically via `ckbcomp` from
  XKB data rather than shipping static per-country keymap files, so there's
  no `gb.map`/`uk.map` for bare `loadkeys` to load. `"gb"` is the XKB layout
  code (matches `XKBLAYOUT` elsewhere in this codebase); there's no
  separate `"uk"` name in this scheme. `dpkg-reconfigure
  keyboard-configuration` also works interactively if you'd rather pick
  from a menu.

## The answer TOML

Parsed directly, field-for-field, with no intermediate schema (see
`plan.txt`) — `installer/config.py` validates required keys are present
and rejects anything it doesn't support yet (only
`disk-setup.filesystem = "zfs"` with `zfs.raid = "raid1"` across exactly 2
disks, only `network.source = "from-dhcp"`). See `examples/answer.toml`
for the shape; real per-site files are never committed to this repo.

## Known limitations (see initrd-plan.txt for the full list and reasoning)

- Single disks, RAIDZ, and static networking aren't implemented yet —
  matches plan.txt's stated v1 scope.
- No QEMU integration test harness yet.
- The initrd currently keeps `dkms`/`zfs-dkms`/build tooling installed
  after building the ZFS kernel module (a further size optimisation that
  wasn't done because it risks the module being torn back out — see
  initrd-plan.txt Risk 2 / the build script's own comments).
