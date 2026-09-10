import logging

import pytest

from testudo_watch.logging_setup import configure_logging


@pytest.fixture(autouse=True)
def _reset_testudo_logger():
    logger = logging.getLogger("testudo_watch")
    saved = logger.handlers[:]
    logger.handlers.clear()
    saved_level = logger.level
    try:
        yield
    finally:
        for h in logger.handlers:
            h.close()
        logger.handlers.clear()
        logger.handlers.extend(saved)
        logger.setLevel(saved_level)


def test_creates_log_dir_and_file(tmp_path):
    configure_logging(tmp_path / "logs")
    logging.getLogger("testudo_watch").info("hello")
    assert (tmp_path / "logs" / "testudo_watch.log").exists()


def test_is_idempotent(tmp_path):
    configure_logging(tmp_path / "logs")
    configure_logging(tmp_path / "logs")
    handlers = logging.getLogger("testudo_watch").handlers
    assert len(handlers) == 2


def test_verbose_sets_stream_handler_to_debug(tmp_path):
    configure_logging(tmp_path / "logs", verbose=True)
    stream = [
        h
        for h in logging.getLogger("testudo_watch").handlers
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.FileHandler)
    ]
    assert stream and stream[0].level == logging.DEBUG
