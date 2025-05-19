aws ec2 run-instances \
--image-id ami-072cfffee59a35472 \
--count 1 \
--instance-type c5.large \
--key-name erielab-ec2-instance-mvp-keypair \
--security-group-ids sg-083ae1761d5db67e5 \
--iam-instance-profile Name="ecsInstanceRole" \
--placement AvailabilityZone=us-west-2b \
--user-data fileb://conf/ec2-instance-user-data.sh
