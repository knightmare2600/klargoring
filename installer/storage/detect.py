"""Disk detection and validation -- plan.txt "Hardware Detection"/"Safety
Features": log what was found, and refuse to touch anything that isn't a
plain, unmounted, sufficiently-large block device named in disk-list.
"""
import os

MIN_DISK_BYTES = 8 * 1024 ** 3  # 8GiB floor -- catches an obviously-wrong disk-list entry


class DiskError(Exception):
    pass


def _size_bytes(name):
    with open(f"/sys/class/block/{name}/size") as f:
        return int(f.read().strip()) * 512


def _model(name):
    path = f"/sys/class/block/{name}/device/model"
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip()
    return "unknown"


def _mounted_devices():
    mounted = set()
    with open("/proc/mounts") as f:
        for line in f:
            dev = line.split()[0]
            if dev.startswith("/dev/"):
                mounted.add(dev[len("/dev/"):])
    return mounted


def detect(disk_names, log):
    mounted = _mounted_devices()
    disks = []

    for name in disk_names:
        path = f"/sys/class/block/{name}"
        if not os.path.isdir(path):
            raise DiskError(f"/dev/{name} does not exist")

        size = _size_bytes(name)
        model = _model(name)
        log.info(f"detected disk /dev/{name}: size={size / 1024**3:.1f}GiB model={model!r}")

        if size < MIN_DISK_BYTES:
            raise DiskError(f"/dev/{name} is only {size / 1024**3:.1f}GiB, refusing (< 8GiB floor)")

        for mounted_dev in mounted:
            if mounted_dev == name or mounted_dev.startswith(name):
                raise DiskError(f"/dev/{name} (or a partition on it) is currently mounted")

        disks.append({"name": name, "path": f"/dev/{name}", "size": size, "model": model})

    return disks
