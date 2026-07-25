"""ZFS mirror pool creation. Properties per plan.txt verbatim: ashift=12,
compression=lz4, atime=off, xattr=sa, acltype=posixacl.

Uses zpool's -R altroot instead of manually managing mountpoints -- datasets
mount under /target for the duration of the install without needing a real
mountpoint=/target property baked into the pool permanently.
"""
import subprocess

TARGET = "/target"
POOL = "rpool"
ROOT_DATASET = f"{POOL}/ROOT/debian"


def create_pool(disks, log):
    zfs_parts = [d["partitions"]["zfs"] for d in disks]
    log.info(f"creating {POOL} mirror across {zfs_parts}")

    subprocess.run([
        "zpool", "create", "-f",
        "-o", "ashift=12",
        "-O", "compression=lz4",
        "-O", "atime=off",
        "-O", "xattr=sa",
        "-O", "acltype=posixacl",
        "-O", "mountpoint=none",
        "-R", TARGET,
        POOL, "mirror", *zfs_parts,
    ], check=True)

    subprocess.run(["zfs", "create", "-p", "-o", "mountpoint=/", ROOT_DATASET], check=True)
    subprocess.run(["zpool", "set", f"bootfs={ROOT_DATASET}", POOL], check=True)

    log.info(f"{ROOT_DATASET} mounted at {TARGET} (via altroot)")
    return TARGET


def export_pool(log):
    log.info(f"exporting {POOL} for a clean re-import on real boot")
    subprocess.run(["zpool", "export", POOL], check=True)
