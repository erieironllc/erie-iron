python3 manage.py update_msg_processor_scale \
  --instance_id=jjlaptop \
  --process_count=2 \
  --threads_per_process=1 \
  --job_limits_def="stem_separate_asset:1"
