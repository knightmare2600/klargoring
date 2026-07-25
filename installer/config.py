"""Loads and validates the Proxmox-compatible answer TOML.

Per plan.txt: parse this directly, don't translate it into some other
internal schema. Downstream modules read straight out of the dict this
returns (e.g. config["global"]["fqdn"]).
"""
import tomllib

REQUIRED = {
    "global": ("keyboard", "country", "fqdn", "timezone", "root-password-hashed", "root-ssh-keys"),
    "network": ("source",),
    "disk-setup": ("filesystem", "disk-list"),
    "first-boot": ("source", "url"),
}


class ConfigError(Exception):
    pass


def load(path):
    with open(path, "rb") as f:
        config = tomllib.load(f)
    validate(config)
    return config


def validate(config):
    for section, keys in REQUIRED.items():
        if section not in config:
            raise ConfigError(f"missing [{section}] section")
        for key in keys:
            if key not in config[section]:
                raise ConfigError(f"missing {key} in [{section}]")

    disks = config["disk-setup"]["disk-list"]
    if not isinstance(disks, list) or not disks:
        raise ConfigError("disk-setup.disk-list must be a non-empty list")

    if config["disk-setup"]["filesystem"] != "zfs":
        raise ConfigError(
            f"filesystem={config['disk-setup']['filesystem']!r} not supported yet (only 'zfs')"
        )

    raid = config["disk-setup"].get("zfs.raid", "raid1")
    if raid != "raid1":
        raise ConfigError(f"zfs.raid={raid!r} not supported yet (only 'raid1' mirror)")
    if raid == "raid1" and len(disks) != 2:
        raise ConfigError(f"zfs.raid=raid1 needs exactly 2 disks, got {disks!r}")

    if config["network"]["source"] != "from-dhcp":
        raise ConfigError(
            f"network.source={config['network']['source']!r} not supported yet (only 'from-dhcp')"
        )
