"""One database server for all tests, and a fresh database, fresh secrets and fresh processes for each of them."""

import os
import random

import pytest

from feed import Courier, Feed, Sender, Sent
from world import BUILD, Postgres, World

SEED = int(os.environ.get("CRASH_SEED") or random.SystemRandom().randrange(10**9))


def pytest_report_header():
    return f"seed: {SEED}"


@pytest.fixture(scope="session")
def seed():
    return SEED


@pytest.fixture(scope="session")
def postgres():
    server = Postgres()
    try:
        server.start()
        yield server
    finally:
        server.remove()


@pytest.fixture
def feeds():
    return [Feed("north-web", "north"), Feed("north-app", "north"), Feed("south-web", "south"), Feed("south-app", "south")]


@pytest.fixture
def make_world(postgres, feeds, tmp_path):
    """Makes worlds, and ends them all when the test is over."""
    worlds = []

    def make(build="faults", **settings):
        folder = tmp_path / f"world-{len(worlds) + 1}"
        folder.mkdir()
        worlds.append(World(postgres, folder, feeds, build=BUILD / build, **settings))
        return worlds[-1]

    yield make
    for world in worlds:
        world.end()


@pytest.fixture
def world(make_world):
    return make_world()


@pytest.fixture
def sent():
    return Sent()


@pytest.fixture
def make_sender(sent, seed):
    def make(world, number=0):
        return Sender(Courier(world.ports, seed + number), sent, seed + number)

    return make
