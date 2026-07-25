"""First-boot integration -- plan.txt [first-boot]: fetch the script now and
bake it into the image at /usr/local/bin/first-boot.sh, plus a oneshot
systemd unit enabled to run it once the installed system is fully up.
"""
import subprocess

SERVICE = """[Unit]
Description=klargoring first-boot script
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/bin/first-boot.sh

[Install]
WantedBy=multi-user.target
"""


def install(chroot, first_boot_config, log):
    url = first_boot_config["url"]
    log.info(f"fetching first-boot script from {url}")
    script_path = f"{chroot.target}/usr/local/bin/first-boot.sh"
    subprocess.run(["curl", "-fsSL", url, "-o", script_path], check=True)
    subprocess.run(["chmod", "+x", script_path], check=True)

    with open(f"{chroot.target}/etc/systemd/system/first-boot.service", "w") as f:
        f.write(SERVICE)

    chroot.run(["systemctl", "enable", "first-boot.service"])
