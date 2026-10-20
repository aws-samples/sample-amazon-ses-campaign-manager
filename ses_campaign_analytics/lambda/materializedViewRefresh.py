import json
import boto3
import os
import time
from datetime import datetime, timedelta
from typing import Dict, Any, List

from botocore.exceptions import ClientError

athena = boto3.client('athena')
sns = boto3.client('sns')
s3 = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')

DATABASE_NAME = os.environ['DATABASE_NAME']
RAW_EVENTS_TABLE = os.environ['RAW_EVENTS_TABLE']
CAMPAIGN_METRICS_TABLE = os.environ['CAMPAIGN_METRICS_TABLE']
PROCESSED_BUCKET = os.environ['PROCESSED_BUCKET']
ATHENA_OUTPUT_LOCATION = os.environ['ATHENA_OUTPUT_LOCATION']
WORKGROUP_NAME = os.environ['WORKGROUP_NAME']
NOTIFICATION_TOPIC_ARN = os.environ.get('NOTIFICATION_TOPIC_ARN', '')
METADATA_TABLE_NAME = os.environ['METADATA_TABLE_NAME']
REFRESH_LOOKBACK_DAYS = int(os.environ.get('REFRESH_LOOKBACK_DAYS', '14'))

# Athena INSERT INTO can write at most 100 partitions per query
MAX_LOOKBACK_DAYS = 90

METRICS_PREFIX = 'materialized-views/campaign_metrics_daily'

# Lock item in the campaign metadata table so overlapping refreshes don't double-count
LOCK_ID = '__materialized_view_refresh_lock__'


class RefreshInProgressError(Exception):
    """Another refresh holds the lock - raised so Lambda retries this invocation later"""


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda handler to refresh materialized views for SES campaign analytics.

    Events are attributed to the day the email was SENT (mail.timestamp), not the day
    the event arrived, so opens/clicks/bounces that arrive days later still count
    toward the right campaign and each email is counted once. Every run recomputes
    the last REFRESH_LOOKBACK_DAYS send dates, which also backfills any failed run.

    Optional event fields:
        date: Last send date to recompute (YYYY-MM-DD, default today UTC)
        lookback_days: Number of send dates to recompute (default REFRESH_LOOKBACK_DAYS, max 90)
    """
    print(f"Starting materialized view refresh at {datetime.utcnow().isoformat()}")

    try:
        end_date = parse_date(event.get('date')) if event.get('date') else datetime.utcnow().date()
        lookback_days = int(event.get('lookback_days') or REFRESH_LOOKBACK_DAYS)
        if not 1 <= lookback_days <= MAX_LOOKBACK_DAYS:
            raise ValueError(f'lookback_days must be between 1 and {MAX_LOOKBACK_DAYS}')
    except (TypeError, ValueError) as e:
        # Bad input won't fix itself on retry
        print(f"Invalid refresh request: {str(e)}")
        return {'statusCode': 400, 'body': json.dumps({'error': str(e)})}

    start_date = end_date - timedelta(days=lookback_days - 1)
    dates = [(start_date + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(lookback_days)]

    if not acquire_lock(context):
        raise RefreshInProgressError('Another materialized view refresh is running, will retry')

    try:
        print(f"Recomputing send dates {dates[0]} to {dates[-1]}")

        # Replace the window's partitions: insert new data first, then delete the old
        # files, so a failed query leaves the previous results in place
        old_keys = list_partition_objects(dates)
        insert_campaign_metrics(dates[0], dates[-1])
        delete_objects(old_keys)

        send_notification(
            subject='SES Analytics: Materialized View Refresh Successful',
            message=f'Successfully refreshed campaign metrics for send dates {dates[0]} to {dates[-1]}\n\n'
                   f'Database: {DATABASE_NAME}\n'
                   f'Table: {CAMPAIGN_METRICS_TABLE}\n'
                   f'Timestamp: {datetime.utcnow().isoformat()}'
        )

        print(f"Materialized view refresh completed successfully for {dates[0]} to {dates[-1]}")

        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Materialized view refresh completed successfully',
                'start_date': dates[0],
                'end_date': dates[-1],
                'timestamp': datetime.utcnow().isoformat()
            })
        }

    except Exception as e:
        error_message = f"Error refreshing materialized views: {str(e)}"
        print(error_message)

        # Send failure notification
        send_notification(
            subject='SES Analytics: Materialized View Refresh FAILED',
            message=f'Failed to refresh campaign metrics for send dates {dates[0]} to {dates[-1]}\n\n'
                   f'Error: {str(e)}\n'
                   f'Timestamp: {datetime.utcnow().isoformat()}'
        )

        # Raise so the failure shows in Lambda error metrics and async invocations are retried
        raise

    finally:
        release_lock()


def parse_date(value: str):
    """Parse a YYYY-MM-DD date (also guards the value interpolated into SQL)"""
    return datetime.strptime(value, '%Y-%m-%d').date()


def acquire_lock(context: Any) -> bool:
    """Take the refresh lock, or return False if an unexpired lock exists"""
    now = int(time.time())
    expires_at = now + context.get_remaining_time_in_millis() // 1000 + 60

    try:
        dynamodb.Table(METADATA_TABLE_NAME).put_item(
            Item={'campaign_id': LOCK_ID, 'expires_at': expires_at, 'ttl': expires_at},
            ConditionExpression='attribute_not_exists(campaign_id) OR expires_at < :now',
            ExpressionAttributeValues={':now': now}
        )
        return True
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            return False
        raise


def release_lock() -> None:
    """Release the refresh lock (a crashed run's lock expires on its own)"""
    try:
        dynamodb.Table(METADATA_TABLE_NAME).delete_item(Key={'campaign_id': LOCK_ID})
    except Exception as e:
        print(f"Could not release refresh lock (it will expire): {str(e)}")


def list_partition_objects(dates: List[str]) -> List[str]:
    """List the existing S3 objects for the given date partitions"""
    keys = []
    paginator = s3.get_paginator('list_objects_v2')

    for date in dates:
        for page in paginator.paginate(Bucket=PROCESSED_BUCKET, Prefix=f"{METRICS_PREFIX}/date={date}/"):
            keys.extend(obj['Key'] for obj in page.get('Contents', []))

    print(f"Found {len(keys)} existing objects to replace")
    return keys


def delete_objects(keys: List[str]) -> None:
    """Delete S3 objects in batches of 1000"""
    for i in range(0, len(keys), 1000):
        response = s3.delete_objects(
            Bucket=PROCESSED_BUCKET,
            Delete={'Objects': [{'Key': key} for key in keys[i:i + 1000]], 'Quiet': True}
        )
        if response.get('Errors'):
            raise RuntimeError(f"Failed to delete {len(response['Errors'])} old metric files: {response['Errors'][0]}")

    print(f"Deleted {len(keys)} old objects")


def insert_campaign_metrics(start_date: str, end_date: str) -> None:
    """
    Insert aggregated campaign metrics per campaign and send date.
    """
    print(f"Inserting campaign metrics for send dates {start_date} to {end_date}")

    # nosec B608 - dates are validated by parse_date, other values come from environment variables
    query = f"""
    INSERT INTO {DATABASE_NAME}.{CAMPAIGN_METRICS_TABLE}
    SELECT
        campaign_id,
        campaign_name,

        -- Volume Metrics (each email counted once - it belongs to exactly one send date)
        COUNT(DISTINCT CASE WHEN eventType = 'Send' THEN message_id END) as emails_sent,
        COUNT(DISTINCT CASE WHEN eventType = 'Delivery' THEN message_id END) as emails_delivered,
        COUNT(DISTINCT CASE WHEN eventType = 'Open' THEN message_id END) as emails_opened,
        COUNT(DISTINCT CASE WHEN eventType = 'Click' THEN message_id END) as emails_clicked,
        COUNT(DISTINCT CASE WHEN eventType = 'Bounce' AND bounce_type = 'Permanent' THEN message_id END) as hard_bounces,
        COUNT(DISTINCT CASE WHEN eventType = 'Bounce' AND bounce_type = 'Transient' THEN message_id END) as soft_bounces,
        COUNT(DISTINCT CASE WHEN eventType = 'Complaint' THEN message_id END) as complaints,
        COUNT(DISTINCT CASE WHEN eventType = 'Reject' THEN message_id END) as rejects,
        COUNT(DISTINCT CASE WHEN eventType = 'Rendering Failure' THEN message_id END) as rendering_failures,

        -- Calculated Rates
        CAST(COUNT(DISTINCT CASE WHEN eventType = 'Delivery' THEN message_id END) AS DOUBLE) /
            NULLIF(COUNT(DISTINCT CASE WHEN eventType = 'Send' THEN message_id END), 0) * 100 as delivery_rate,

        CAST(COUNT(DISTINCT CASE WHEN eventType = 'Open' THEN message_id END) AS DOUBLE) /
            NULLIF(COUNT(DISTINCT CASE WHEN eventType = 'Delivery' THEN message_id END), 0) * 100 as open_rate,

        CAST(COUNT(DISTINCT CASE WHEN eventType = 'Click' THEN message_id END) AS DOUBLE) /
            NULLIF(COUNT(DISTINCT CASE WHEN eventType = 'Delivery' THEN message_id END), 0) * 100 as click_rate,

        CAST(COUNT(DISTINCT CASE WHEN eventType = 'Bounce' AND bounce_type = 'Permanent' THEN message_id END) AS DOUBLE) /
            NULLIF(COUNT(DISTINCT CASE WHEN eventType = 'Send' THEN message_id END), 0) * 100 as hard_bounce_rate,

        CAST(COUNT(DISTINCT CASE WHEN eventType = 'Complaint' THEN message_id END) AS DOUBLE) /
            NULLIF(COUNT(DISTINCT CASE WHEN eventType = 'Delivery' THEN message_id END), 0) * 100 as complaint_rate,

        CAST(COUNT(DISTINCT CASE WHEN eventType = 'Rendering Failure' THEN message_id END) AS DOUBLE) /
            NULLIF(COUNT(DISTINCT CASE WHEN eventType = 'Send' THEN message_id END), 0) * 100 as rendering_failure_rate,

        -- Unique Recipients
        COUNT(DISTINCT CASE WHEN eventType = 'Send' THEN recipient END) as unique_recipients,

        -- Timing Metrics
        AVG(CASE WHEN eventType = 'Delivery' THEN processing_time_ms END) as avg_delivery_time_ms,

        -- Additional Context
        arbitrary(source) as from_address,
        arbitrary(subject) as sample_subject,

        -- Partition column: the day the emails were sent
        send_date as date

    FROM (
        SELECT
            COALESCE(element_at(mail.tags.campaign_id, 1), 'no-campaign-id') as campaign_id,
            COALESCE(element_at(mail.tags.campaign_name, 1), 'no-campaign-name') as campaign_name,
            CAST(DATE(try(from_iso8601_timestamp(mail.timestamp))) AS VARCHAR) as send_date,
            eventType,
            mail.messageId as message_id,
            element_at(mail.destination, 1) as recipient,
            mail.source as source,
            mail.commonHeaders.subject as subject,
            bounce.bounceType as bounce_type,
            delivery.processingTimeMillis as processing_time_ms
        FROM {DATABASE_NAME}.{RAW_EVENTS_TABLE}
        -- Events can't arrive before the email was sent, so this prunes older partitions
        WHERE ingest_timestamp >= TIMESTAMP '{start_date} 00:00:00'
    ) events
    WHERE send_date BETWEEN '{start_date}' AND '{end_date}'
    GROUP BY campaign_id, campaign_name, send_date
    HAVING COUNT(DISTINCT CASE WHEN eventType = 'Send' THEN message_id END) > 0
    """

    query_execution_id = execute_athena_query(query)
    wait_for_query_completion(query_execution_id)

    print(f"Successfully inserted campaign metrics for send dates {start_date} to {end_date}")


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
