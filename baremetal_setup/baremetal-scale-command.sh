ERIELAB_ENV=prod python3 manage.py update_msg_processor_scale \
  --instance_id=collayamsgprocessor-1 \
  --process_count=12 \
  --threads_per_process=5 \
  --job_limits_def="stem_separate_asset:4"

python3 manage.py update_msg_processor_scale \
  --instance_id=schwabox-ubuntu \
  --process_count=0 \
  --threads_per_process=3 \
  --job_limits_def="stem_separate_asset:1"

python3 manage.py update_msg_processor_scale \
  --instance_id=jjlaptop \
  --process_count=2 \
  --threads_per_process=1 \
  --job_limits_def="stem_separate_asset:1"


python3 manage.py update_msg_processor_scale \
  --instance_id=i-089f0dd87f4a6a922 \
  --process_count=4 \
  --threads_per_process=3 \
  --job_limits_def="stem_separate_asset:2"

