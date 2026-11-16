import os

# name -> ("dotenv", value) for values this module loaded from the project .env file.
# Anything else in os.environ came from the real process / Databricks App environment.
SOURCES = {}

PRIORITY_VAR = "LABSTD_ENV_PRIORITY"  # "dotenv" (default) or "environment"


def parse_dotenv(path):
    """Read KEY=VALUE pairs from a .env file. Never touches os.environ."""
    out = {}
    if not path or not os.path.isfile(path):
        return out
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.lower().startswith("export "):
                line = line[7:]
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            if k:
                out[k] = v
    return out


def load_dotenv(path, override=None):
    """Load a .env file into os.environ.

    By default the value in the .env file WINS over an environment variable of the
    same name. The old behaviour (environment always wins) let a stale
    LLM_API_KEY, left in the Databricks App settings or a cluster, silently hide the
    new key that had been written to .env. Set LABSTD_ENV_PRIORITY=environment, or
    pass override=False, to restore it. Empty values in .env are ignored, so a blank
    line can never erase a real key.
    """
    if not path or not os.path.isfile(path):
        return False
    if override is None:
        override = os.environ.get(PRIORITY_VAR, "dotenv").strip().lower() != "environment"
    for k, v in parse_dotenv(path).items():
        if v == "":
            continue
        if k in os.environ and not override:
            continue
        os.environ[k] = v
        SOURCES[k] = ("dotenv", v)
    return True


def env_source(name):
    """'dotenv', 'environment' or 'not set' for one variable."""
    value = os.environ.get(name)
    if not value:
        return "not set"
    src, loaded = SOURCES.get(name, ("environment", None))
    # If something replaced the value after the .env file was read, it is no longer the .env value.
    return src if src == "environment" or loaded == value else "environment"
