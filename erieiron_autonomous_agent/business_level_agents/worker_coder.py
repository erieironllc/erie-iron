from erieiron_autonomous_agent.coding_agents import self_driving_coder_agent
from erieiron_autonomous_agent.models import Task
from erieiron_common import common


def do_work(task_id):
    self_driving_coder_agent.execute(task_id)
