import json

from erieiron_config import settings
from erieiron_common import common


class RuntimeConfig:
    internal_instance = None

    @classmethod
    def set_instance(cls, instance: 'RuntimeConfig') -> 'RuntimeConfig':
        # should only be use internally to this class or for testing
        cls.internal_instance = instance
        return cls.internal_instance

    @classmethod
    def instance(cls) -> 'RuntimeConfig':
        if cls.internal_instance:
            return cls.internal_instance
        else:
            if settings.RUNTIME_CONFIG_OVERRIDES:
                return cls.set_instance(RuntimeConfigOverrideInstance(
                    config_override=json.loads(settings.RUNTIME_CONFIG_OVERRIDES)
                ))
            else:
                return cls.set_instance(RuntimeConfig())

    def get_list(self, name: str, delim=",") -> str:
        return common.safe_split(self.get(name), strip=True)

    def get(self, name: str, default=None) -> str:
        from erieiron_common import models

        try:
            val = models.RuntimeConfigVal.objects.get(name=name).value
            if val is None:
                return default
            else:
                return val
        except models.RuntimeConfigVal.DoesNotExist:
            return default

    def get_int(self, name: str, default: int = 0) -> int:
        if self.get(name) is None:
            return default
        else:
            return int(self.get(name, default=default))

    def get_bool(self, name: str, default: bool = False) -> bool:
        from erieiron_common import common
        return common.parse_bool(self.get(name, default=default))

    def delete_value(self, name: str):
        common.log_info(f"deleting runtime config {name}")
        from erieiron_common import models
        config_val = models.RuntimeConfigVal.objects.filter(name=name).delete()

    def set_value(self, name: str, val):
        common.log_info(f"setting runtime config {name} to {val}")
        from erieiron_common import models

        try:
            config_val = models.RuntimeConfigVal.objects.get(name=name)
        except models.RuntimeConfigVal.DoesNotExist:
            config_val = models.RuntimeConfigVal(name=name)

        config_val.value = str(val)
        config_val.save()

        return self

    def get_map_val(self, config_name: str, key_name: str, default_val=None) -> str:
        from erieiron_common import common
        the_map = self.get_map(config_name, default_vals=default_val)
        return common.get(config_name, key_name, default_val)

    def get_map(self, name: str, default_vals=None) -> dict:
        from erieiron_common import common
        the_map = {}
        for name_val in common.safe_split(self.get(name), strip=True):
            name_val_parts = common.safe_split(name_val, delimeter=":", strip=True)
            name = name_val_parts[0]
            if len(name_val_parts) == 0:
                value = default_vals
            else:
                value = name_val_parts[1]
            the_map[name] = value
        return the_map


class RuntimeConfigOverrideInstance(RuntimeConfig):
    def __init__(self, config_override: dict = None):
        super().__init__()
        self.config_override = common.default(config_override, {})

    def get(self, name: str, default=None) -> str:
        if name in self.config_override:
            return self.config_override.get(name)
        else:
            return super().get(name, default)

    def set_value(self, name: str, val):
        self.config_override[name] = val
