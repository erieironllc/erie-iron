from types import SimpleNamespace

import pytest

from erieiron_autonomous_agent.enums import TaskStatus
from erieiron_ui import views


class _TaskStub:
    def __init__(self, task_id: str, status: str, name: str):
        self.id = task_id
        self.status = status
        self._name = name

    def get_name(self) -> str:
        return self._name


def _initiative_stub(init_id: str, title: str, tasks):
    return SimpleNamespace(id=init_id, title=title, tasks=list(tasks))


def test_build_project_plan_viewmodel_orders_rows_and_counts_units():
    init_one = _initiative_stub(
        "init-1",
        "Launch",
        tasks=[
            _TaskStub("task-1", TaskStatus.NOT_STARTED.value, "Write brief"),
        ],
    )
    init_two = _initiative_stub(
        "init-2",
        "Scale",
        tasks=[
            _TaskStub("task-2", TaskStatus.COMPLETE.value, "Ship v1"),
            _TaskStub("task-3", TaskStatus.BLOCKED.value, "Triage bugs"),
        ],
    )

    viewmodel = views._build_project_plan_viewmodel([init_one, init_two])

    assert viewmodel["total_initiatives"] == 2
    assert viewmodel["total_tasks"] == 3
    assert viewmodel["total_units"] == 3

    rows = viewmodel["rows"]
    assert [row["type"] for row in rows] == [
        "initiative",
        "task",
        "initiative",
        "task",
        "task",
    ]

    first_initiative = rows[0]
    assert first_initiative["label"] == "Launch"
    assert first_initiative["bar_units"] == 1
    assert first_initiative["status"] == TaskStatus.NOT_STARTED.value
    assert first_initiative["offset_units"] == 0
    assert first_initiative["bar_percent"] == pytest.approx(33.3333)
    assert first_initiative["offset_percent"] == pytest.approx(0)

    second_initiative = rows[2]
    assert second_initiative["bar_units"] == 2
    assert second_initiative["status"] == TaskStatus.BLOCKED.value
    assert second_initiative["offset_units"] == 1
    assert second_initiative["bar_percent"] == pytest.approx(66.6667)
    assert second_initiative["offset_percent"] == pytest.approx(33.3333)

    task_row = rows[3]
    assert task_row["bar_units"] == 1
    assert task_row["status"] == TaskStatus.COMPLETE.value
    assert rows[1]["offset_units"] == 0
    assert rows[3]["offset_units"] == 1
    assert rows[4]["offset_units"] == 2
    assert rows[1]["bar_percent"] == pytest.approx(33.3333)
    assert rows[3]["offset_percent"] == pytest.approx(33.3333)
