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


class BusinessOperationType(ErieEnum):
    ERIE_IRON_AUTONOMOUS = auto()
    ERIE_IRON_MANUAL = auto()
    THIRD_PARTY_AUTONOMOUS = auto()
    THIRD_PARTY_MANUAL = auto()
    
    @staticmethod
    def is_thirdparty(operation_type):
        operation_type = BusinessOperationType(operation_type)
        return operation_type in [BusinessOperationType.THIRD_PARTY_MANUAL, BusinessOperationType.THIRD_PARTY_AUTONOMOUS]
    
    @staticmethod
    def is_autonomous(operation_type):
        operation_type = BusinessOperationType(operation_type)
        return operation_type in [BusinessOperationType.ERIE_IRON_AUTONOMOUS, BusinessOperationType.THIRD_PARTY_AUTONOMOUS]
    
    @staticmethod
    def is_manual(operation_type):
        return not BusinessOperationType.is_autonomous(operation_type)


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
