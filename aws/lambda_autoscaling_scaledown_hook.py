import json
import logging
import pprint
import urllib.request

import boto3


def lambda_handler(event, context):
    sns_message = json.loads(event['Records'][0]['Sns']['Message'])
    pprint.pprint(sns_message)

    instance_id = sns_message['EC2InstanceId']
    lifecycle_hook_name = sns_message['LifecycleHookName']
    auto_scaling_group_name = sns_message['AutoScalingGroupName']
    lifecycle_action_token = sns_message['LifecycleActionToken']

    print("instance_id", instance_id)
    print("lifecycle_hook_name", lifecycle_hook_name)
    print("auto_scaling_group_name", auto_scaling_group_name)
    print("lifecycle_action_token", lifecycle_action_token)

    try:
        response = make_http_request(
            f"https://collaya.com/admin/thread_drain/request?instance_status=kill_requested&instance_id={instance_id}"
        )
    except Exception as e:
        logging.exception(e)

    response = boto3.client('autoscaling').complete_lifecycle_action(
        LifecycleHookName=lifecycle_hook_name,
        AutoScalingGroupName=auto_scaling_group_name,
        LifecycleActionToken=lifecycle_action_token,
        LifecycleActionResult='CONTINUE'
    )

    return {
        'statusCode': 200,
        'body': json.dumps(f"Lifecycle action completed for instance {instance_id}")
    }


def make_http_request(url):
    print("calling", url)

    response_body = fetch_url(url)

    print("response_body", response_body)
    return json.loads(response_body)


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
