"""
Email Sender Lambda
Triggered by SQS to send individual emails via SES
Rate-limited by event source max concurrency plus per-invocation pacing
"""

import json
import os
import random
import time
from collections import defaultdict

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, ParamValidationError

from campaign_completion import complete_if_done

# Import unsubscribe helper
try:
    from unsubscribe_helper import generate_list_unsubscribe_headers, get_env_config
    UNSUBSCRIBE_AVAILABLE = True
except ImportError:
    UNSUBSCRIBE_AVAILABLE = False
    print('Warning: unsubscribe_helper not available')

dynamodb = boto3.resource('dynamodb')

# Disable boto3 retries for SES - throttling is handled by requeueing with a delay
ses_config = Config(
    retries={
        'max_attempts': 0,  # No automatic retries
        'mode': 'standard'
    }
)
ses = boto3.client('ses', config=ses_config)
sesv2 = boto3.client('sesv2', config=ses_config)
sqs = boto3.client('sqs')
sns = boto3.client('sns')

CAMPAIGN_TABLE_NAME = os.environ['CAMPAIGN_TABLE_NAME']
EMAIL_QUEUE_URL = os.environ['EMAIL_QUEUE_URL']
DLQ_QUEUE_URL = os.environ['DLQ_QUEUE_URL']
NOTIFICATION_TOPIC_ARN = os.environ.get('NOTIFICATION_TOPIC_ARN', '')
# Must match the queue's redrive maxReceiveCount so we record the failure before SQS moves it
MAX_RECEIVE_COUNT = int(os.environ.get('MAX_RECEIVE_COUNT', '5'))
# Emails per second for one invocation (0 = unpaced). Total rate = this x event source max concurrency
MAX_SEND_RATE = float(os.environ.get('MAX_SEND_RATE_PER_INVOCATION', '0'))

campaign_table = dynamodb.Table(CAMPAIGN_TABLE_NAME)

# Throttled messages are requeued with exponential backoff, then dead-lettered
MAX_THROTTLE_RETRIES = 10
MAX_SQS_DELAY_SECONDS = 900

THROTTLING_ERRORS = {'Throttling', 'ThrottlingException', 'TooManyRequestsException'}

# Daily sending quota won't clear for hours, so retrying just burns the queue
QUOTA_ERRORS = {'LimitExceededException'}

PERMANENT_ERRORS = {
    # SES v1 (SendTemplatedEmail)
    'MessageRejected', 'MailFromDomainNotVerifiedException', 'MailFromDomainNotVerified',
    'TemplateDoesNotExist', 'ConfigurationSetDoesNotExist', 'ConfigurationSetSendingPausedException',
    'AccountSendingPausedException', 'InvalidParameterValue',
    # SES v2 (SendEmail)
    'NotFoundException', 'BadRequestException', 'SendingPausedException', 'AccountSuspendedException',
    # Credentials / validation
    'ValidationError', 'AccessDenied', 'AccessDeniedException',
    'InvalidClientTokenId', 'SignatureDoesNotMatch',
}

# Campaign status when its record no longer exists (deleted by the user)
CAMPAIGN_NOT_FOUND = 'NOT_FOUND'

# Campaigns the user stopped, cancelled or deleted - remaining messages are dropped
STOPPED_STATUSES = {'STOPPED', 'CANCELLED', CAMPAIGN_NOT_FOUND}


def handler(event, context):
    """
    Process SQS batch (up to 20 messages)

    Successful, dead-lettered and requeued messages are deleted by Lambda when the
    handler returns. Only messages listed in batchItemFailures go back to the queue.
    """

    batch_item_failures = []
    progress = defaultdict(lambda: {'sent': 0, 'failed': 0})
    status_cache = {}
    last_send = 0.0

    for record in event['Records']:
        message_id = record['messageId']

        try:
            message = json.loads(record['body'])
            campaign_key = (message['campaign_id'], int(message['schedule_timestamp']))
        except (ValueError, KeyError, TypeError) as e:
            # Unparseable - no campaign to count it against, keep it for inspection
            print(f'Malformed message {message_id}, sending to DLQ: {str(e)}')
            if not send_raw_to_dlq(record['body'], f'Malformed message: {str(e)}'):
                batch_item_failures.append({'itemIdentifier': message_id})
            continue

        if campaign_key not in status_cache:
            status_cache[campaign_key] = get_campaign_status(*campaign_key)

        if status_cache[campaign_key] in STOPPED_STATUSES:
            continue

        # Pace sends so concurrency x rate never exceeds the configured TPS
        if MAX_SEND_RATE > 0:
            wait = last_send + (1.0 / MAX_SEND_RATE) - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            last_send = time.monotonic()

        try:
            send_email(message)
            progress[campaign_key]['sent'] += 1
        except Exception as e:
            outcome = handle_send_error(e, message, record)
            if outcome == 'failed':
                progress[campaign_key]['failed'] += 1
            elif outcome == 'retry':
                batch_item_failures.append({'itemIdentifier': message_id})

    # One counter update per campaign per batch; the post-update item tells us if it's done
    for (campaign_id, schedule_timestamp), counts in progress.items():
        campaign = update_campaign_progress(campaign_id, schedule_timestamp, counts['sent'], counts['failed'])
        if campaign:
            complete_if_done(campaign_table, campaign, send_notification)

    print(f"Processed {len(event['Records'])} messages, {len(batch_item_failures)} returned for retry")

    return {'batchItemFailures': batch_item_failures}


def send_email(message):
    """Send one email, using SES v2 when List-Unsubscribe headers are needed"""

    tags = [{'Name': k, 'Value': str(v)} for k, v in (message.get('tags') or {}).items()]
    template_data = json.dumps(message.get('template_data', {}))
    email_headers = build_unsubscribe_headers(message)

    if email_headers:
        # SES v2 API supports templates WITH custom headers
        params = {
            'FromEmailAddress': message['from_email'],
            'Destination': {'ToAddresses': [message['to_address']]},
            'Content': {
                'Template': {
                    'TemplateName': message['template_name'],
                    'TemplateData': template_data,
                    'Headers': [{'Name': name, 'Value': value} for name, value in email_headers.items()]
                }
            },
        }
        if message.get('configuration_set'):
            params['ConfigurationSetName'] = message['configuration_set']
        if tags:
            params['EmailTags'] = tags
        return sesv2.send_email(**params)

    # Standard templated email (SES v1 API)
    params = {
        'Source': message['from_email'],
        'Destination': {'ToAddresses': [message['to_address']]},
        'Template': message['template_name'],
        'TemplateData': template_data,
    }
    if message.get('configuration_set'):
        params['ConfigurationSetName'] = message['configuration_set']
    if tags:
        params['Tags'] = tags
    return ses.send_templated_email(**params)


def build_unsubscribe_headers(message):
    """Return List-Unsubscribe headers for this message, or None"""

    if not message.get('unsubscribe_enabled') or not UNSUBSCRIBE_AVAILABLE:
        return None
    if message.get('unsubscribe_type', 'both') not in ['headers', 'both']:
        return None

    try:
        config = get_env_config()
        return generate_list_unsubscribe_headers(
            message['to_address'],
            config['encryption_key'],
            config['endpoint_url'],
            config['mailto'],
            message.get('unsubscribe_topic')
        )
    except Exception as e:
        print(f'Warning: Failed to generate unsubscribe headers: {str(e)}')
        return None


def handle_send_error(error, message, record):
    """
    Decide what happens to a message whose send failed

    Returns:
        'failed'   - dead-lettered and counted as failed
        'requeued' - sent back to the queue with a delay
        'retry'    - return to SQS via batchItemFailures
    """

    error_code, error_message = get_error_details(error)

    if error_code in QUOTA_ERRORS or 'daily message quota' in error_message.lower():
        print(f'Sending quota exceeded, sending to DLQ: {error_message}')
        return dead_letter(message, f'Sending quota exceeded: {error_message}')

    if error_code in THROTTLING_ERRORS:
        retries = int(message.get('throttle_retries', 0)) + 1
        if retries > MAX_THROTTLE_RETRIES:
            return dead_letter(message, f'Throttled {MAX_THROTTLE_RETRIES} times: {error_message}')

        message['throttle_retries'] = retries
        backoff = 2 ** retries
        delay = min(MAX_SQS_DELAY_SECONDS, int(backoff + random.uniform(0, backoff)))  # nosec B311 - jitter, not security
        try:
            sqs.send_message(QueueUrl=EMAIL_QUEUE_URL, MessageBody=json.dumps(message), DelaySeconds=delay)
            print(f'Throttled, requeued with {delay}s delay (attempt {retries}): {error_message}')
            return 'requeued'
        except Exception as e:
            print(f'Failed to requeue throttled message: {str(e)}')
            return 'retry'

    if error_code in PERMANENT_ERRORS or isinstance(error, ParamValidationError):
        print(f'Permanent error, sending to DLQ: {error_message}')
        return dead_letter(message, error_message)

    # Unknown error - let SQS retry, but record the failure ourselves on the last attempt
    receive_count = int(record.get('attributes', {}).get('ApproximateReceiveCount', '1'))
    if receive_count >= MAX_RECEIVE_COUNT:
        return dead_letter(message, f'Failed after {receive_count} attempts: {error_message}')

    print(f'Unknown error (attempt {receive_count}/{MAX_RECEIVE_COUNT}), leaving for SQS retry: {error_message}')
    return 'retry'


def get_error_details(error):
    """Return (error_code, error_message) for boto and non-boto exceptions"""

    if isinstance(error, ClientError):
        details = error.response.get('Error', {})
        return details.get('Code', ''), details.get('Message', str(error))
    return '', str(error)


def dead_letter(message, error_message):
    """Move a message to the DLQ. Returns 'failed', or 'retry' if the DLQ write failed"""

    message['error_message'] = error_message
    if send_raw_to_dlq(json.dumps(message), error_message):
        return 'failed'
    return 'retry'


def send_raw_to_dlq(body, error_message):
    """Send a message body to the DLQ, returning True on success"""

    try:
        sqs.send_message(
            QueueUrl=DLQ_QUEUE_URL,
            MessageBody=body,
            MessageAttributes={'error_message': {'DataType': 'String', 'StringValue': error_message[:1000] or 'unknown'}}
        )
        return True
    except Exception as e:
        print(f'Failed to send message to DLQ: {str(e)}')
        return False


def get_campaign_status(campaign_id, schedule_timestamp):
    """Return the campaign's status, CAMPAIGN_NOT_FOUND if deleted, or None if it can't be read"""

    try:
        response = campaign_table.get_item(
            Key={'campaign_id': campaign_id, 'schedule_timestamp': schedule_timestamp},
            ProjectionExpression='#status',
            ExpressionAttributeNames={'#status': 'status'}
        )
        if 'Item' not in response:
            return CAMPAIGN_NOT_FOUND
        return response['Item'].get('status')
    except Exception as e:
        # Fail open - a read error shouldn't stop a campaign
        print(f'Error reading campaign status: {str(e)}')
        return None


def update_campaign_progress(campaign_id, schedule_timestamp, sent=0, failed=0):
    """Add to campaign progress counters, returning the updated item (or None)"""

    if not sent and not failed:
        return None

    try:
        response = campaign_table.update_item(
            Key={
                'campaign_id': campaign_id,
                'schedule_timestamp': schedule_timestamp
            },
            UpdateExpression='SET sent_count = sent_count + :sent, failed_count = failed_count + :failed',
            ConditionExpression='attribute_exists(campaign_id)',
            ExpressionAttributeValues={':sent': sent, ':failed': failed},
            ReturnValues='ALL_NEW'
        )
        return response.get('Attributes')

    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            print(f'Campaign {campaign_id} no longer exists, progress not recorded')
        else:
            print(f'Error updating campaign progress: {str(e)}')
    except Exception as e:
        print(f'Error updating campaign progress: {str(e)}')
    return None


def send_notification(subject, message):
    """Send SNS notification"""

    if not NOTIFICATION_TOPIC_ARN:
        return

    try:
        sns.publish(
            TopicArn=NOTIFICATION_TOPIC_ARN,
            # SNS subjects must be ASCII and under 100 characters
            Subject=subject.encode('ascii', 'ignore').decode()[:99],
            Message=message
        )
    except Exception as e:
        print(f'Failed to send notification: {str(e)}')
