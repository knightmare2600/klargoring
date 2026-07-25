# projekt-lods

An automated bare-metal installer that replaces the Proxmox VE ISO
installer workflow entirely: PXE/iPXE boot a custom-built initrd, feed it
a Proxmox-compatible answer TOML over HTTP, and it debootstraps Debian
Trixie onto a ZFS mirror, installs Proxmox VE, and reboots into a working
node — no ISO, no Debian Installer `partman`, no manual intervention.

Confirmed working end-to-end on real disks (2026-07-25): PXE boot →
disk wipe → ZFS mirror → Debian + Proxmox VE install → reboot → DHCP
lease on the real interface → first-boot automation → SSH in using the
key from the answer TOML.

## Why this exists instead of just PXE-booting Proxmox's own installer

Proxmox VE's official automated-install mechanism is ISO-only. There's no
supported way to PXE-boot its installer kernel/initrd and point it at a
small answer file over HTTP the way Debian's own installer supports a
`preseed` URL, or Kickstart does on RHEL/Rocky — every automated Proxmox
install via the official tooling means serving and booting the full
~1.4GB ISO image, for what amounts to fetching one small config file.

This was put to [gyptazy](https://x.com/gyptazy) directly (a
Proxmox-adjacent expert whose name comes up throughout the Proxmox
community) — asked whether iPXE could boot Proxmox's kernel/initrd
directly with a URL to the TOML, the same way a Debian preseed or Rocky
Kickstart works:

> If it's for automated installation, you can simply use FAI or Debian
> installer PXE (including preseed file via dhcp announcement) for
> installing a Debian system, adding the PVE repository and install the
> packages. We use that way for several customers.

That's the confirmation: there's no lightweight PXE path in Proxmox's own
installer, and the standard workaround in production is exactly what
plan.txt originally set out to formalise — PXE-boot a plain Debian
install (via preseed/FAI), then layer the Proxmox repo and packages on
top afterward. projekt-lods does that same thing, but as a single
purpose-built, reusable initrd driven by the same TOML answer-file
contract already used across this estate — one PXE boot, one small file,
no gigabyte-scale ISO in the loop.

## How it works

1. iPXE boots a custom kernel + initrd (built by `build/build-installer-initrd.sh`,
   ~220MB, systemd as PID 1).
2. The initrd brings up networking via DHCP, fetches the answer TOML from
   `toml_url=` on the kernel cmdline, and parses it directly with
   Python's `tomllib` — no intermediate schema, the TOML *is* the config
   format (see "The answer TOML" below).
3. It partitions the target disks, creates a ZFS mirror, and runs
   `debootstrap` a second time — this time against the real target disks
   instead of its own initrd contents — to lay down Debian Trixie.
4. Inside a chroot on the freshly bootstrapped target: hostname/hosts/
   timezone/locale/keyboard/network config, root password hash + SSH key
   injection, the Proxmox VE repo + packages, `proxmox-boot-tool`-managed
   bootloader setup, and a `first-boot.service` that fetches and runs a
   site-specific first-boot script on the real first boot.
5. The ZFS pool is exported cleanly and the machine reboots into a
   working Proxmox VE node.

## Status

- **Stage 1** (the initrd itself) — built, confirmed booting on real
  hardware via PXE.
- **Stage 2** (disk detection through reboot) — confirmed end-to-end on
  real disks: DHCP networking, `first-boot.service`, and SSH via the
  TOML-injected key all verified on a real reboot, not just a
  syntax/import check.

## Repository layout

```
installer/          the Stage 2 installer, runs inside the booted initrd
  main.py              orchestrator: load TOML -> ... -> reboot
  config.py            TOML load + validation (parses the TOML directly,
                        no schema translation)
  logger.py            logs to /var/log/projekt-lods/install.log + console
  storage/
    detect.py            disk detection/validation (size floor, not mounted)
    partition.py         sgdisk GPT layout: bios_boot + ESP + zfs, per disk
    zfs.py                zpool/zfs dataset creation
  osutil/                (not os/ -- a subpackage literally named os/ would
                          shadow the stdlib os module for the whole process,
                          since main.py's own directory lands at sys.path[0])
    debootstrap.py        Pass B: debootstrap onto /target
    chroot.py             bind-mount/chroot helper + target config
                          (hostname, hosts, timezone, locale, keyboard,
                          network, fstab, root password hash + SSH key)
  proxmox/
    repository.py         Proxmox apt repo + signing key (deb822, checksummed)
    packages.py           proxmox-ve install, kernel swap, os-prober removal
  boot/
    grub.py                hostid, initramfs, proxmox-boot-tool per disk
  firstboot/
    systemd.py             fetches first-boot.sh, enables first-boot.service

build/
  build-installer-initrd.sh   Stage 1: builds the initrd + matching kernel
  build-installer-iso.sh      wraps that build's output into a bootable ISO
  grub.cfg                    the ISO's GRUB menu (local/serial console entries)

.github/workflows/
  build.yml            manual workflow_dispatch: builds the initrd+kernel,
                        then the ISO, as separate jobs; uploads both

examples/
  answer.toml          generic template -- real per-site files are never
                        checked in here

tests/
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

## Building the ISO

For sites/situations where PXE isn't available. Run this *after*
`build-installer-initrd.sh` — it wraps that build's output, it doesn't
build the kernel/initrd itself:

```
bash build/build-installer-iso.sh /path/to/initrd-output-dir /path/to/iso-output-dir
```

No root needed for this step. Needs `grub-pc-bin`, `grub-efi-amd64-bin`,
`xorriso`, and `mtools` (`grub-mkrescue`'s own dependencies) on the build
host. Produces a single hybrid BIOS+UEFI ISO
(`projekt-lods-installer.iso`) — the same disc image boots on both old
BIOS hardware and modern UEFI systems.

Both build steps also run as a manually-triggered GitHub Actions workflow
(`.github/workflows/build.yml`, `workflow_dispatch` only — prompts for
the target Debian suite, e.g. `trixie`/`bookworm`, then builds the
initrd+kernel and the ISO as separate jobs, uploading both as workflow
artifacts).

## Booting via iPXE

Real production example, from this estate's own `menu.ipxe`:

```
# ===========================================================================
# PROJEKT LODS
# Robert's own hand-rolled live Proxmox installer distro, built separately --
# NOT the Proxmox-official auto-install ISO path (see "Why this exists"
# above). Different distro, different mechanism.
# Uses ${boot-url}/${site-prefix} like every other entry here rather than a
# hardcoded IP/site -- ${site-prefix}-answer.toml matches the same per-site
# answer-file convention already established elsewhere in this menu.
# ===========================================================================
:lods
iseq ${arch} arm64 && goto noarch-msg ||
kernel ${boot-url}/proxmox/lods/vmlinuz toml_url=${boot-url}/proxmox/${site-prefix}-answer.toml confirm-wipe no-reboot
initrd ${boot-url}/proxmox/lods/installer-initrd.img
boot
```

`toml_url=` is read straight off `/proc/cmdline` by both the initrd's own
init sequence and `installer/main.py`. In a multi-site estate this comes
from the existing per-site `${boot-url}/proxmox/${site-prefix}-answer.toml`
resolution done entirely in iPXE (gateway-based site detection, an `iseq`
branch setting both `${site-prefix}` and `${boot-url}` together) — never
hardcode a single site's URL into a shared iPXE menu entry.

## Booting via ISO

Burn/mount `projekt-lods-installer.iso` and boot it. Unlike the iPXE path
(where `menu.ipxe` already resolves the real `toml_url=` per site), a
generic ISO can't know that URL in advance — its GRUB menu
(`build/grub.cfg`) ships two entries, "local console (tty1)" and "serial
console (ttyS0, 115200n8)", both with a placeholder
`toml_url=http://CHANGE-ME/answer.toml`.

GRUB's own menu always shows "press `e` to edit, `c` for a command line"
at the bottom — no whiptail/dialog anywhere in this project. Press `e` on
whichever console entry matches your setup, replace `CHANGE-ME` with the
real answer-file URL, then boot (`Ctrl-X` or `F10` in GRUB's editor). If
you boot without editing it, `curl` simply fails to fetch the placeholder
and the installer exits cleanly — `confirm-wipe` (baked into both
entries) also still requires an explicit interactive confirmation before
any disk is touched regardless, so an un-edited boot can't wipe anything
by accident.

### Kernel cmdline options

- `toml_url=<url>` — **required**. Where to fetch the answer TOML from.
  Fetched with `curl -fsSL`, over plain HTTP in every example here — this
  is consistent with existing conventions in this estate, but worth
  knowing if you're serving secrets (password hash, SSH keys) over an
  untrusted network.
- `confirm-wipe` — for interactive/manual test runs. Requires an operator
  at the console to explicitly confirm before any disk is touched.
- `unattended` — for real PXE/production use. Skips the interactive
  confirmation entirely and proceeds straight to wiping the disks listed
  in the TOML's `disk-setup.disk-list`.
- `no-reboot` — stay up after a successful install instead of rebooting
  immediately, for inspection. Useful the first several times you run
  this against real hardware, or when debugging.

**The installer refuses to touch any disk unless one of `confirm-wipe` or
`unattended` is present on the cmdline.** Neither present at all means it
logs the disks it *would* wipe and exits without touching anything — this
is deliberate, not a bug, and there is no interactive whiptail/dialog
prompt anywhere in this flow to catch a missing flag; the kernel cmdline
is the entire interface.

Example full cmdline for a production PXE boot:
```
toml_url=http://192.168.139.50/proxmox/VRK-answer.toml unattended
```

The console itself can be paused before boot at the bootloader's own
menu (GRUB: press `e` to edit the entry, `c` for a command line) —
useful for a one-off manual retarget of `toml_url=` without editing the
served menu at all.

## Debugging a booted instance

- Console login is automatic (`agetty --autologin root`) on both the
  local console (`tty1`) and serial (`ttyS0`) — no interactive prompts of
  any kind block boot, since every real deployment of this initrd runs
  unattended over PXE.
- Remote access: `ssh root@<hostname-or-ip>`, password `lods`. Deliberately
  username+password rather than a baked-in key — this project is public,
  and a private key baked into a publicly-distributed image either leaks
  an estate-internal credential or is useless to anyone outside that
  estate. This applies only to the transient, PXE-booted installer
  environment itself, never the installed target (which gets the real
  TOML-supplied password hash / SSH key instead). SSH host keys are *not*
  baked in — they're stripped after build time so each real boot
  generates its own fresh ones.
- The hostname is randomised per boot (`lods-<8 hex chars>`) since the
  initrd is the same static image every time — expect a different
  prompt/SSH target name on every instance, by design.
- `systemctl status lods-installer.service` / `journalctl -u lods-installer --no-pager`
  — the installer's own stdout/stderr, persisted regardless of console
  scrollback limits.
- `cat /run/answer.toml` — the fetched answer file, if you need to check
  exactly what was ingested.
- If the console keyboard layout is wrong: `ckbcomp -layout gb | loadkeys -`.
  Not `loadkeys gb` on its own — modern Debian's `console-setup` generates
  console keymaps dynamically via `ckbcomp` from XKB data rather than
  shipping static per-country keymap files. `"gb"` is the XKB layout code;
  `dpkg-reconfigure keyboard-configuration` also works interactively if
  you'd rather pick from a menu.
- After a successful `no-reboot` run, the ZFS pool is cleanly exported —
  `/target` will be gone, that's expected, not a fault. To inspect it
  afterward: `zpool import -R /target rpool`, then `zpool export rpool`
  again once you're done, before letting the machine reboot for real.

## The answer TOML

Parsed directly, field-for-field, with no intermediate schema —
`installer/config.py` validates required keys are present and rejects
anything it doesn't support yet. See `examples/answer.toml` for the full
shape; real per-site files are never committed to this repo. Currently
supported:

- `disk-setup.filesystem = "zfs"` with `zfs.raid = "raid1"` across
  exactly 2 disks (single disks, RAIDZ not yet implemented)
- `network.source = "from-dhcp"` (static networking not yet implemented)
- `first-boot.source = "from-url"` — fetches and runs a script on first
  real boot, `After=`/`Wants=network-online.target`

## Known limitations

- Single disks, RAIDZ, and static networking aren't implemented yet.
- No QEMU integration test harness yet.
- The initrd currently keeps `dkms`/`zfs-dkms`/build tooling installed
  after building the ZFS kernel module (a further size optimisation not
  yet done, since it risks the module being torn back out).
- The ISO build (`build/build-installer-iso.sh`, `grub-mkrescue`) hasn't
  been run to completion yet — this build host is missing `xorriso` and
  `mtools` (and `grub-efi-amd64-bin`, for the UEFI half of the hybrid
  ISO). Syntax-checked only; needs a real run (locally with those
  packages installed, or via the GitHub Actions workflow, which installs
  them itself) before it's trusted.
