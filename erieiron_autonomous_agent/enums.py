from enum import auto

from erieiron_common.enums import ErieEnum


class TaskStatus(ErieEnum):
    BLOCKED = auto()
    NOT_STARTED = auto()
    IN_PROGRESS = auto()
    COMPLETE = auto()
    FAILED = auto()

    @staticmethod
    def get_sorted_status():
        return [
            TaskStatus.IN_PROGRESS,
            TaskStatus.BLOCKED,
            TaskStatus.NOT_STARTED,
            TaskStatus.COMPLETE,
            TaskStatus.FAILED
        ]


class TaskExecutionMode(ErieEnum):
    HOST = auto()
    CONTAINER = auto()


class BusinessStatus(ErieEnum):
    IDEA = auto()
    ACTIVE = auto()
    PAUSED = auto()
    SHUTDOWN = auto()


class BusinessGuidanceRating(ErieEnum):
    MAINTAIN = auto()
    INCREASE_BUDGET = auto()
    DECREASE_BUDGET = auto()
    SHUTDOWN = auto()


class TrafficLight(ErieEnum):
    GREEN = auto()
    YELLOW = auto()
    RED = auto()
