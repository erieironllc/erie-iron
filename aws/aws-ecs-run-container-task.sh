aws ecs run-task  \
--cluster erielab-webservice-mvp-ecs-cluster \
--launch-type EC2 \
--task-definition erielab-webservice-taskdef \
--count 1 \
--region us-west-2