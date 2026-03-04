"""
Email Sender Lambda
Triggered by SQS to send individual emails via SES
Rate-limited by Lambda reserved concurrency
"""

import json
import os
import boto3
from botocore.config import Config
from datetime import datetime

# Import unsubscribe helper
try:
    from unsubscribe_helper import generate_list_unsubscribe_headers, get_env_config
    UNSUBSCRIBE_AVAILABLE = True
except ImportError:
    UNSUBSCRIBE_AVAILABLE = False
    print('Warning: unsubscribe_helper not available')

dynamodb = boto3.resource('dynamodb')

# Disable boto3 retries - let SQS handle all retries
ses_config = Config(
    retries={
        'max_attempts': 0,  # No automatic retries
        'mode': 'standard'
    }
)
ses = boto3.client('ses', config=ses_config)
sqs = boto3.client('sqs')
sns = boto3.client('sns')

CAMPAIGN_TABLE_NAME = os.environ['CAMPAIGN_TABLE_NAME']
NOTIFICATION_TOPIC_ARN = os.environ.get('NOTIFICATION_TOPIC_ARN', '')

campaign_table = dynamodb.Table(CAMPAIGN_TABLE_NAME)


def handler(event, context):
    """
    Process SQS batch (up to 20 messages)
    Manually handle message deletion, requeuing, and DLQ
    """
    
    for record in event['Records']:
        receipt_handle = record['receiptHandle']
        
        try:
            message = json.loads(record['body'])
            
            # Build tags list if present
            tags = []
            if message.get('tags'):
                tags = [{'Name': k, 'Value': str(v)} for k, v in message['tags'].items()]
            
            # Generate List-Unsubscribe headers if enabled
            email_headers = None
            
            # DEBUG: Log unsubscribe settings from message
            print(f"DEBUG unsubscribe_enabled: {message.get('unsubscribe_enabled')}")
            print(f"DEBUG unsubscribe_type: {message.get('unsubscribe_type')}")
            print(f"DEBUG UNSUBSCRIBE_AVAILABLE: {UNSUBSCRIBE_AVAILABLE}")
            
            if message.get('unsubscribe_enabled') and UNSUBSCRIBE_AVAILABLE:
                unsubscribe_type = message.get('unsubscribe_type', 'both')
                print(f"DEBUG checking if unsubscribe_type '{unsubscribe_type}' in ['headers', 'both']")
                if unsubscribe_type in ['headers', 'both']:
                    try:
                        config = get_env_config()
                        print(f"DEBUG generating headers with config: endpoint={config.get('endpoint_url')}, mailto={config.get('mailto')}")
                        email_headers = generate_list_unsubscribe_headers(
                            message['to_address'],
                            config['encryption_key'],
                            config['endpoint_url'],
                            config['mailto'],
                            message.get('unsubscribe_topic')
                        )
                        print(f"DEBUG email_headers result: {email_headers}")
                    except Exception as e:
                        print(f'Warning: Failed to generate unsubscribe headers: {str(e)}')
            
            # Use SES v2 API when we have headers (supports templates + headers)
            # Otherwise use v1 API for backwards compatibility
            ses_client_to_use = ses if not email_headers else boto3.client('sesv2', config=ses_config)
            
            # Send email via SES (no retries - SQS handles that)
            if email_headers:
                # SES v2 API supports templates WITH custom headers
                # Headers go at top level, not inside Content
                ses_params = {
                    'FromEmailAddress': message['from_email'],
                    'Destination': {'ToAddresses': [message['to_address']]},
                    'Content': {
                        'Template': {
                            'TemplateName': message['template_name'],
                            'TemplateData': json.dumps(message.get('template_data', {})),
                            'Headers': [
                                {'Name': name, 'Value': value}
                                for name, value in email_headers.items()
                            ]
                        }
                    },
                    'EmailTags': []  # Will be populated below if tags exist
                }
            else:
                # Standard templated email (SES v1 API)
                ses_params = {
                    'Source': message['from_email'],
                    'Destination': {'ToAddresses': [message['to_address']]},
                    'Template': message['template_name'],
                    'TemplateData': json.dumps(message.get('template_data', {}))
                }
            
            # Add optional parameters (handle both v1 and v2 API formats)
            if message.get('configuration_set'):
                if email_headers:
                    # SES v2 API format
                    ses_params['ConfigurationSetName'] = message['configuration_set']
                else:
                    # SES v1 API format
                    ses_params['ConfigurationSetName'] = message['configuration_set']
            
            if tags:
                if email_headers:
                    # SES v2 API format
                    ses_params['EmailTags'] = [{'Name': t['Name'], 'Value': t['Value']} for t in tags]
                else:
                    # SES v1 API format
                    ses_params['Tags'] = tags
            
            # DEBUG: Log what we're sending to SES
            print(f"DEBUG template_data: {json.dumps(message.get('template_data', {}))}")
            if email_headers:
                print(f"DEBUG using SES v2 API with headers: {list(email_headers.keys())}")
            
            # Send using appropriate API
            if email_headers:
                response = ses_client_to_use.send_email(**ses_params)
            else:
                response = ses_client_to_use.send_templated_email(**ses_params)
            
            # Success - delete message and update progress
            sqs.delete_message(
                QueueUrl=os.environ['EMAIL_QUEUE_URL'],
                ReceiptHandle=receipt_handle
            )
            
            update_campaign_progress(
                message['campaign_id'],
                message['schedule_timestamp'],
                sent=1
            )
            
        except Exception as e:
            # Check if it's a throttling error (works for both v1 and v2 API)
            error_code = getattr(e, 'response', {}).get('Error', {}).get('Code', '')
            is_throttling = error_code in ['Throttling', 'ThrottlingException', 'TooManyRequestsException']
            
            # Check if it's a permanent error
            permanent_errors = ['MessageRejected', 'MailFromDomainNotVerifiedException', 
                              'TemplateDoesNotExist', 'ConfigurationSetDoesNotExist',
                              'AccountSendingPausedException']
            is_permanent = error_code in permanent_errors
            
            if is_throttling:
                # Throttling - manually requeue to SQS
                print(f'Throttled, requeuing: {str(e)}')
                sqs.send_message(
                    QueueUrl=os.environ['EMAIL_QUEUE_URL'],
                    MessageBody=json.dumps(message)
                )
                # Delete original message
                sqs.delete_message(
                    QueueUrl=os.environ['EMAIL_QUEUE_URL'],
                    ReceiptHandle=receipt_handle
                )
            elif is_permanent:
                # Permanent failure - send to DLQ manually
                print(f'Permanent error, sending to DLQ: {str(e)}')
                message['error_message'] = str(e)
                sqs.send_message(
                    QueueUrl=os.environ['DLQ_QUEUE_URL'],
                    MessageBody=json.dumps(message)
                )
                # Delete original message
                sqs.delete_message(
                    QueueUrl=os.environ['EMAIL_QUEUE_URL'],
                    ReceiptHandle=receipt_handle
                )
                
                update_campaign_progress(
                    message['campaign_id'],
                    message['schedule_timestamp'],
                    failed=1
                )
            else:
                # Unknown error - requeue to try again
                print(f'Unknown error, requeuing: {str(e)}')
                sqs.send_message(
                    QueueUrl=os.environ['EMAIL_QUEUE_URL'],
                    MessageBody=json.dumps(message)
                )
                # Delete original message
                sqs.delete_message(
                    QueueUrl=os.environ['EMAIL_QUEUE_URL'],
                    ReceiptHandle=receipt_handle
                )
    
    # Check if campaign is complete (periodically)
    if event['Records']:
        try:
            first_message = json.loads(event['Records'][0]['body'])
            check_campaign_completion(
                first_message['campaign_id'],
                first_message['schedule_timestamp']
            )
        except Exception as e:
            print(f'Error checking campaign completion: {str(e)}')
    
    print(f"✅ Processed {len(event['Records'])} messages")


def update_campaign_progress(campaign_id, schedule_timestamp, sent=0, failed=0):
    """Update campaign progress counters in DynamoDB"""
    
    try:
        update_expression = []
        expression_values = {}
        
        if sent > 0:
            update_expression.append('sent_count = sent_count + :sent')
            expression_values[':sent'] = sent
        
        if failed > 0:
            update_expression.append('failed_count = failed_count + :failed')
            expression_values[':failed'] = failed
        
        if not update_expression:
            return
        
        campaign_table.update_item(
            Key={
                'campaign_id': campaign_id,
                'schedule_timestamp': schedule_timestamp
            },
            UpdateExpression='SET ' + ', '.join(update_expression),
            ExpressionAttributeValues=expression_values
        )
        
    except Exception as e:
        print(f'Error updating campaign progress: {str(e)}')


def check_campaign_completion(campaign_id, schedule_timestamp):
    """Check if campaign is complete and update status"""
    
    try:
        # Get campaign
        response = campaign_table.query(
            KeyConditionExpression='campaign_id = :cid',
            ExpressionAttributeValues={':cid': campaign_id},
            Limit=1
        )
        
        if not response.get('Items'):
            return
        
        campaign = response['Items'][0]
        
        # Check if all emails have been processed
        total = int(campaign.get('total_recipients', 0))
        sent = int(campaign.get('sent_count', 0))
        failed = int(campaign.get('failed_count', 0))
        processed = sent + failed
        
        # If complete, update status
        if processed >= total and campaign['status'] == 'PROCESSING':
            campaign_table.update_item(
                Key={
                    'campaign_id': campaign_id,
                    'schedule_timestamp': schedule_timestamp
                },
                UpdateExpression='SET #status = :status, completed_at = :completed_at',
                ExpressionAttributeNames={'#status': 'status'},
                ExpressionAttributeValues={
                    ':status': 'COMPLETED',
                    ':completed_at': int(datetime.now().timestamp())
                }
            )
            
            print(f'Campaign {campaign_id} completed: {sent} sent, {failed} failed')
            
            # Send completion notification
            if NOTIFICATION_TOPIC_ARN:
                send_notification(
                    f'Campaign Completed: {campaign["campaign_name"]}',
                    f'Campaign {campaign_id} has completed.\n\n'
                    f'Total Recipients: {total}\n'
                    f'Successfully Sent: {sent}\n'
                    f'Failed: {failed}\n'
                    f'Success Rate: {round((sent/total*100) if total > 0 else 0, 2)}%'
                )
        
    except Exception as e:
        print(f'Error checking campaign completion: {str(e)}')


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
