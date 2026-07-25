"""GPT partitioning via sgdisk -- same tool Proxmox's own installer uses,
more scriptable than parted. Layout per plan.txt, per disk:
  1: BIOS boot partition (1MiB,   type EF02) -- legacy grub-pc embedding area
  2: EFI system partition (512MiB, type EF00, fat32)
  3: ZFS partition (rest of disk,  type BF01)
"""
import subprocess
import time


def partition_path(disk_path, index):
    # /dev/sda -> /dev/sda1, but /dev/nvme0n1 -> /dev/nvme0n1p1
    if disk_path[-1].isdigit():
        return f"{disk_path}p{index}"
    return f"{disk_path}{index}"


def partition_disk(disk_path, log):
    log.info(f"partitioning {disk_path}: wiping existing table")
    subprocess.run(["sgdisk", "--zap-all", disk_path], check=True)

    log.info(f"partitioning {disk_path}: bios-boot + ESP + zfs")
    subprocess.run(["sgdisk",
                    "-n1:1M:+1M",   "-t1:EF02",
                    "-n2:0:+512M",  "-t2:EF00",
                    "-n3:0:0",      "-t3:BF01",
                    disk_path], check=True)

    subprocess.run(["partprobe", disk_path], check=False)
    subprocess.run(["udevadm", "settle"], check=False)
    time.sleep(1)  # partition device nodes can lag udevadm settle by a beat

    return {
        "bios_boot": partition_path(disk_path, 1),
        "esp": partition_path(disk_path, 2),
        "zfs": partition_path(disk_path, 3),
    }


def partition_disks(disks, log):
    """disks: list of dicts from storage.detect.detect(). Returns the same
    list with a 'partitions' key added to each entry."""
    for disk in disks:
        disk["partitions"] = partition_disk(disk["path"], log)

    for i, disk in enumerate(disks):
        esp = disk["partitions"]["esp"]
        label = "EFI" if i == 0 else f"EFI{i}"
        log.info(f"formatting {esp} as FAT32 ({label})")
        subprocess.run(["mkfs.vfat", "-F", "32", "-n", label, esp], check=True)

    return disks
