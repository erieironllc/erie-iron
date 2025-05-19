export ERIE_EC2_HOSTNAME=$(aws ec2 describe-instances \
--query 'Reservations[*].Instances[?State.Name==`running`].NetworkInterfaces[*].Association.PublicDnsName' \
--output text)

echo $ERIE_EC2_HOSTNAME