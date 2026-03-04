import json
import boto3
import os
import time
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

athena = boto3.client('athena')
sns = boto3.client('sns')
glue = boto3.client('glue')

DATABASE_NAME = os.environ['DATABASE_NAME']
RAW_EVENTS_TABLE = os.environ['RAW_EVENTS_TABLE']
CAMPAIGN_METRICS_TABLE = os.environ['CAMPAIGN_METRICS_TABLE']
PROCESSED_BUCKET = os.environ['PROCESSED_BUCKET']
ATHENA_OUTPUT_LOCATION = os.environ['ATHENA_OUTPUT_LOCATION']
WORKGROUP_NAME = os.environ['WORKGROUP_NAME']
NOTIFICATION_TOPIC_ARN = os.environ.get('NOTIFICATION_TOPIC_ARN', '')


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda handler to refresh materialized views for SES campaign analytics.
    Runs daily to aggregate campaign metrics from raw events.
    """
    print(f"Starting materialized view refresh at {datetime.utcnow().isoformat()}")
    
    try:
        # Calculate date range - use provided date or default to yesterday
        if 'date' in event and event['date']:
            target_date = event['date']
            print(f"Using provided date: {target_date}")
        else:
            target_date = (datetime.utcnow() - timedelta(days=1)).strftime('%Y-%m-%d')
            print(f"Using default date (yesterday): {target_date}")
        
        # Step 1: Drop existing partition if exists
        drop_partition(target_date)
        
        # Step 2: Insert new aggregated data
        insert_campaign_metrics(target_date)
        
        # Step 3: Send success notification
        send_notification(
            subject='SES Analytics: Materialized View Refresh Successful',
            message=f'Successfully refreshed campaign metrics for {target_date}\n\n'
                   f'Database: {DATABASE_NAME}\n'
                   f'Table: {CAMPAIGN_METRICS_TABLE}\n'
                   f'Timestamp: {datetime.utcnow().isoformat()}'
        )
        
        print(f"Materialized view refresh completed successfully for {target_date}")
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Materialized view refresh completed successfully',
                'date': target_date,
                'timestamp': datetime.utcnow().isoformat()
            })
        }
        
    except Exception as e:
        error_message = f"Error refreshing materialized views: {str(e)}"
        print(error_message)
        
        # Send failure notification
        send_notification(
            subject='SES Analytics: Materialized View Refresh FAILED',
            message=f'Failed to refresh campaign metrics\n\n'
                   f'Error: {str(e)}\n'
                   f'Timestamp: {datetime.utcnow().isoformat()}'
        )
        
        return {
            'statusCode': 500,
            'body': json.dumps({
                'message': 'Materialized view refresh failed',
                'error': str(e),
                'timestamp': datetime.utcnow().isoformat()
            })
        }


def drop_partition(date: str) -> None:
    """
    Delete existing partition for the given date to ensure idempotency.
    First attempts to delete the S3 data directly, then repairs the table.
    """
    import boto3
    
    try:
        print(f"Attempting to remove existing data for date={date}")
        
        # Step 1: Delete S3 objects for this partition directly
        s3 = boto3.client('s3')
        bucket = PROCESSED_BUCKET
        prefix = f"materialized-views/campaign_metrics_daily/date={date}/"
        
        print(f"Deleting S3 objects at s3://{bucket}/{prefix}")
        
        # List and delete all objects in the partition
        paginator = s3.get_paginator('list_objects_v2')
        pages = paginator.paginate(Bucket=bucket, Prefix=prefix)
        
        delete_count = 0
        for page in pages:
            if 'Contents' in page:
                objects = [{'Key': obj['Key']} for obj in page['Contents']]
                if objects:
                    s3.delete_objects(
                        Bucket=bucket,
                        Delete={'Objects': objects}
                    )
                    delete_count += len(objects)
        
        print(f"Deleted {delete_count} S3 objects for date={date}")
        
        # Step 2: Run MSCK REPAIR to update Glue catalog (removes partition metadata)
        # This is optional but helps keep metadata in sync
        repair_query = f"""
        MSCK REPAIR TABLE {DATABASE_NAME}.{CAMPAIGN_METRICS_TABLE}
        """
        
        try:
            repair_execution_id = execute_athena_query(repair_query)
            wait_for_query_completion(repair_execution_id)
            print(f"Successfully repaired table metadata")
        except Exception as repair_error:
            print(f"Note: Could not repair table (not critical): {str(repair_error)}")
        
    except Exception as e:
        print(f"Note: Could not remove existing data (may not exist): {str(e)}")
        # This is not a critical error, continue processing


def insert_campaign_metrics(date: str) -> None:
    """
    Insert aggregated campaign metrics for the given date.
    """
    print(f"Inserting campaign metrics for date={date}")
    
    query = f"""
    INSERT INTO {DATABASE_NAME}.{CAMPAIGN_METRICS_TABLE}
    SELECT
        COALESCE(mail.tags.campaign_id[1], 'no-campaign-id') as campaign_id,
        COALESCE(mail.tags.campaign_name[1], 'no-campaign-name') as campaign_name,
        
        -- Volume Metrics
        COUNT(DISTINCT CASE WHEN eventType = 'Send' THEN mail.messageId END) as emails_sent,
        COUNT(DISTINCT CASE WHEN eventType = 'Delivery' THEN mail.messageId END) as emails_delivered,
        COUNT(DISTINCT CASE WHEN eventType = 'Open' THEN mail.messageId END) as emails_opened,
        COUNT(DISTINCT CASE WHEN eventType = 'Click' THEN mail.messageId END) as emails_clicked,
        COUNT(DISTINCT CASE WHEN eventType = 'Bounce' AND bounce.bounceType = 'Permanent' THEN mail.messageId END) as hard_bounces,
        COUNT(DISTINCT CASE WHEN eventType = 'Bounce' AND bounce.bounceType = 'Transient' THEN mail.messageId END) as soft_bounces,
        COUNT(DISTINCT CASE WHEN eventType = 'Complaint' THEN mail.messageId END) as complaints,
        COUNT(DISTINCT CASE WHEN eventType = 'Reject' THEN mail.messageId END) as rejects,
        COUNT(DISTINCT CASE WHEN eventType = 'Rendering Failure' THEN mail.messageId END) as rendering_failures,
        
        -- Calculated Rates
        CAST(COUNT(DISTINCT CASE WHEN eventType = 'Delivery' THEN mail.messageId END) AS DOUBLE) / 
            NULLIF(COUNT(DISTINCT CASE WHEN eventType = 'Send' THEN mail.messageId END), 0) * 100 as delivery_rate,
        
        CAST(COUNT(DISTINCT CASE WHEN eventType = 'Open' THEN mail.messageId END) AS DOUBLE) / 
            NULLIF(COUNT(DISTINCT CASE WHEN eventType = 'Delivery' THEN mail.messageId END), 0) * 100 as open_rate,
        
        CAST(COUNT(DISTINCT CASE WHEN eventType = 'Click' THEN mail.messageId END) AS DOUBLE) / 
            NULLIF(COUNT(DISTINCT CASE WHEN eventType = 'Delivery' THEN mail.messageId END), 0) * 100 as click_rate,
        
        CAST(COUNT(DISTINCT CASE WHEN eventType = 'Bounce' AND bounce.bounceType = 'Permanent' THEN mail.messageId END) AS DOUBLE) / 
            NULLIF(COUNT(DISTINCT CASE WHEN eventType = 'Send' THEN mail.messageId END), 0) * 100 as hard_bounce_rate,
        
        CAST(COUNT(DISTINCT CASE WHEN eventType = 'Complaint' THEN mail.messageId END) AS DOUBLE) / 
            NULLIF(COUNT(DISTINCT CASE WHEN eventType = 'Delivery' THEN mail.messageId END), 0) * 100 as complaint_rate,
        
        CAST(COUNT(DISTINCT CASE WHEN eventType = 'Rendering Failure' THEN mail.messageId END) AS DOUBLE) / 
            NULLIF(COUNT(DISTINCT CASE WHEN eventType = 'Send' THEN mail.messageId END), 0) * 100 as rendering_failure_rate,
        
        -- Unique Recipients
        COUNT(DISTINCT CASE WHEN eventType = 'Send' THEN element_at(mail.destination, 1) END) as unique_recipients,
        
        -- Timing Metrics
        AVG(CASE WHEN eventType = 'Delivery' THEN delivery.processingTimeMillis END) as avg_delivery_time_ms,
        
        -- Additional Context
        arbitrary(mail.source) as from_address,
        arbitrary(mail.commonHeaders.subject) as sample_subject,
        
        -- Partition column
        '{date}' as date
        
    FROM {DATABASE_NAME}.{RAW_EVENTS_TABLE}
    WHERE DATE(ingest_timestamp) = DATE('{date}')
    GROUP BY 
        COALESCE(mail.tags.campaign_id[1], 'no-campaign-id'),
        COALESCE(mail.tags.campaign_name[1], 'no-campaign-name')
    HAVING COUNT(DISTINCT CASE WHEN eventType = 'Send' THEN mail.messageId END) > 0
    """
    
    query_execution_id = execute_athena_query(query)
    wait_for_query_completion(query_execution_id)
    
    print(f"Successfully inserted campaign metrics for date={date}")


def execute_athena_query(query: str) -> str:
    """
    Execute an Athena query and return the query execution ID.
    """
    print(f"Executing Athena query:\n{query[:500]}...")
    
    response = athena.start_query_execution(
        QueryString=query,
        QueryExecutionContext={
            'Database': DATABASE_NAME
        },
        ResultConfiguration={
            'OutputLocation': ATHENA_OUTPUT_LOCATION
        },
        WorkGroup=WORKGROUP_NAME
    )
    
    query_execution_id = response['QueryExecutionId']
    print(f"Query execution started with ID: {query_execution_id}")
    
    return query_execution_id


def wait_for_query_completion(query_execution_id: str, max_wait_seconds: int = 600) -> None:
    """
    Wait for an Athena query to complete.
    """
    print(f"Waiting for query {query_execution_id} to complete...")
    
    start_time = time.time()
    
    while True:
        if time.time() - start_time > max_wait_seconds:
            raise TimeoutError(f"Query {query_execution_id} did not complete within {max_wait_seconds} seconds")
        
        response = athena.get_query_execution(QueryExecutionId=query_execution_id)
        status = response['QueryExecution']['Status']['State']
        
        print(f"Query status: {status}")
        
        if status == 'SUCCEEDED':
            print(f"Query {query_execution_id} completed successfully")
            return
        
        elif status in ['FAILED', 'CANCELLED']:
            reason = response['QueryExecution']['Status'].get('StateChangeReason', 'Unknown')
            raise Exception(f"Query {query_execution_id} {status}: {reason}")
        
        # Query is still running
        time.sleep(5)


def send_notification(subject: str, message: str) -> None:
    """
    Send SNS notification if topic ARN is configured.
    """
    if not NOTIFICATION_TOPIC_ARN:
        print("No notification topic configured, skipping notification")
        return
    
    try:
        sns.publish(
            TopicArn=NOTIFICATION_TOPIC_ARN,
            Subject=subject,
            Message=message
        )
        print(f"Notification sent: {subject}")
    except Exception as e:
        print(f"Failed to send notification: {str(e)}")
        # Don't fail the entire process if notification fails
