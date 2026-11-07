"""Compose personas and harnesses into independently named, installed agents."""

from .composition import compose, compose_preset, load_preset, write_document
from .store import AgentStore, make_agent


__version__ = "0.0.6"


def main(argv=None):
    from .cli import main as run

    return run(argv)
