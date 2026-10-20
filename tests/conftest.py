from pathlib import Path


def pytest_configure(config):
    # pytest creates basetemp itself but requires its parent to exist on Windows.
    if config.option.basetemp:
        Path(config.option.basetemp).parent.mkdir(parents=True, exist_ok=True)
