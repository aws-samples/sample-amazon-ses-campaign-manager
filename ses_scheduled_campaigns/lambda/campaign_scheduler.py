"""
Campaign Scheduler Lambda
Called by Amazon SES Campaign Manager to schedule a new campaign
"""

import json
import os
import boto3
from datetime import datetime
from decimal import Decimal
import secrets

dynamodb = boto3.resource('dynamodb')
s3 = boto3.client('s3')
events = boto3.client('events')

CAMPAIGN_TABLE_NAME = os.environ['CAMPAIGN_TABLE_NAME']
CAMPAIGN_BUCKET_NAME = os.environ['CAMPAIGN_BUCKET_NAME']
CAMPAIGN_PROCESSOR_ARN = os.environ['CAMPAIGN_PROCESSOR_ARN']

campaign_table = dynamodb.Table(CAMPAIGN_TABLE_NAME)


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
            "tags": {"campaign_id": "123", "campaign_name": "My Campaign"},
            "rate_limit": 10,  # emails per second
            "total_recipients": 1000,
            "template_data": {}  # base template data
        },
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
            return {
                'statusCode': 400,
                'body': json.dumps({'error': f'Unknown action: {action}'})
            }
            
    except Exception as e:
        print(f"Error in campaign_scheduler: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }


def schedule_campaign(campaign_data):
    """Schedule a new campaign"""
    
    # Validate required fields
    required_fields = [
        'campaign_name', 'schedule_timestamp', 'csv_s3_key',
        'template_name', 'from_email', 'total_recipients'
    ]
    
    for field in required_fields:
        if field not in campaign_data:
            return {
                'statusCode': 400,
                'body': json.dumps({'error': f'Missing required field: {field}'})
            }
    
    # Generate campaign ID (same format as bulk emails for consistency)
    # Format: {unix_timestamp}-{4_char_random}
    timestamp = int(datetime.now().timestamp())
    random_suffix = secrets.token_hex(2)  # 2 bytes = 4 hex characters
    campaign_id = f"{timestamp}-{random_suffix}"
    
    schedule_timestamp = campaign_data['schedule_timestamp']
    
    # Verify CSV exists in S3
    try:
        s3.head_object(Bucket=CAMPAIGN_BUCKET_NAME, Key=campaign_data['csv_s3_key'])
    except Exception as e:
        return {
            'statusCode': 400,
            'body': json.dumps({'error': f'CSV file not found in S3: {str(e)}'})
        }
    
    # Create campaign record in DynamoDB
    campaign_item = {
        'campaign_id': campaign_id,
        'schedule_timestamp': schedule_timestamp,
        'campaign_name': campaign_data['campaign_name'],
        'status': 'SCHEDULED',
        'csv_s3_path': f's3://{CAMPAIGN_BUCKET_NAME}/{campaign_data["csv_s3_key"]}',
        'template_name': campaign_data['template_name'],
        'from_email': campaign_data['from_email'],
        'configuration_set': campaign_data.get('configuration_set', ''),
        'tags': campaign_data.get('tags', {}),
        'total_recipients': campaign_data['total_recipients'],
        'sent_count': 0,
        'failed_count': 0,
        'template_data': campaign_data.get('template_data', {}),
        'unsubscribe_enabled': campaign_data.get('unsubscribe_enabled', False),
        'unsubscribe_type': campaign_data.get('unsubscribe_type', 'both'),
        'unsubscribe_topic': campaign_data.get('unsubscribe_topic'),
        'created_at': int(datetime.now().timestamp()),
        'started_at': 0,
        'completed_at': 0,
        'error_message': '',
    }
    
    # Add TTL for 1 hour after scheduled execution
    # DynamoDB stream will trigger cleanup Lambda to remove EventBridge rule and S3 file
    ttl_seconds = 60 * 60  # 1 hour
    campaign_item['ttl'] = schedule_timestamp + ttl_seconds
    
    campaign_table.put_item(Item=campaign_item)
    
    # Create EventBridge rule for the scheduled time
    rule_name = f'ses-campaign-{campaign_id}'
    schedule_date = datetime.fromtimestamp(schedule_timestamp)
    
    # Create one-time scheduled rule using cron expression
    cron_expression = (
        f'cron({schedule_date.minute} {schedule_date.hour} '
        f'{schedule_date.day} {schedule_date.month} ? {schedule_date.year})'
    )
    
    events.put_rule(
        Name=rule_name,
        ScheduleExpression=cron_expression,
        State='ENABLED',
        Description=f'Trigger for campaign: {campaign_data["campaign_name"]}'
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
    
    print(f'Scheduled campaign {campaign_id} for {schedule_date.isoformat()}')
    
    return {
        'statusCode': 200,
        'body': json.dumps({
            'campaign_id': campaign_id,
            'status': 'SCHEDULED',
            'schedule_time': schedule_date.isoformat(),
            'message': 'Campaign scheduled successfully'
        })
    }


def list_campaigns(status_filter=None, limit=50, future_only=False):
    """
    List campaigns efficiently using schedule_timestamp
    
    Args:
        status_filter: Ignored (kept for backwards compatibility)
        limit: Maximum number of campaigns to return
        future_only: If True, only return campaigns scheduled in the future
    """
    
    try:
        current_timestamp = int(datetime.now().timestamp())
        
        if future_only:
            # Efficient GSI query: status='SCHEDULED' AND schedule_timestamp > now
            # Uses status-index GSI with status as PK and schedule_timestamp as SK
            response = campaign_table.query(
                IndexName='status-index',
                KeyConditionExpression='#status = :status AND schedule_timestamp > :now',
                ExpressionAttributeNames={'#status': 'status'},
                ExpressionAttributeValues={
                    ':status': 'SCHEDULED',
                    ':now': current_timestamp
                },
                Limit=limit,
                ScanIndexForward=True  # Ascending (soonest first for future campaigns)
            )
        else:
            # Query all SCHEDULED campaigns using existing GSI
            response = campaign_table.query(
                IndexName='status-index',
                KeyConditionExpression='#status = :status',
                ExpressionAttributeNames={'#status': 'status'},
                ExpressionAttributeValues={':status': 'SCHEDULED'},
                Limit=limit,
                ScanIndexForward=False  # Descending
            )
        
        campaigns = response.get('Items', [])
        
        # Sort by schedule_timestamp (ascending for future, descending for all)
        campaigns.sort(
            key=lambda x: x.get('schedule_timestamp', 0), 
            reverse=not future_only
        )
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'campaigns': campaigns,
                'count': len(campaigns),
                'future_only': future_only
            }, cls=DecimalEncoder)
        }
        
    except Exception as e:
        return {
            'statusCode': 500,
            'body': json.dumps({'error': f'Failed to list campaigns: {str(e)}'})
        }


def cancel_campaign(campaign_id):
    """Cancel a scheduled campaign"""
    
    if not campaign_id:
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 'campaign_id is required'})
        }
    
    try:
        # Get campaign from DynamoDB
        response = campaign_table.query(
            KeyConditionExpression='campaign_id = :cid',
            ExpressionAttributeValues={':cid': campaign_id},
            Limit=1
        )
        
        if not response.get('Items'):
            return {
                'statusCode': 404,
                'body': json.dumps({'error': 'Campaign not found'})
            }
        
        campaign = response['Items'][0]
        
        # Can only cancel if status is SCHEDULED
        if campaign['status'] != 'SCHEDULED':
            return {
                'statusCode': 400,
                'body': json.dumps({
                    'error': f'Cannot cancel campaign with status: {campaign["status"]}'
                })
            }
        
        # Delete EventBridge rule
        rule_name = f'ses-campaign-{campaign_id}'
        
        try:
            # Remove targets first
            events.remove_targets(Rule=rule_name, Ids=['1'])
            # Then delete rule
            events.delete_rule(Name=rule_name)
        except events.exceptions.ResourceNotFoundException:
            print(f'EventBridge rule {rule_name} not found, skipping deletion')
        
        # Update campaign status
        campaign_table.update_item(
            Key={
                'campaign_id': campaign_id,
                'schedule_timestamp': campaign['schedule_timestamp']
            },
            UpdateExpression='SET #status = :status',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={':status': 'CANCELLED'}
        )
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'campaign_id': campaign_id,
                'status': 'CANCELLED',
                'message': 'Campaign cancelled successfully'
            })
        }
        
    except Exception as e:
        return {
            'statusCode': 500,
            'body': json.dumps({'error': f'Failed to cancel campaign: {str(e)}'})
        }


def get_campaign_status(campaign_id):
    """Get status and progress of a campaign"""
    
    if not campaign_id:
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 'campaign_id is required'})
        }
    
    try:
        response = campaign_table.query(
            KeyConditionExpression='campaign_id = :cid',
            ExpressionAttributeValues={':cid': campaign_id},
            Limit=1
        )
        
        if not response.get('Items'):
            return {
                'statusCode': 404,
                'body': json.dumps({'error': 'Campaign not found'})
            }
        
        campaign = response['Items'][0]
        
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
                'schedule_timestamp': campaign.get('schedule_timestamp', 0),
                'started_at': campaign.get('started_at', 0),
                'completed_at': campaign.get('completed_at', 0),
                'error_message': campaign.get('error_message', '')
            }, cls=DecimalEncoder)
        }
        
    except Exception as e:
        return {
            'statusCode': 500,
            'body': json.dumps({'error': f'Failed to get campaign status: {str(e)}'})
        }
