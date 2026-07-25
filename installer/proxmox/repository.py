"""Adds the Proxmox VE apt repository, verified against
pve.proxmox.com/wiki/Install_Proxmox_VE_on_Debian_13_Trixie (2026-07-23):
deb822 .sources format, not a classic one-line deb entry, and the signing
key comes from enterprise.proxmox.com with a published checksum.
"""
import hashlib
import subprocess

KEY_URL = "https://enterprise.proxmox.com/debian/proxmox-archive-keyring-trixie.gpg"
KEY_SHA256 = "136673be77aba35dcce385b28737689ad64fd785a797e57897589aed08db6e45"
KEY_PATH = "/usr/share/keyrings/proxmox-archive-keyring.gpg"
SOURCES_PATH = "/etc/apt/sources.list.d/pve-install-repo.sources"

SOURCES_CONTENT = f"""Types: deb
URIs: http://download.proxmox.com/debian/pve
Suites: trixie
Components: pve-no-subscription
Signed-By: {KEY_PATH}
"""


def add_repository(target, log):
    log.info(f"fetching Proxmox repo signing key from {KEY_URL}")
    subprocess.run(["curl", "-fsSL", KEY_URL, "-o", f"{target}{KEY_PATH}"], check=True)

    with open(f"{target}{KEY_PATH}", "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    if digest != KEY_SHA256:
        raise RuntimeError(
            f"proxmox-archive-keyring checksum mismatch: got {digest}, expected {KEY_SHA256} "
            "-- refusing to trust an unverified repo signing key"
        )
    log.info("repo signing key checksum verified")

    with open(f"{target}{SOURCES_PATH}", "w") as f:
        f.write(SOURCES_CONTENT)
