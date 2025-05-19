import json
import logging
import time
import urllib.request

import boto3

S3_BUCKET = "erie-keyvalue-store"
S3_PREFIX = "cache"  # Optional prefix for organization

s3_client = boto3.client("s3")
ec2_client = boto3.client("ec2")
asg_client = boto3.client("autoscaling")
elbv2_client = boto3.client("elbv2")

SERVICES = [
    {
        "AUTO_SCALING_GROUP_NAME": 'erie-webservic-ecs-asg',
        "TARGET_GROUP_ARN": 'arn:aws:elasticloadbalancing:us-west-2:471112823728:targetgroup/erielab-targetgroup/537e79df601e5716',
        "ECS_CLUSTER_NAME": 'erielab-webservice-ecs-cluster',
        "ECS_SERVICE_NAME": 'erielab-webservice-ecs-cluster-service',
        "MIRROR_ECS": False,
        "ASG_SCALE_UP_TO": 3,
        "ASG_DOWN_TO": 1
        # },
        # {
        #     "AUTO_SCALING_GROUP_NAME": 'erie-messageprocessor-ecs-asg',
        #     "ECS_CLUSTER_NAME": 'erielab-messageprocessor-ecs-cluster',
        #     "ECS_SERVICE_NAME": 'erielab-messageprocessor-ecs-cluster-service',
        #     "MIRROR_ECS": False,
        #     "ASG_SCALE_UP_TO": 1,
        #     "ASG_DOWN_TO": 0
    }
]

logger = logging.getLogger()
logger.setLevel(logging.INFO)

boto_autoscaling = boto3.client('autoscaling')
boto_ecs = boto3.client('ecs')
boto_ec2 = boto3.client('ec2')
boto_codepipeline = boto3.client('codepipeline')

ALB_ARN = "arn:aws:elasticloadbalancing:us-west-2:471112823728:targetgroup/erielab-targetgroup/537e79df601e5716"


def lambda_handler(event, context):
    logger.info("Received event: " + json.dumps(event))

    TARGET_PIPELINE_NAME = 'erielab-webservice-mvp-codepipeline'

    try:
        state = get_state(event)
        if state == 'STARTED':
            for s in SERVICES:
                asg_name = s['AUTO_SCALING_GROUP_NAME']
                current_desired_capacity = get_asg_desired_capacity(asg_name)
                set_cached_value(
                    f"{asg_name}_previous_desired",
                    current_desired_capacity
                )

                desired = max(1, current_desired_capacity, s['ASG_SCALE_UP_TO'])

                if s['MIRROR_ECS']:
                    set_ecs_service_tasks(
                        s['ECS_CLUSTER_NAME'],
                        s['ECS_SERVICE_NAME'],
                        desired
                    )

                update_asg_capacity(
                    asg_name,
                    desired=desired
                )

            time.sleep(10)
            for s in SERVICES:
                try:
                    activate_ecs_instances(
                        s['AUTO_SCALING_GROUP_NAME'],
                        s['ECS_CLUSTER_NAME']
                    )
                except Exception as e:
                    logging.exception(e)

        elif is_pipeline_running(TARGET_PIPELINE_NAME):
            logger.info(f"Pipeline '{TARGET_PIPELINE_NAME}' is currently running. Skipping scale-down.")
        else:
            logger.info(f"Pipeline '{TARGET_PIPELINE_NAME}' is not running. Proceeding with scale-down.")

            for s in SERVICES:
                asg_name = s['AUTO_SCALING_GROUP_NAME']

                desired = get_cached_value(
                    f"{asg_name}_previous_desired",
                    s['ASG_DOWN_TO']
                )

                min_instances = get_asg_min_instances(asg_name)
                desired = max(min_instances, desired)
                print("SCALING DOWN TO", desired)

                if 'TARGET_GROUP_ARN' in s:
                    asg_instances, current_asg_desired_count = get_asg_instances(asg_name)
                    print("ALL INSTANCES", asg_instances)
                    unhealthy_instances = get_unhealthy_instances(s['TARGET_GROUP_ARN'])
                    print("UNHEALTHY INSTANCES", unhealthy_instances)

                    instances_to_keep = []
                    instances_to_kill = []

                    for instance_id in asg_instances:
                        if instance_id in unhealthy_instances:
                            instances_to_kill.append(instance_id)
                        elif len(instances_to_keep) < desired:
                            instances_to_keep.append(instance_id)
                        else:
                            instances_to_kill.append(instance_id)

                    # if we don't have enough keepers, grab some from the kill batch
                    for instance_id in instances_to_kill:
                        if len(instances_to_keep) >= desired:
                            break
                        instances_to_keep.append(instance_id)

                    instances_to_kill = [i for i in instances_to_kill if i not in instances_to_keep]

                    print("KILLING", instances_to_kill)
                    print("KEEPING", instances_to_keep)

                    deregister_instances_from_alb(
                        s['TARGET_GROUP_ARN'],
                        instances_to_kill
                    )

                    scale_down_asg(
                        asg_name,
                        instances_to_kill
                    )
                else:
                    update_asg_capacity(
                        asg_name,
                        desired=desired
                    )

                if s['MIRROR_ECS']:
                    set_ecs_service_tasks(
                        s['ECS_CLUSTER_NAME'],
                        s['ECS_SERVICE_NAME'],
                        desired
                    )

                name_remaining_instances(
                    asg_name,
                    f"{asg_name} {get_pacific_time().strftime('%Y%m%d%H%M')}"
                )
    except Exception as e:
        logger.error(f"Error processing the Lambda function: {e}")
        raise e


def get_state(event):
    try:
        detail = event['detail']
        pipeline = detail['pipeline']
        state = detail['state']  # 'STARTED','SUCCEEDED' or 'FAILED'
        logger.info(f"Pipeline {pipeline} has state {state}")
    except KeyError as e:
        logger.error(f"Missing key in event: {e}")
        state = "test_mode"
        logger.info(f"Assuming test mode")

    return state


def get_asg_desired_capacity(auto_scaling_group_name):
    response = boto3.client("autoscaling").describe_auto_scaling_groups(
        AutoScalingGroupNames=[auto_scaling_group_name]
    )

    if response["AutoScalingGroups"]:
        return response["AutoScalingGroups"][0]["DesiredCapacity"]
    else:
        raise ValueError(f"No Auto Scaling group not found for {auto_scaling_group_name}")


def update_asg_capacity(asg_name, desired):
    logger.info(f"Updating Auto Scaling Group '{asg_name}' with Desired: {desired}")
    response = boto_autoscaling.update_auto_scaling_group(
        AutoScalingGroupName=asg_name,
        DesiredCapacity=desired
    )
    logger.info(f"Auto Scaling Group updated: {response}")


def set_ecs_service_tasks(cluster_name, service_name, desired_count):
    boto3.client('ecs').update_service(
        cluster=cluster_name,
        service=service_name,
        desiredCount=desired_count
    )


def update_ecs_service(cluster_name, service_name, desired_count):
    logger.info(f"Updating ECS Service '{service_name}' in Cluster '{cluster_name}' to Desired Count: {desired_count}")
    response = boto_ecs.update_service(
        cluster=cluster_name,
        service=service_name,
        desiredCount=desired_count
    )
    logger.info(f"ECS Service updated: {response}")


def terminate_old_instances(asg_name):
    logger.info(f"Fetching instances from Auto Scaling Group '{asg_name}'")
    asg_response = boto_autoscaling.describe_auto_scaling_groups(
        AutoScalingGroupNames=[asg_name]
    )

    if not asg_response['AutoScalingGroups']:
        logger.error(f"No Auto Scaling Group found with name '{asg_name}'")
        return

    asg = asg_response['AutoScalingGroups'][0]
    instances = asg.get('Instances', [])

    logger.info(f"Retrieved instances: {instances}")  # Log instances

    if len(instances) <= 1:
        logger.info("Only one or no instance present in the Auto Scaling Group. No termination needed.")
        return

    # Sort instances by LaunchTime descending
    try:
        sorted_instances = sorted(instances, key=lambda x: x['LaunchTime'], reverse=True)
    except KeyError as e:
        logger.error(f"Missing key in instance data: {e}")
        raise e

    # Keep the most recent instance
    instances_to_terminate = sorted_instances[1:]

    logger.info(f"Instances to terminate: {[instance['InstanceId'] for instance in instances_to_terminate]}")

    for instance in instances_to_terminate:
        instance_id = instance['InstanceId']
        logger.info(f"Terminating instance {instance_id}")
        try:
            boto_autoscaling.terminate_instance_in_auto_scaling_group(
                InstanceId=instance_id,
                ShouldDecrementDesiredCapacity=False
            )
            logger.info(f"Termination initiated for instance {instance_id}")
        except Exception as e:
            logger.error(f"Failed to terminate instance {instance_id}: {e}")


def name_remaining_instances(asg_name, instance_name):
    """
    Tags the remaining instance in the ASG with the specified tag value.
    """
    logger.info(f"Fetching instances from Auto Scaling Group '{asg_name}' to tag the remaining instance.")
    asg_response = boto_autoscaling.describe_auto_scaling_groups(
        AutoScalingGroupNames=[asg_name]
    )

    if not asg_response['AutoScalingGroups']:
        logger.error(f"No Auto Scaling Group found with name '{asg_name}'")
        return

    asg = asg_response['AutoScalingGroups'][0]
    instances = asg.get('Instances', [])

    if not instances:
        return

    boto_ec2.create_tags(
        Resources=[i['InstanceId'] for i in instances],
        Tags=[{'Key': 'Name', 'Value': instance_name}]
    )


def is_pipeline_running(pipeline_name):
    """
    Checks if the specified CodePipeline is currently running.
    Returns True if any execution is in progress, else False.
    """
    try:
        logger.info(f"Checking if pipeline '{pipeline_name}' is running.")
        response = boto_codepipeline.list_pipeline_executions(
            pipelineName=pipeline_name,
            maxResults=10  # Adjust as needed
        )
        executions = response.get('pipelineExecutionSummaries', [])

        for execution in executions:
            status = execution.get('status')
            if status == 'InProgress':
                logger.info(f"Pipeline '{pipeline_name}' has an execution in progress (Execution ID: {execution.get('pipelineExecutionId')}).")
                return True

        logger.info(f"No in-progress executions found for pipeline '{pipeline_name}'.")
        return False

    except Exception as e:
        logger.error(f"Error checking pipeline status: {e}")
        # Depending on requirements, you might want to return False or re-raise the exception
        return False


def is_dst_pacific(utc_dt):
    import datetime

    """
    Determine if a given UTC datetime is within the DST period for Pacific Time.
    """
    year = utc_dt.year

    # DST starts on the second Sunday in March
    march = datetime.date(year, 3, 1)
    first_sunday_march = march + datetime.timedelta(days=(6 - march.weekday()))
    second_sunday_march = first_sunday_march + datetime.timedelta(weeks=1)
    dst_start = datetime.datetime.combine(second_sunday_march, datetime.time(2, 0))
    # Convert DST start to UTC (PST is UTC-8 before DST starts)
    dst_start_utc = dst_start + datetime.timedelta(hours=8)

    # DST ends on the first Sunday in November
    november = datetime.date(year, 11, 1)
    first_sunday_november = november + datetime.timedelta(days=(6 - november.weekday()))
    dst_end = datetime.datetime.combine(first_sunday_november, datetime.time(2, 0))
    # Convert DST end to UTC (PDT is UTC-7 during DST)
    dst_end_utc = dst_end + datetime.timedelta(hours=7)

    return dst_start_utc <= utc_dt < dst_end_utc


def get_pacific_time():
    import datetime

    """
    Get the current time in the Pacific Time Zone, accounting for DST.
    """
    # noinspection PyDeprecation
    now_utc = datetime.datetime.utcnow()

    if is_dst_pacific(now_utc):
        offset = datetime.timedelta(hours=-7)  # PDT
    else:
        offset = datetime.timedelta(hours=-8)  # PST

    return now_utc + offset


def fetch_url(url):
    request = urllib.request.Request(
        url,
        headers={
            'Content-Type': 'application/json',
            'X-Custom-Service': 'lambda_job_requester',
            'X-Forwarded-For': '52.94.76.0'
        })
    with urllib.request.urlopen(request) as response:
        print("response.status", response.status)
        if response.status != 200:
            raise Exception(f"Failed to call {url}. HTTP status: {response.status}")

        response_body = response.read().decode('utf-8')
    return response_body


def set_cached_value(key, value):
    """Store a JSON-serializable ``value`` under ``key`` in S3."""
    try:
        serialized_value = json.dumps(value)
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=f"{S3_PREFIX}/{key}.json",
            Body=serialized_value.encode("utf-8")
        )
    except Exception as e:
        print(f"Error storing key {key} in S3: {e}")


def get_cached_value(key, default=None):
    """Return the JSON value stored under ``key`` or ``default``."""
    try:
        response = s3_client.get_object(
            Bucket=S3_BUCKET,
            Key=f"{S3_PREFIX}/{key}.json"
        )
        serialized_value = response["Body"].read().decode("utf-8")
        return json.loads(serialized_value)
    except s3_client.exceptions.NoSuchKey:
        return default
    except Exception as e:
        print(f"Error retrieving key {key} from S3: {e}")
        return default


def activate_ecs_instances(asg_name, cluster_name):
    ecs_client = boto3.client("ecs")
    ec2_client = boto3.client("ec2")
    asg_client = boto3.client("autoscaling")

    # Get instances in the Auto Scaling Group
    response = asg_client.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
    if not response["AutoScalingGroups"]:
        print(f"No Auto Scaling Group found with name: {asg_name}")
        return

    # Extract instance IDs from ASG
    instances = response["AutoScalingGroups"][0]["Instances"]
    instance_ids = [i["InstanceId"] for i in instances if i["LifecycleState"] in {"InService", "Pending"}]

    if not instance_ids:
        print(f"No running or starting instances found in ASG: {asg_name}")
        return

    # List all container instances in the ECS cluster
    list_response = ecs_client.list_container_instances(cluster=cluster_name)
    container_instances = list_response["containerInstanceArns"]

    # Describe the container instances to map them to EC2 instances
    describe_response = ecs_client.describe_container_instances(
        cluster=cluster_name,
        containerInstances=container_instances
    )

    # Find draining ECS instances that match the EC2 instances
    instance_arns_to_activate = [
        instance["containerInstanceArn"]
        for instance in describe_response["containerInstances"]
        if instance["ec2InstanceId"] in instance_ids
    ]

    if not instance_arns_to_activate:
        print(f"No ECS instances found in ASG: {asg_name}")
        return

    ecs_client.update_container_instances_state(
        cluster=cluster_name,
        containerInstances=instance_arns_to_activate,
        status="ACTIVE"
    )

    print(f"Successfully updated {len(instance_arns_to_activate)} instances to ACTIVE in ECS cluster {cluster_name}.")


def wait_for_healthy_loadbalancer():
    elbv2 = boto3.client('elbv2')

    for _ in range(60):  # wait up to 10mins
        response = elbv2.describe_target_health(TargetGroupArn=ALB_ARN)

        unhealthy_instances = []
        for target in response['TargetHealthDescriptions']:
            instance_id = target['Target']['Id']
            health_state = target['TargetHealth']['State']

            if health_state != 'healthy':
                unhealthy_instances.append(instance_id)

        if not unhealthy_instances:
            print("✅ All instances are healthy!")
            return

        print(f"waiting for the following instances prior to scaledown:  {', '.join([str(s) for s in unhealthy_instances])}")

        time.sleep(10)  # Wait 10 seconds before polling again

    raise Exception("timed out waiting for a healthy loadbalance.  aborting scaledown")


def get_asg_min_instances(auto_scaling_group_name: str) -> int:
    client = boto3.client('autoscaling')

    response = client.describe_auto_scaling_groups(AutoScalingGroupNames=[auto_scaling_group_name])

    if response['AutoScalingGroups']:
        return int(response['AutoScalingGroups'][0]['MinSize'])
    else:
        raise ValueError(f"Auto Scaling Group '{auto_scaling_group_name}' not found.")


def get_unhealthy_instances(target_group_arn):
    """Retrieve unhealthy instances from the ALB target group."""
    response = elbv2_client.describe_target_health(TargetGroupArn=target_group_arn)

    unhealthy_instances = [
        target["Target"]["Id"]
        for target in response["TargetHealthDescriptions"]
        if target["TargetHealth"]["State"] == "unhealthy"
    ]

    return unhealthy_instances


def get_asg_instances(asg_name):
    """Retrieve instances from the Auto Scaling Group."""
    response = asg_client.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])

    if not response["AutoScalingGroups"]:
        return [], 0  # No ASG found

    asg = response["AutoScalingGroups"][0]
    instance_ids = [inst["InstanceId"] for inst in asg["Instances"]]
    desired_count = asg["DesiredCapacity"]

    return instance_ids, desired_count


def deregister_instances_from_alb(target_group_arn, instance_ids):
    """Deregister instances from ALB Target Group."""
    if not instance_ids:
        print("No unhealthy instances to deregister.")
        return

    targets = [{"Id": instance_id} for instance_id in instance_ids]

    elbv2_client.deregister_targets(TargetGroupArn=target_group_arn, Targets=targets)
    print(f"Deregistered instances from ALB: {instance_ids}")


def scale_down_asg(asg_name, instance_ids_to_remove):
    for instance_id in instance_ids_to_remove:
        asg_client.terminate_instance_in_auto_scaling_group(
            InstanceId=instance_id,
            ShouldDecrementDesiredCapacity=True
        )
        print(f"Terminated instance: {instance_id}")
