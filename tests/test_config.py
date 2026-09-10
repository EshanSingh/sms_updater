import textwrap
from pathlib import Path

import pytest

from testudo_watch.config import AppConfig, ConfigError, load_config


def write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "watches.toml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def test_loads_valid_config(tmp_path):
    path = write(
        tmp_path,
        """
        poll_interval_seconds = 30
        notifier = "console"

        [[watch]]
        course_id = "CMSC351"
        term_id = "202601"
        sections = ["0101", "0201"]

        [[watch]]
        course_id = "MATH240"
        term_id = "202601"
        """,
    )
    config, watches = load_config(path)
    assert config == AppConfig(
        poll_interval_seconds=30,
        notifier="console",
        db_path="testudo_watch.db",
        log_dir="logs",
    )
    assert watches[0].course_id == "CMSC351"
    assert watches[0].sections == ("0101", "0201")
    assert watches[1].sections == ()


def test_poll_interval_floor_is_enforced(tmp_path):
    path = write(
        tmp_path,
        """
        poll_interval_seconds = 3
        notifier = "console"
        [[watch]]
        course_id = "CMSC351"
        term_id = "202601"
        """,
    )
    config, _ = load_config(path)
    assert config.poll_interval_seconds == 15


def test_missing_file_raises_config_error(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.toml")


def test_bad_notifier_raises_config_error_naming_key(tmp_path):
    path = write(
        tmp_path,
        """
        notifier = "carrier-pigeon"
        [[watch]]
        course_id = "CMSC351"
        term_id = "202601"
        """,
    )
    with pytest.raises(ConfigError, match="notifier"):
        load_config(path)


def test_watch_missing_required_key_raises_naming_key(tmp_path):
    path = write(
        tmp_path,
        """
        notifier = "console"
        [[watch]]
        course_id = "CMSC351"
        """,
    )
    with pytest.raises(ConfigError, match="term_id"):
        load_config(path)


def test_no_watches_raises_config_error(tmp_path):
    path = write(tmp_path, 'notifier = "console"\n')
    with pytest.raises(ConfigError, match="watch"):
        load_config(path)


def test_sections_must_be_list_of_strings(tmp_path):
    path = write(
        tmp_path,
        """
        notifier = "console"
        [[watch]]
        course_id = "CMSC351"
        term_id = "202601"
        sections = "0101"
        """,
    )
    with pytest.raises(ConfigError, match="sections"):
        load_config(path)
