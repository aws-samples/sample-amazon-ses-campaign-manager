"""
TTL Cleanup Lambda
Triggered by DynamoDB Stream when campaign items expire (TTL)
Cleans up EventBridge rules and S3 CSV files
"""

import json
import os
import boto3
from decimal import Decimal

events = boto3.client('events')
s3 = boto3.client('s3')


def handler(event, context):
    """
    Handle DynamoDB Stream events for expired campaigns
    
    When a campaign's TTL expires (1 hour after execution), this function:
    1. Deletes the EventBridge scheduled rule
    2. Deletes the CSV file from S3
    """
    
    try:
        for record in event.get('Records', []):
            # Only process REMOVE events (both TTL expiration and manual deletion)
            if record['eventName'] != 'REMOVE':
                continue
            
            # Get the old image (deleted item)
            old_image = record['dynamodb'].get('OldImage', {})
            
            if not old_image:
                continue
            
            # Extract campaign data
            campaign_id = old_image.get('campaign_id', {}).get('S', '')
            csv_s3_path = old_image.get('csv_s3_path', {}).get('S', '')
            campaign_name = old_image.get('campaign_name', {}).get('S', 'Unknown')
            
            if not campaign_id:
                print(f"No campaign_id found in deleted item, skipping")
                continue
            
            # Check if this was a TTL deletion or manual deletion
            user_identity = record.get('userIdentity', {})
            deletion_type = 'TTL' if (user_identity.get('type') == 'Service' and 
                                     user_identity.get('principalId') == 'dynamodb.amazonaws.com') else 'Manual'
            
            print(f"Processing {deletion_type} cleanup for campaign: {campaign_id} ({campaign_name})")
            
            # 1. Delete EventBridge rule
            rule_name = f'ses-campaign-{campaign_id}'
            try:
                # Remove targets first
                events.remove_targets(Rule=rule_name, Ids=['1'])
                print(f"Removed targets from rule: {rule_name}")
                
                # Then delete rule
                events.delete_rule(Name=rule_name)
                print(f"Deleted EventBridge rule: {rule_name}")
            except events.exceptions.ResourceNotFoundException:
                print(f"EventBridge rule not found (already deleted or never created): {rule_name}")
            except Exception as eb_error:
                print(f"Error deleting EventBridge rule {rule_name}: {str(eb_error)}")
            
            # 2. Delete CSV file from S3
            if csv_s3_path:
                try:
                    # Parse S3 path (format: s3://bucket-name/key)
                    if csv_s3_path.startswith('s3://'):
                        s3_parts = csv_s3_path[5:].split('/', 1)
                        if len(s3_parts) == 2:
                            bucket_name = s3_parts[0]
                            object_key = s3_parts[1]
                            
                            s3.delete_object(
                                Bucket=bucket_name,
                                Key=object_key
                            )
                            print(f"Deleted CSV file: {csv_s3_path}")
                except Exception as s3_error:
                    print(f"Error deleting CSV file {csv_s3_path}: {str(s3_error)}")
            
            print(f"{deletion_type} cleanup completed for campaign: {campaign_id}")
        
        return {
            'statusCode': 200,
            'body': json.dumps({'message': 'Cleanup completed successfully'})
        }
        
    except Exception as e:
        print(f"Error in TTL cleanup handler: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
