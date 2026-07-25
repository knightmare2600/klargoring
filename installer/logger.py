"""Logging setup. Per plan.txt: log everything to /var/log/klargoring/install.log,
in addition to stdout (which klargoring-installer.service already sends to both the
journal and the console).
"""
import logging
import os

LOG_PATH = "/var/log/klargoring/install.log"


def setup():
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

    logger = logging.getLogger("klargoring")
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")

    file_handler = logging.FileHandler(LOG_PATH)
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("[klargoring] %(message)s"))
    logger.addHandler(console_handler)

    return logger
