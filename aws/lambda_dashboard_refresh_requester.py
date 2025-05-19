import urllib.request


def lambda_handler(event, context):
    try:
        request = urllib.request.Request(
            f"https://collaya.com/admin/job/request?job=dashboard_refresh",
            headers={
                'X-Custom-Service': "lambda_job_requester"
            })

        with urllib.request.urlopen(request) as response:
            response_body = response.read().decode('utf-8')

        status_code = response.getcode()

        return {
            'statusCode': status_code,
            'body': response_body
        }
    except Exception as e:
        return {
            'statusCode': 500,
            'body': str(e)
        }
