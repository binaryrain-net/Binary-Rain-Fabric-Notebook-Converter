"""Project settings of ``fabric-nb``, such as the default working directory.

Settings are stored in ``config.toml`` in the root of this converter repository, next to
``pyproject.toml``. ``FABRIC_NB_CONFIG`` overrides the file location.

The working directory is taken from the first source that is set:

1. the ``--workdir`` option
2. the ``FABRIC_NB_WORKDIR`` environment variable
3. ``workdir`` in ``config.toml``, a relative value is resolved against the folder of the file
4. the current directory

Relative paths given to ``fabric-nb`` are resolved against it.
"""

import json
import os
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = "config.toml"
CONFIG_ENV_VAR = "FABRIC_NB_CONFIG"
WORKDIR_ENV_VAR = "FABRIC_NB_WORKDIR"
WORKDIR_KEY = "workdir"
SETTING_KEYS = (WORKDIR_KEY,)


class SettingsError(Exception):
    """Raised when the settings file cannot be read or a setting is invalid."""


def config_path() -> Path:
    """Return the path of the project settings file.

    :returns Path: ``FABRIC_NB_CONFIG`` if set, otherwise ``config.toml`` in the
        repository root.
    """
    override = os.environ.get(CONFIG_ENV_VAR)
    if override:
        return Path(override).expanduser()
    return PROJECT_ROOT / CONFIG_FILE


def _check_key(key: str) -> None:
    """Make sure a setting key is known.

    :param str key: Setting name.
    :raises SettingsError: If the key is not supported.
    """
    if key not in SETTING_KEYS:
        raise SettingsError(f"Unknown setting {key!r}, use one of: {', '.join(SETTING_KEYS)}")


def load_settings() -> dict[str, str]:
    """Read all settings from the settings file.

    :returns dict[str, str]: Settings, empty if the file does not exist.
    :raises SettingsError: If the file is not valid TOML.
    """
    path = config_path()
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise SettingsError(f"Invalid settings file {path}: {exc}") from exc
    return {key: str(value) for key, value in data.items() if isinstance(value, str)}


def _save_settings(settings: dict[str, str]) -> Path:
    """Write all settings to the settings file.

    :param dict[str, str] settings: Settings to store.
    :returns Path: Path of the written file.
    :raises OSError: If the file cannot be written.
    """
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # A JSON string is a valid TOML basic string, which escapes Windows backslashes.
    lines = [f"{key} = {json.dumps(value)}" for key, value in sorted(settings.items())]
    path.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8", newline="\n")
    return path


def get_setting(key: str) -> str | None:
    """Read one setting.

    :param str key: Setting name.
    :returns str | None: Stored value, ``None`` if not set.
    :raises SettingsError: If the key is unknown or the file is invalid.
    """
    _check_key(key)
    return load_settings().get(key)


def set_setting(key: str, value: str) -> Path:
    """Store one setting.

    :param str key: Setting name.
    :param str value: Value to store.
    :returns Path: Path of the settings file.
    :raises SettingsError: If the key is unknown or the file is invalid.
    :raises OSError: If the file cannot be written.
    """
    _check_key(key)
    settings = load_settings()
    settings[key] = value
    return _save_settings(settings)


def unset_setting(key: str) -> bool:
    """Remove one setting.

    :param str key: Setting name.
    :returns bool: ``True`` if the setting was stored before.
    :raises SettingsError: If the key is unknown or the file is invalid.
    :raises OSError: If the file cannot be written.
    """
    _check_key(key)
    settings = load_settings()
    if key not in settings:
        return False
    del settings[key]
    _save_settings(settings)
    return True


def save_workdir(path: Path) -> Path:
    """Store a folder as the default working directory.

    :param Path path: Existing folder, relative paths are resolved against the current
        directory.
    :returns Path: Stored absolute folder.
    :raises SettingsError: If the folder does not exist or the file is invalid.
    :raises OSError: If the file cannot be written.
    """
    folder = path.expanduser().resolve()
    if not folder.is_dir():
        raise SettingsError(f"Folder does not exist: {folder}")
    set_setting(WORKDIR_KEY, str(folder))
    return folder


def resolve_workdir(override: Path | None = None) -> Path:
    """Determine the working directory used to resolve relative paths.

    :param Path | None override: Value of the ``--workdir`` option. Default is None.
    :returns Path: Absolute working directory.
    :raises SettingsError: If the folder does not exist or the settings file is invalid.
    """
    if override is not None:
        candidate, origin = override, "--workdir"
    elif os.environ.get(WORKDIR_ENV_VAR):
        candidate, origin = Path(os.environ[WORKDIR_ENV_VAR]), WORKDIR_ENV_VAR
    elif stored := load_settings().get(WORKDIR_KEY):
        path = config_path()
        candidate, origin = path.parent / Path(stored).expanduser(), f"{WORKDIR_KEY} in {path}"
    else:
        return Path.cwd()
    folder = candidate.expanduser().resolve()
    if not folder.is_dir():
        raise SettingsError(f"Working directory from {origin} does not exist: {folder}")
    return folder


def resolve_path(path: Path, workdir: Path) -> Path:
    """Resolve a user given path against the working directory.

    :param Path path: Path as given by the user.
    :param Path workdir: Working directory.
    :returns Path: ``path`` if absolute, otherwise ``workdir / path``.
    """
    path = path.expanduser()
    return path if path.is_absolute() else workdir / path
