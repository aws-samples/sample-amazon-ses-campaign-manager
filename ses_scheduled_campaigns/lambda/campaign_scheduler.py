"""
Campaign Scheduler Lambda
Called by Amazon SES Campaign Manager to schedule, list, cancel and inspect campaigns
"""

import json
import os
import time
import boto3
from datetime import datetime, timezone
from decimal import Decimal
from botocore.exceptions import ClientError
import secrets

from unsubscribe_config import check_unsubscribe_config

dynamodb = boto3.resource('dynamodb')
s3 = boto3.client('s3')
events = boto3.client('events')
lambda_client = boto3.client('lambda')

CAMPAIGN_TABLE_NAME = os.environ['CAMPAIGN_TABLE_NAME']
CAMPAIGN_BUCKET_NAME = os.environ['CAMPAIGN_BUCKET_NAME']
CAMPAIGN_PROCESSOR_ARN = os.environ['CAMPAIGN_PROCESSOR_ARN']
CAMPAIGN_RETENTION_DAYS = int(os.environ.get('CAMPAIGN_RETENTION_DAYS', '90'))

campaign_table = dynamodb.Table(CAMPAIGN_TABLE_NAME)

# EventBridge cron has minute granularity and must exist before it fires,
# so anything sooner than this is handed to the processor immediately
MIN_SCHEDULE_LEAD_SECONDS = 60

# Times this far in the past (slow CSV upload, clock skew) still send immediately instead of failing
PAST_SCHEDULE_GRACE_SECONDS = 300

CAMPAIGN_STATUSES = ['SCHEDULED', 'PROCESSING', 'COMPLETED', 'FAILED', 'STOPPED', 'CANCELLED']


class DecimalEncoder(json.JSONEncoder):
    """Helper class to convert DynamoDB Decimals to native Python types"""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return int(obj) if obj % 1 == 0 else float(obj)
        return super(DecimalEncoder, self).default(obj)


def handler(event, context):
    """
    Handle campaign scheduling requests from Amazon SES Campaign Manager

    Expected event structure:
    {
        "action": "schedule" | "list" | "cancel" | "get_status",
        "campaign_data": {  # for schedule action
            "campaign_name": "Campaign Name",
            "schedule_timestamp": 1234567890,  # Unix timestamp
            "csv_s3_key": "campaigns/campaign_123.csv",
            "template_name": "MyTemplate",
            "from_email": "sender@example.com",
            "configuration_set": "my-config-set",
            "tags": {"campaign_name": "My_Campaign"},  # campaign_id is added automatically
            "total_recipients": 1000,
            "template_data": {},  # base template data
            "unsubscribe_enabled": false,
            "unsubscribe_type": "both",
            "unsubscribe_topic": null,
            "unsubscribe_key_fingerprint": "..."  # optional, checked against the stack's key
        },
        "status": "COMPLETED",  # optional filter for list action
        "campaign_id": "abc-123"  # for cancel/get_status actions
    }
    """

    try:
        action = event.get('action', 'schedule')

        if action == 'schedule':
            return schedule_campaign(event.get('campaign_data', {}))
        elif action == 'list':
            return list_campaigns(
                event.get('status'),
                event.get('limit', 50),
                event.get('future_only', False)
            )
        elif action == 'cancel':
            return cancel_campaign(event.get('campaign_id'))
        elif action == 'get_status':
            return get_campaign_status(event.get('campaign_id'))
        else:
            return error_response(400, f'Unknown action: {action}')

    except Exception as e:
        print(f"Error in campaign_scheduler: {str(e)}")
        return error_response(500, str(e))


def error_response(status_code, message):
    """Build an error response"""
    return {
        'statusCode': status_code,
        'body': json.dumps({'error': message})
    }


def to_dynamodb(value):
    """Convert JSON floats to Decimal - DynamoDB rejects Python floats"""
    return json.loads(json.dumps(value), parse_float=Decimal)


def schedule_campaign(campaign_data):
    """Schedule a new campaign"""

    # Validate required fields
    required_fields = [
        'campaign_name', 'schedule_timestamp', 'csv_s3_key',
        'template_name', 'from_email', 'total_recipients'
    ]

    for field in required_fields:
        if field not in campaign_data:
            return error_response(400, f'Missing required field: {field}')

    try:
        schedule_timestamp = int(campaign_data['schedule_timestamp'])
    except (TypeError, ValueError):
        return error_response(400, 'schedule_timestamp must be a Unix timestamp in seconds')

    now = int(time.time())
    schedule_date = datetime.fromtimestamp(schedule_timestamp, tz=timezone.utc)

    if schedule_timestamp < now - PAST_SCHEDULE_GRACE_SECONDS:
        return error_response(400, f'Schedule time {schedule_date.isoformat()} is in the past')

    send_now = schedule_timestamp < now + MIN_SCHEDULE_LEAD_SECONDS

    unsubscribe_enabled = bool(campaign_data.get('unsubscribe_enabled', False))
    unsubscribe_type = campaign_data.get('unsubscribe_type', 'both')

    if unsubscribe_enabled:
        unsubscribe_error = check_unsubscribe_config(
            unsubscribe_type,
            campaign_data.get('unsubscribe_key_fingerprint')
        )
        if unsubscribe_error:
            return error_response(400, unsubscribe_error)

    # Verify CSV exists in S3
    try:
        s3.head_object(Bucket=CAMPAIGN_BUCKET_NAME, Key=campaign_data['csv_s3_key'])
    except Exception as e:
        return error_response(400, f'CSV file not found in S3: {str(e)}')

    tags = dict(campaign_data.get('tags') or {})

    # Create campaign record in DynamoDB
    campaign_item = {
        'schedule_timestamp': schedule_timestamp,
        'campaign_name': campaign_data['campaign_name'],
        'status': 'SCHEDULED',
        'csv_s3_path': f's3://{CAMPAIGN_BUCKET_NAME}/{campaign_data["csv_s3_key"]}',
        'template_name': campaign_data['template_name'],
        'from_email': campaign_data['from_email'],
        'configuration_set': campaign_data.get('configuration_set', ''),
        'total_recipients': int(campaign_data['total_recipients']),
        'sent_count': 0,
        'failed_count': 0,
        'template_data': to_dynamodb(campaign_data.get('template_data', {})),
        'unsubscribe_enabled': unsubscribe_enabled,
        'unsubscribe_type': unsubscribe_type,
        'unsubscribe_topic': campaign_data.get('unsubscribe_topic'),
        'created_at': now,
        'started_at': 0,
        'completed_at': 0,
        'error_message': '',
        # Keep the record (and its sent/failed counts) for the retention period after the send
        'ttl': schedule_timestamp + CAMPAIGN_RETENTION_DAYS * 24 * 60 * 60,
    }

    campaign_id = None
    for _ in range(3):
        # Generate campaign ID (same format as bulk emails for consistency)
        # Format: {unix_timestamp}-{4_char_random}
        candidate_id = f"{now}-{secrets.token_hex(2)}"  # 2 bytes = 4 hex characters

        # Every email carries the campaign ID so analytics can attribute its events
        campaign_item['campaign_id'] = candidate_id
        campaign_item['tags'] = to_dynamodb({**tags, 'campaign_id': candidate_id})

        try:
            campaign_table.put_item(Item=campaign_item, ConditionExpression='attribute_not_exists(campaign_id)')
            campaign_id = candidate_id
            break
        except ClientError as e:
            if e.response['Error']['Code'] != 'ConditionalCheckFailedException':
                raise

    if not campaign_id:
        return error_response(500, 'Could not generate a unique campaign ID, please retry')

    try:
        if send_now:
            lambda_client.invoke(
                FunctionName=CAMPAIGN_PROCESSOR_ARN,
                InvocationType='Event',
                Payload=json.dumps({'campaign_id': campaign_id})
            )
        else:
            create_schedule_rule(campaign_id, schedule_date, campaign_data['campaign_name'])
    except Exception as e:
        # Never leave a SCHEDULED campaign that nothing will trigger
        error_msg = f'Failed to create campaign trigger: {str(e)}'
        print(error_msg)
        delete_schedule_rule(campaign_id)
        mark_failed(campaign_id, schedule_timestamp, error_msg)
        return error_response(500, error_msg)

    if send_now:
        print(f'Campaign {campaign_id} scheduled time {schedule_date.isoformat()} is less than '
              f'{MIN_SCHEDULE_LEAD_SECONDS}s away, started immediately')
    else:
        print(f'Scheduled campaign {campaign_id} for {schedule_date.isoformat()}')

    return {
        'statusCode': 200,
        'body': json.dumps({
            'campaign_id': campaign_id,
            'status': 'SCHEDULED',
            'schedule_time': schedule_date.isoformat(),
            'sent_immediately': send_now,
            'message': 'Campaign started immediately' if send_now else 'Campaign scheduled successfully'
        })
    }


def create_schedule_rule(campaign_id, schedule_date, campaign_name):
    """Create a one-time EventBridge rule that triggers the Campaign Processor"""

    rule_name = f'ses-campaign-{campaign_id}'

    # One-time cron in UTC (EventBridge cron is always UTC)
    cron_expression = (
        f'cron({schedule_date.minute} {schedule_date.hour} '
        f'{schedule_date.day} {schedule_date.month} ? {schedule_date.year})'
    )

    events.put_rule(
        Name=rule_name,
        ScheduleExpression=cron_expression,
        State='ENABLED',
        Description=f'Trigger for campaign: {campaign_name}'[:512]
    )

    # Add target to invoke Campaign Processor Lambda
    events.put_targets(
        Rule=rule_name,
        Targets=[
            {
                'Id': '1',
                'Arn': CAMPAIGN_PROCESSOR_ARN,
                'Input': json.dumps({'campaign_id': campaign_id})
            }
        ]
    )


def delete_schedule_rule(campaign_id):
    """Delete a campaign's EventBridge rule if it exists"""

    rule_name = f'ses-campaign-{campaign_id}'

    try:
        # Remove targets first
        events.remove_targets(Rule=rule_name, Ids=['1'])
        # Then delete rule
        events.delete_rule(Name=rule_name)
    except events.exceptions.ResourceNotFoundException:
        print(f'EventBridge rule {rule_name} not found, skipping deletion')
    except Exception as e:
        print(f'Error deleting EventBridge rule {rule_name}: {str(e)}')


def mark_failed(campaign_id, schedule_timestamp, error_message):
    """Mark a campaign that could not be scheduled as FAILED"""

    try:
        campaign_table.update_item(
            Key={'campaign_id': campaign_id, 'schedule_timestamp': schedule_timestamp},
            UpdateExpression='SET #status = :failed, error_message = :error',
            ConditionExpression='#status = :scheduled',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':failed': 'FAILED',
                ':scheduled': 'SCHEDULED',
                ':error': error_message
            }
        )
    except Exception as e:
        print(f'Failed to mark campaign {campaign_id} as failed: {str(e)}')


def list_campaigns(status_filter=None, limit=50, future_only=False):
    """
    List campaigns using the status-index GSI

    Args:
        status_filter: Only return campaigns with this status (default: all statuses)
        limit: Maximum number of campaigns to return
        future_only: Only return SCHEDULED campaigns in the future, soonest first
    """

    try:
        limit = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        limit = 50

    if status_filter and status_filter not in CAMPAIGN_STATUSES:
        return error_response(400, f'Unknown status: {status_filter}')

    try:
        current_timestamp = int(time.time())

        if future_only:
            # Efficient GSI query: status='SCHEDULED' AND schedule_timestamp > now
            campaigns = query_by_status('SCHEDULED', limit, ascending=True, after_timestamp=current_timestamp)
        else:
            statuses = [status_filter] if status_filter else CAMPAIGN_STATUSES
            campaigns = []
            for status in statuses:
                campaigns.extend(query_by_status(status, limit, ascending=False))

            # Most recent schedule first across all statuses
            campaigns.sort(key=lambda x: x.get('schedule_timestamp', 0), reverse=True)
            campaigns = campaigns[:limit]

        return {
            'statusCode': 200,
            'body': json.dumps({
                'campaigns': campaigns,
                'count': len(campaigns),
                'future_only': future_only
            }, cls=DecimalEncoder)
        }

    except Exception as e:
        return error_response(500, f'Failed to list campaigns: {str(e)}')


def query_by_status(status, limit, ascending, after_timestamp=None):
    """Query up to `limit` campaigns with one status, following pagination"""

    key_condition = '#status = :status'
    expression_values = {':status': status}

    if after_timestamp is not None:
        key_condition += ' AND schedule_timestamp > :now'
        expression_values[':now'] = after_timestamp

    query_args = {
        'IndexName': 'status-index',
        'KeyConditionExpression': key_condition,
        'ExpressionAttributeNames': {'#status': 'status'},
        'ExpressionAttributeValues': expression_values,
        'ScanIndexForward': ascending,
    }

    items = []
    while len(items) < limit:
        response = campaign_table.query(Limit=limit - len(items), **query_args)
        items.extend(response.get('Items', []))

        if 'LastEvaluatedKey' not in response:
            break
        query_args['ExclusiveStartKey'] = response['LastEvaluatedKey']

    return items


def get_campaign(campaign_id):
    """Return the campaign item, or None if it doesn't exist"""

    response = campaign_table.query(
        KeyConditionExpression='campaign_id = :cid',
        ExpressionAttributeValues={':cid': campaign_id},
        Limit=1
    )
    items = response.get('Items', [])
    return items[0] if items else None


def cancel_campaign(campaign_id):
    """
    Cancel a scheduled campaign, or stop one that is already sending

    SCHEDULED  -> CANCELLED (EventBridge rule deleted, nothing is sent)
    PROCESSING -> STOPPED   (emails still in the queue are discarded)
    """

    if not campaign_id:
        return error_response(400, 'campaign_id is required')

    try:
        campaign = get_campaign(campaign_id)

        if not campaign:
            return error_response(404, 'Campaign not found')

        current_status = campaign['status']

        if current_status == 'SCHEDULED':
            new_status = 'CANCELLED'
            message = 'Campaign cancelled successfully'
        elif current_status == 'PROCESSING':
            new_status = 'STOPPED'
            message = ('Campaign stopped. Emails already accepted by SES will still be delivered; '
                       'emails still waiting in the queue will not be sent.')
        else:
            return error_response(400, f'Cannot cancel campaign with status: {current_status}')

        # Conditional so we never overwrite a status the processor changed in the meantime
        try:
            campaign_table.update_item(
                Key={
                    'campaign_id': campaign_id,
                    'schedule_timestamp': campaign['schedule_timestamp']
                },
                UpdateExpression='SET #status = :new_status, stopped_at = :now',
                ConditionExpression='#status = :current_status',
                ExpressionAttributeNames={'#status': 'status'},
                ExpressionAttributeValues={
                    ':new_status': new_status,
                    ':current_status': current_status,
                    ':now': int(time.time())
                }
            )
        except ClientError as e:
            if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                return error_response(409, 'Campaign status changed while cancelling (it may have just started). Refresh and try again.')
            raise

        # Status is already CANCELLED/STOPPED, so even if the rule fires now the processor skips it
        delete_schedule_rule(campaign_id)

        return {
            'statusCode': 200,
            'body': json.dumps({
                'campaign_id': campaign_id,
                'status': new_status,
                'message': message
            })
        }

    except Exception as e:
        return error_response(500, f'Failed to cancel campaign: {str(e)}')


def get_campaign_status(campaign_id):
    """Get status and progress of a campaign"""

    if not campaign_id:
        return error_response(400, 'campaign_id is required')

    try:
        campaign = get_campaign(campaign_id)

        if not campaign:
            return error_response(404, 'Campaign not found')

        # Calculate progress percentage
        total = campaign.get('total_recipients', 0)
        sent = campaign.get('sent_count', 0)
        failed = campaign.get('failed_count', 0)

        progress = 0
        if total > 0:
            progress = round(((sent + failed) / total) * 100, 2)

        return {
            'statusCode': 200,
            'body': json.dumps({
                'campaign_id': campaign_id,
                'campaign_name': campaign.get('campaign_name', ''),
                'status': campaign.get('status', ''),
                'progress_percent': progress,
                'total_recipients': total,
                'sent_count': sent,
                'failed_count': failed,
                'enqueued_count': campaign.get('enqueued_count', 0),
                'schedule_timestamp': campaign.get('schedule_timestamp', 0),
                'started_at': campaign.get('started_at', 0),
                'completed_at': campaign.get('completed_at', 0),
                'stopped_at': campaign.get('stopped_at', 0),
                'error_message': campaign.get('error_message', '')
            }, cls=DecimalEncoder)
        }

    except Exception as e:
        return error_response(500, f'Failed to get campaign status: {str(e)}')
