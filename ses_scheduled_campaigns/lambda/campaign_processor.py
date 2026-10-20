"""
Campaign Processor Lambda
Triggered by EventBridge at scheduled time (or by the scheduler directly when the
scheduled time is less than a minute away) to process a campaign
Reads CSV from S3 and enqueues emails to SQS
"""

import json
import os
import time
import boto3
import csv
from io import StringIO
from decimal import Decimal
from botocore.exceptions import ClientError

from campaign_completion import complete_if_done
from unsubscribe_config import check_unsubscribe_config

# Import unsubscribe helper
try:
    from unsubscribe_helper import add_unsubscribe_to_template_data
    UNSUBSCRIBE_AVAILABLE = True
except ImportError:
    UNSUBSCRIBE_AVAILABLE = False
    print('Warning: unsubscribe_helper not available')

dynamodb = boto3.resource('dynamodb')
s3 = boto3.client('s3')
sqs = boto3.client('sqs')
events = boto3.client('events')
sns = boto3.client('sns')

CAMPAIGN_TABLE_NAME = os.environ['CAMPAIGN_TABLE_NAME']
EMAIL_QUEUE_URL = os.environ['EMAIL_QUEUE_URL']
NOTIFICATION_TOPIC_ARN = os.environ.get('NOTIFICATION_TOPIC_ARN', '')
CAMPAIGN_RETENTION_DAYS = int(os.environ.get('CAMPAIGN_RETENTION_DAYS', '90'))

campaign_table = dynamodb.Table(CAMPAIGN_TABLE_NAME)

# SQS batch size limit
SQS_BATCH_SIZE = 10
SQS_MAX_ATTEMPTS = 5

# Persist enqueue progress every 100 messages so a crashed run re-sends at most that many
CHECKPOINT_EVERY_BATCHES = 10

# Stop enqueueing with this much time left so progress can be saved for the retry
TIME_BUFFER_MS = 30000


class OutOfTimeError(Exception):
    """Raised to hand the remaining recipients to Lambda's async retry"""


class CampaignStoppedError(Exception):
    """Raised when the campaign is stopped while recipients are being enqueued"""


def handler(event, context):
    """
    Process scheduled campaign
    1. Claim the campaign (SCHEDULED -> PROCESSING) so duplicate triggers are ignored
    2. Read CSV from S3
    3. Enqueue messages to SQS, saving progress as it goes
    4. Mark enqueue complete, clean up EventBridge rule and CSV
    """

    campaign_id = event.get('campaign_id')

    if not campaign_id:
        raise ValueError('campaign_id not provided in event')

    print(f'Processing campaign: {campaign_id}')

    campaign = get_campaign(campaign_id)

    if not campaign:
        # Deleted before it ran - nothing to send and nothing to retry
        print(f'Campaign {campaign_id} not found (deleted?), skipping')
        cleanup_eventbridge_rule(campaign_id)
        return {'statusCode': 200, 'message': 'Campaign not found'}

    claimed = claim_campaign(campaign, context)

    if not claimed:
        print(f'Campaign {campaign_id} has status {campaign["status"]}, skipping')
        return {'statusCode': 200, 'message': 'Campaign already processed, in progress or cancelled'}

    schedule_timestamp = claimed['schedule_timestamp']
    progress = {'enqueued': int(claimed.get('enqueued_count', 0)), 'total': 0}

    try:
        recipients = read_csv_from_s3(claimed['csv_s3_path'])

        if not recipients:
            raise ValueError('No recipients found in CSV')

        progress['total'] = len(recipients)

        if claimed.get('unsubscribe_enabled'):
            # Fail the campaign rather than send it without the unsubscribe links it was scheduled with
            if not UNSUBSCRIBE_AVAILABLE:
                raise RuntimeError('Unsubscribe is enabled but the cryptography layer is not available')
            unsubscribe_error = check_unsubscribe_config(claimed.get('unsubscribe_type', 'both'))
            if unsubscribe_error:
                raise RuntimeError(unsubscribe_error)

        if progress['enqueued']:
            print(f'Resuming enqueue at recipient {progress["enqueued"]} of {len(recipients)}')
        else:
            print(f'Found {len(recipients)} recipients')

        enqueue_emails(campaign_id, claimed, recipients, progress, context)

        print(f'Enqueued {len(recipients)} messages to SQS')

        campaign = mark_enqueue_complete(campaign_id, schedule_timestamp, len(recipients))

        cleanup_eventbridge_rule(campaign_id)
        delete_csv(claimed['csv_s3_path'])

        send_notification(
            f'Campaign Started: {claimed["campaign_name"]}',
            f'Campaign {campaign_id} has started processing {len(recipients)} emails.'
        )

        # All emails may already have been sent while we were still enqueueing
        complete_if_done(campaign_table, campaign, send_notification)

        return {
            'statusCode': 200,
            'message': f'Campaign {campaign_id} processing started',
            'recipients_count': len(recipients),
            'messages_enqueued': len(recipients)
        }

    except OutOfTimeError as e:
        # Progress is saved and the lease released - the async retry resumes from here
        print(str(e))
        raise

    except CampaignStoppedError as e:
        print(str(e))
        cleanup_eventbridge_rule(campaign_id)
        return {'statusCode': 200, 'message': str(e)}

    except Exception as e:
        error_msg = f'Error processing campaign: {str(e)}'
        if progress['enqueued']:
            error_msg += f' (after enqueuing {progress["enqueued"]} of {progress["total"]} recipients)'
        print(error_msg)

        update_campaign_with_error(campaign_id, schedule_timestamp, error_msg)

        send_notification(
            f'Campaign Failed: {claimed.get("campaign_name", campaign_id)}',
            f'Campaign {campaign_id} failed.\n\nError: {error_msg}'
        )

        raise


def get_campaign(campaign_id):
    """Return the campaign item, or None if it doesn't exist"""

    response = campaign_table.query(
        KeyConditionExpression='campaign_id = :cid',
        ExpressionAttributeValues={':cid': campaign_id},
        Limit=1
    )
    items = response.get('Items', [])
    return items[0] if items else None


def claim_campaign(campaign, context):
    """
    Atomically take ownership of the campaign for this invocation

    EventBridge delivers at least once and Lambda retries async failures, so the
    status check and the update must be a single conditional write.

    Returns:
        The updated campaign item, or None if another invocation owns it or it isn't runnable
    """

    now = int(time.time())
    lease_until = now + context.get_remaining_time_in_millis() // 1000 + 30
    key = {'campaign_id': campaign['campaign_id'], 'schedule_timestamp': campaign['schedule_timestamp']}

    # Campaigns scheduled by older versions expire 1 hour after their scheduled time,
    # which could delete the record (and stop sending) mid-campaign
    expires_at = int(campaign['schedule_timestamp']) + CAMPAIGN_RETENTION_DAYS * 24 * 60 * 60

    try:
        return campaign_table.update_item(
            Key=key,
            UpdateExpression=(
                'SET #status = :processing, started_at = :now, enqueued_count = :zero, '
                'enqueue_complete = :false, lease_until = :lease, #ttl = :ttl'
            ),
            ConditionExpression='#status = :scheduled',
            ExpressionAttributeNames={'#status': 'status', '#ttl': 'ttl'},  # TTL is a reserved word
            ExpressionAttributeValues={
                ':processing': 'PROCESSING',
                ':scheduled': 'SCHEDULED',
                ':now': now,
                ':zero': 0,
                ':false': False,
                ':lease': lease_until,
                ':ttl': expires_at
            },
            ReturnValues='ALL_NEW'
        )['Attributes']
    except ClientError as e:
        if e.response['Error']['Code'] != 'ConditionalCheckFailedException':
            raise

    # Resume an enqueue interrupted by a timeout, once the previous invocation's lease has expired
    try:
        return campaign_table.update_item(
            Key=key,
            UpdateExpression='SET lease_until = :lease',
            ConditionExpression='#status = :processing AND enqueue_complete = :false AND lease_until < :now',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':processing': 'PROCESSING',
                ':false': False,
                ':now': now,
                ':lease': lease_until
            },
            ReturnValues='ALL_NEW'
        )['Attributes']
    except ClientError as e:
        if e.response['Error']['Code'] != 'ConditionalCheckFailedException':
            raise

    return None


def read_csv_from_s3(s3_path):
    """Read and parse CSV file from S3"""

    bucket, key = parse_s3_path(s3_path)

    # Read CSV from S3 (utf-8-sig strips the BOM Excel adds to "CSV UTF-8" exports)
    response = s3.get_object(Bucket=bucket, Key=key)
    csv_content = response['Body'].read().decode('utf-8-sig')

    # Parse CSV
    recipients = []
    csv_reader = csv.DictReader(StringIO(csv_content))

    # Validate required column
    if not csv_reader.fieldnames or 'To_Address' not in csv_reader.fieldnames:
        raise ValueError("CSV must contain 'To_Address' column")

    for row_num, row in enumerate(csv_reader, start=2):
        to_address = (row.get('To_Address') or '').strip()

        if not to_address:
            print(f'Skipping row {row_num}: missing To_Address')
            continue

        # Extract substitution variables (columns starting with 'sub_')
        substitutions = {}
        for key, value in row.items():
            if key and key.startswith('sub_'):
                sub_key = key[4:]  # Remove 'sub_' prefix
                substitutions[sub_key] = value

        recipients.append({
            'to_address': to_address,
            'substitutions': substitutions,
            'row_number': row_num
        })

    return recipients


def parse_s3_path(s3_path):
    """Split s3://bucket/key into (bucket, key)"""

    parts = s3_path.replace('s3://', '', 1).split('/', 1)
    return parts[0], parts[1]


def decimal_to_number(obj):
    """Convert Decimal objects to int or float for JSON serialization"""
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    elif isinstance(obj, dict):
        return {k: decimal_to_number(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [decimal_to_number(item) for item in obj]
    return obj


def enqueue_emails(campaign_id, campaign, recipients, progress, context):
    """
    Enqueue one SQS message per recipient, starting at progress['enqueued']

    Progress is checkpointed to DynamoDB so a run that times out can be resumed by
    the async retry without re-sending recipients that were already enqueued.
    """

    schedule_timestamp = campaign['schedule_timestamp']
    batches_since_checkpoint = 0

    for batch_start in range(progress['enqueued'], len(recipients), SQS_BATCH_SIZE):
        if context.get_remaining_time_in_millis() < TIME_BUFFER_MS:
            save_checkpoint(campaign_id, schedule_timestamp, batch_start, release_lease=True)
            raise OutOfTimeError(
                f'Enqueued {batch_start} of {len(recipients)} recipients before timeout, resuming on retry'
            )

        batch_end = min(batch_start + SQS_BATCH_SIZE, len(recipients))
        batch = [
            {
                'Id': str(idx),  # Unique within the batch
                'MessageBody': json.dumps(build_message(campaign_id, campaign, recipients[idx]))
            }
            for idx in range(batch_start, batch_end)
        ]

        send_batch_to_sqs(campaign_id, schedule_timestamp, batch)
        progress['enqueued'] = batch_end

        batches_since_checkpoint += 1
        if batches_since_checkpoint >= CHECKPOINT_EVERY_BATCHES:
            save_checkpoint(campaign_id, schedule_timestamp, batch_end)
            batches_since_checkpoint = 0


def build_message(campaign_id, campaign, recipient):
    """Build the SQS message for one recipient (Decimals converted for JSON)"""

    unsubscribe_enabled = campaign.get('unsubscribe_enabled', False)
    unsubscribe_type = campaign.get('unsubscribe_type', 'both')
    unsubscribe_topic = campaign.get('unsubscribe_topic')

    # Merge base template data with recipient substitutions
    template_data = decimal_to_number({**campaign.get('template_data', {}), **recipient['substitutions']})

    # Add unsubscribe link if enabled and available
    if unsubscribe_enabled and UNSUBSCRIBE_AVAILABLE:
        try:
            template_data = add_unsubscribe_to_template_data(
                template_data,
                recipient['to_address'],
                unsubscribe_type,
                unsubscribe_topic
            )
        except Exception as e:
            print(f'Warning: Failed to add unsubscribe link for row {recipient["row_number"]}: {str(e)}')

    return {
        'campaign_id': campaign_id,
        'to_address': recipient['to_address'],
        'template_name': str(campaign['template_name']),
        'template_data': template_data,
        'from_email': str(campaign['from_email']),
        'configuration_set': str(campaign.get('configuration_set', '')),
        'tags': decimal_to_number(campaign.get('tags', {})),
        'schedule_timestamp': int(campaign['schedule_timestamp']),
        'unsubscribe_enabled': unsubscribe_enabled,
        'unsubscribe_type': unsubscribe_type,
        'unsubscribe_topic': unsubscribe_topic
    }


def send_batch_to_sqs(campaign_id, schedule_timestamp, entries):
    """Send a batch of messages to SQS, retrying entries that failed"""

    for attempt in range(SQS_MAX_ATTEMPTS):
        response = sqs.send_message_batch(
            QueueUrl=EMAIL_QUEUE_URL,
            Entries=entries
        )

        failed = response.get('Failed', [])
        if not failed:
            return

        # Sender faults (e.g. message too large) never succeed - count those recipients as failed
        permanent = [f for f in failed if f.get('SenderFault')]
        for failure in permanent:
            print(f'Recipient could not be enqueued ({failure.get("Code")}): {failure.get("Message")}')
        if permanent:
            add_failed_count(campaign_id, schedule_timestamp, len(permanent))

        retry_ids = {f['Id'] for f in failed if not f.get('SenderFault')}
        if not retry_ids:
            return

        entries = [entry for entry in entries if entry['Id'] in retry_ids]
        time.sleep(0.2 * 2 ** attempt)

    raise RuntimeError(f'Failed to enqueue {len(entries)} messages after {SQS_MAX_ATTEMPTS} attempts')


def save_checkpoint(campaign_id, schedule_timestamp, enqueued_count, release_lease=False):
    """Persist enqueue progress; raises CampaignStoppedError if the campaign was stopped"""

    update_expression = 'SET enqueued_count = :count'
    expression_values = {':count': enqueued_count, ':processing': 'PROCESSING'}

    if release_lease:
        update_expression += ', lease_until = :zero'
        expression_values[':zero'] = 0

    try:
        campaign_table.update_item(
            Key={'campaign_id': campaign_id, 'schedule_timestamp': schedule_timestamp},
            UpdateExpression=update_expression,
            ConditionExpression='#status = :processing',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues=expression_values
        )
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            raise CampaignStoppedError(
                f'Campaign {campaign_id} was stopped after {enqueued_count} recipients were enqueued'
            )
        raise


def mark_enqueue_complete(campaign_id, schedule_timestamp, total):
    """Record that every recipient is enqueued, returning the updated item"""

    try:
        return campaign_table.update_item(
            Key={'campaign_id': campaign_id, 'schedule_timestamp': schedule_timestamp},
            UpdateExpression=(
                'SET enqueue_complete = :true, enqueued_count = :total, total_recipients = :total '
                'REMOVE lease_until'
            ),
            ConditionExpression='#status = :processing',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={':true': True, ':total': total, ':processing': 'PROCESSING'},
            ReturnValues='ALL_NEW'
        )['Attributes']
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            raise CampaignStoppedError(f'Campaign {campaign_id} was stopped after all recipients were enqueued')
        raise


def add_failed_count(campaign_id, schedule_timestamp, count):
    """Count recipients that could not be enqueued as failed"""

    try:
        campaign_table.update_item(
            Key={'campaign_id': campaign_id, 'schedule_timestamp': schedule_timestamp},
            UpdateExpression='SET failed_count = failed_count + :count',
            ExpressionAttributeValues={':count': count}
        )
    except Exception as e:
        print(f'Failed to record enqueue failures: {str(e)}')


def update_campaign_with_error(campaign_id, schedule_timestamp, error_message):
    """Update campaign with error status"""

    try:
        campaign_table.update_item(
            Key={
                'campaign_id': campaign_id,
                'schedule_timestamp': schedule_timestamp
            },
            UpdateExpression='SET #status = :status, error_message = :error REMOVE lease_until',
            # Don't overwrite a campaign the user stopped in the meantime
            ConditionExpression='#status = :processing',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':status': 'FAILED',
                ':processing': 'PROCESSING',
                ':error': error_message
            }
        )
    except Exception as e:
        print(f'Failed to update campaign with error: {str(e)}')


def cleanup_eventbridge_rule(campaign_id):
    """Remove EventBridge rule after execution"""

    rule_name = f'ses-campaign-{campaign_id}'

    try:
        # Remove targets first
        events.remove_targets(Rule=rule_name, Ids=['1'])
        # Then delete rule
        events.delete_rule(Name=rule_name)
        print(f'Cleaned up EventBridge rule: {rule_name}')
    except events.exceptions.ResourceNotFoundException:
        print(f'EventBridge rule {rule_name} not found, may have been deleted already')
    except Exception as e:
        print(f'Error cleaning up EventBridge rule: {str(e)}')


def delete_csv(s3_path):
    """Delete the recipient CSV once every recipient is enqueued (it holds PII)"""

    try:
        bucket, key = parse_s3_path(s3_path)
        s3.delete_object(Bucket=bucket, Key=key)
        print(f'Deleted CSV file: {s3_path}')
    except Exception as e:
        print(f'Error deleting CSV file {s3_path}: {str(e)}')


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
