"""Logging setup. Per plan.txt: log everything to /var/log/lods/install.log,
in addition to stdout (which lods-installer.service already sends to both the
journal and the console).
"""
import logging
import os

LOG_PATH = "/var/log/lods/install.log"


def setup():
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

    logger = logging.getLogger("lods")
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")

    file_handler = logging.FileHandler(LOG_PATH)
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("[lods] %(message)s"))
    logger.addHandler(console_handler)

    return logger
