"""Copy the current Spring application and add PiLab-only probes."""

import argparse
import shutil
from pathlib import Path

from .legacy import HERE, SPRING_JAVA


RECEIVER = "handler/DeviceMessageReceiver.java"
HANDLER = "handler/WebSocketHandler.java"


def stage_receiver(repo, destination):
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Spring build directory must be empty.")
    source = repo / "spring"
    destination.mkdir(parents=True, exist_ok=True)
    for name in (".dockerignore", "Dockerfile", "build.gradle", "settings.gradle", "gradlew", "gradlew.bat"):
        shutil.copy2(source / name, destination / name)
    shutil.copytree(source / "gradle" / "wrapper", destination / "gradle" / "wrapper")
    shutil.copytree(source / "src", destination / "src")
    shutil.copyfile(HERE / "ReceiverLabSessionProbe.java",
                    destination / SPRING_JAVA / "handler" / "ReceiverLabSessionProbe.java")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--repo", type=Path, default=HERE.parent)
    args = parser.parse_args()
    stage_receiver(args.repo, args.destination)
