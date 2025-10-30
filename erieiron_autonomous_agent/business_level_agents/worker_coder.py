from erieiron_autonomous_agent.coding_agents.agent_dispatch import (
    get_self_driving_coder_agent_module,
)
from erieiron_autonomous_agent.models import Task
from erieiron_common import common


def do_work(task_id):
    agent = get_self_driving_coder_agent_module()
    agent.execute(task_id)
