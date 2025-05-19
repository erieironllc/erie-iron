import decimal
import json
from enum import Enum
from pathlib import Path

from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.db.models.base import ModelState


class ErieIronJSONEncoder(DjangoJSONEncoder):
    @staticmethod
    def dumps(d):
        from erieiron_common import common
        return json.dumps(
            common.get_dict(d),
            cls=ErieIronJSONEncoder
        )

    def default(self, obj):
        if isinstance(obj, ModelState):
            return obj.__dict__

        if isinstance(obj, models.Model):
            return obj.__dict__

        if isinstance(obj, decimal.Decimal):
            return float(obj)

        if isinstance(obj, Path):
            return str(obj)

        if isinstance(obj, Enum):
            return obj.value

        return super().default(obj)
