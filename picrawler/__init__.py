#!/usr/bin/env python3
"""PiCrawler motion framework: kinematics, gaits and servo control."""
from . import kinematics
from .gaits import MoveList
from .version import __version__

__all__ = ["Picrawler", "MoveList", "kinematics", "__version__"]


def __getattr__(name):
    # Import the hardware class lazily so kinematics and gaits can be used
    # (and tested) on machines without robot_hat installed.
    if name == "Picrawler":
        from .picrawler import Picrawler
        return Picrawler
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __main__():
    print(f"Picrawler {__version__} — motion framework for the SunFounder PiCrawler")
