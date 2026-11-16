import os
import yaml


class Cfg:
    def __init__(self, d):
        self.d = d
        self.catalog = d["catalog"]
        self.schema = d["schema"]
        self.volume = d["volume"]
        self.folders = d["folders"]

    def t(self, name):
        return f"{self.catalog}.{self.schema}.{self.d['tables'][name]}"

    def vol(self, *parts):
        return "/".join(["/Volumes", self.catalog, self.schema, self.volume, *parts])


def load_cfg(root):
    with open(os.path.join(root, "config", "pipeline_config.yaml")) as f:
        return Cfg(yaml.safe_load(f))
