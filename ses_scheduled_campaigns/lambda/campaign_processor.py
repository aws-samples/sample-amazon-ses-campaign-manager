"""
Campaign Processor Lambda
Triggered by EventBridge at scheduled time to process campaign
Reads CSV from S3 and enqueues emails to SQS
"""

import json
import os
import boto3
import csv
from io import StringIO
from datetime import datetime
from decimal import Decimal

# Import unsubscribe helper
try:
    from unsubscribe_helper import add_unsubscribe_to_template_data, generate_list_unsubscribe_headers, get_env_config
    UNSUBSCRIBE_AVAILABLE = True
except ImportError:
    UNSUBSCRIBE_AVAILABLE = False
    print('Warning: unsubscribe_helper not available')

dynamodb = boto3.resource('dynamodb')
s3 = boto3.client('s3')
sqs = boto3.client('sqs')
events = boto3.client('events')
lambda_client = boto3.client('lambda')
sns = boto3.client('sns')

CAMPAIGN_TABLE_NAME = os.environ['CAMPAIGN_TABLE_NAME']
CAMPAIGN_BUCKET_NAME = os.environ['CAMPAIGN_BUCKET_NAME']
EMAIL_QUEUE_URL = os.environ['EMAIL_QUEUE_URL']
NOTIFICATION_TOPIC_ARN = os.environ.get('NOTIFICATION_TOPIC_ARN', '')

campaign_table = dynamodb.Table(CAMPAIGN_TABLE_NAME)

# SQS batch size limit
SQS_BATCH_SIZE = 10


def handler(event, context):
    """
    Process scheduled campaign
    1. Update campaign status to PROCESSING
    2. Read CSV from S3
    3. Parse recipients
    4. Update Lambda concurrency for rate limiting
    5. Enqueue messages to SQS
    6. Clean up EventBridge rule
    """
    
    try:
        campaign_id = event.get('campaign_id')
        
        if not campaign_id:
            raise ValueError('campaign_id not provided in event')
        
        print(f'Processing campaign: {campaign_id}')
        
        # Get campaign from DynamoDB
        response = campaign_table.query(
            KeyConditionExpression='campaign_id = :cid',
            ExpressionAttributeValues={':cid': campaign_id},
            Limit=1
        )
        
        if not response.get('Items'):
            raise ValueError(f'Campaign not found: {campaign_id}')
        
        campaign = response['Items'][0]
        
        # Check if campaign can be processed
        if campaign['status'] != 'SCHEDULED':
            print(f'Campaign {campaign_id} has status {campaign["status"]}, skipping')
            return {'statusCode': 200, 'message': 'Campaign already processed or cancelled'}
        
        # Update status to PROCESSING
        update_campaign_status(campaign_id, campaign['schedule_timestamp'], 'PROCESSING')
        
        # Parse CSV and enqueue emails
        recipients = read_csv_from_s3(campaign['csv_s3_path'])
        
        if not recipients:
            raise ValueError('No recipients found in CSV')
        
        print(f'Found {len(recipients)} recipients')
        
        # Enqueue messages in batches
        total_enqueued = enqueue_emails(campaign_id, campaign, recipients)
        
        print(f'Enqueued {total_enqueued} messages to SQS')
        
        # Clean up EventBridge rule
        cleanup_eventbridge_rule(campaign_id)
        
        # Send notification
        if NOTIFICATION_TOPIC_ARN:
            send_notification(
                f'Campaign Started: {campaign["campaign_name"]}',
                f'Campaign {campaign_id} has started processing {len(recipients)} emails.'
            )
        
        return {
            'statusCode': 200,
            'message': f'Campaign {campaign_id} processing started',
            'recipients_count': len(recipients),
            'messages_enqueued': total_enqueued
        }
        
    except Exception as e:
        error_msg = f'Error processing campaign: {str(e)}'
        print(error_msg)
        
        # Update campaign with error
        if 'campaign_id' in event and 'campaign' in locals():
            update_campaign_with_error(
                event['campaign_id'],
                campaign.get('schedule_timestamp', 0),
                error_msg
            )
        
        # Send error notification
        if NOTIFICATION_TOPIC_ARN:
            send_notification(
                f'Campaign Failed: {event.get("campaign_id", "Unknown")}',
                f'Error: {error_msg}'
            )
        
        raise


def read_csv_from_s3(s3_path):
    """Read and parse CSV file from S3"""
    
    # Parse S3 path (format: s3://bucket/key)
    parts = s3_path.replace('s3://', '').split('/', 1)
    bucket = parts[0]
    key = parts[1]
    
    # Read CSV from S3
    response = s3.get_object(Bucket=bucket, Key=key)
    csv_content = response['Body'].read().decode('utf-8')
    
    # Parse CSV
    recipients = []
    csv_reader = csv.DictReader(StringIO(csv_content))
    
    # Validate required column
    if 'To_Address' not in csv_reader.fieldnames:
        raise ValueError("CSV must contain 'To_Address' column")
    
    for row_num, row in enumerate(csv_reader, start=2):
        to_address = row.get('To_Address', '').strip()
        
        if not to_address:
            print(f'Skipping row {row_num}: missing To_Address')
            continue
        
        # Extract substitution variables (columns starting with 'sub_')
        substitutions = {}
        for key, value in row.items():
            if key.startswith('sub_'):
                sub_key = key[4:]  # Remove 'sub_' prefix
                substitutions[sub_key] = value
        
        recipients.append({
            'to_address': to_address,
            'substitutions': substitutions,
            'row_number': row_num
        })
    
    return recipients


def decimal_to_number(obj):
    """Convert Decimal objects to int or float for JSON serialization"""
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    elif isinstance(obj, dict):
        return {k: decimal_to_number(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [decimal_to_number(item) for item in obj]
    return obj


def enqueue_emails(campaign_id, campaign, recipients):
    """Enqueue individual email messages to SQS in batches"""
    
    total_enqueued = 0
    batch = []
    
    # Get unsubscribe settings from campaign
    unsubscribe_enabled = campaign.get('unsubscribe_enabled', False)
    unsubscribe_type = campaign.get('unsubscribe_type', 'both')
    unsubscribe_topic = campaign.get('unsubscribe_topic')
    
    for idx, recipient in enumerate(recipients):
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
                print(f'Warning: Failed to add unsubscribe link for {recipient["to_address"]}: {str(e)}')
        
        # Each SQS message contains ONE email (Lambda will process 20 at once)
        # Convert Decimal values from DynamoDB to JSON-serializable types
        message = {
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
        
        batch.append({
            'Id': str(idx % SQS_BATCH_SIZE),  # ID within batch
            'MessageBody': json.dumps(message)
        })
        
        # Send batch when it reaches SQS limit (10 messages per batch send)
        if len(batch) >= SQS_BATCH_SIZE:
            send_batch_to_sqs(batch)
            total_enqueued += len(batch)
            batch = []
    
    # Send remaining messages
    if batch:
        send_batch_to_sqs(batch)
        total_enqueued += len(batch)
    
    return total_enqueued


def send_batch_to_sqs(batch):
    """Send a batch of messages to SQS"""
    
    try:
        response = sqs.send_message_batch(
            QueueUrl=EMAIL_QUEUE_URL,
            Entries=batch
        )
        
        # Check for failures
        if response.get('Failed'):
            for failure in response['Failed']:
                print(f'Failed to enqueue message {failure["Id"]}: {failure["Message"]}')
        
    except Exception as e:
        print(f'Error sending batch to SQS: {str(e)}')
        raise


def update_campaign_status(campaign_id, schedule_timestamp, status):
    """Update campaign status in DynamoDB"""
    
    update_expression = 'SET #status = :status'
    expression_values = {':status': status}
    expression_names = {'#status': 'status'}
    
    if status == 'PROCESSING':
        update_expression += ', started_at = :started_at'
        expression_values[':started_at'] = int(datetime.now().timestamp())
    elif status == 'COMPLETED':
        update_expression += ', completed_at = :completed_at'
        expression_values[':completed_at'] = int(datetime.now().timestamp())
    
    campaign_table.update_item(
        Key={
            'campaign_id': campaign_id,
            'schedule_timestamp': schedule_timestamp
        },
        UpdateExpression=update_expression,
        ExpressionAttributeNames=expression_names,
        ExpressionAttributeValues=expression_values
    )


def update_campaign_with_error(campaign_id, schedule_timestamp, error_message):
    """Update campaign with error status"""
    
    try:
        campaign_table.update_item(
            Key={
                'campaign_id': campaign_id,
                'schedule_timestamp': schedule_timestamp
            },
            UpdateExpression='SET #status = :status, error_message = :error',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':status': 'FAILED',
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


def send_notification(subject, message):
    """Send SNS notification"""
    
    try:
        sns.publish(
            TopicArn=NOTIFICATION_TOPIC_ARN,
            Subject=subject,
            Message=message
        )
    except Exception as e:
        print(f'Failed to send notification: {str(e)}')
