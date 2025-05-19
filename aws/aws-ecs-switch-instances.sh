aws ecs update-container-instances-state \
  --cluster erielab-webservice-mvp-ecs-cluster \
  --container-instances 405774600b5c4c3b9b198d2976e21d1b \
  --status DRAINING
