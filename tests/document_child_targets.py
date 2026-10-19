"""Stand-in readers that run in the child process (IsolatedExtractor tests).

Kept tiny on purpose: the child process imports this module by name.
"""
import os
import time


def hang(connection, data, filename, limits):
    time.sleep(60)


def crash(connection, data, filename, limits):
    os._exit(3)
