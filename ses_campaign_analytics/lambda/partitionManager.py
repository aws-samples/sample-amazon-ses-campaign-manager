"""
Lambda function to automatically add Athena partitions when new data arrives in S3.
Triggered by S3 events when Firehose writes Parquet files.
"""
import json
import boto3
import logging
import os
from urllib.parse import unquote_plus

logger = logging.getLogger()
logger.setLevel(logging.INFO)

athena = boto3.client('athena')


def handler(event, context):
    """
    Process S3 events and create Athena partitions for new data.
    
    Args:
        event: S3 event notification
        context: Lambda context object
    """
    try:
        for record in event['Records']:
            # URL decode the key (S3 sends URL-encoded keys)
            key = unquote_plus(record['s3']['object']['key'])
            
            logger.info(f"Processing S3 object: {key}")
            
            # Key format: events/year=YYYY/month=MM/day=DD/hour=HH/file.parquet
            if not key.startswith('events/') or not key.endswith('.parquet'):
                logger.info(f"Skipping non-parquet file: {key}")
                continue
            
            parts = key.split('/')
            
            # Validate structure: events/year=.../month=.../day=.../hour=.../file.parquet
            if len(parts) < 6:
                logger.info(f"Skipping file with incorrect structure: {key}")
                continue
            
            # Validate each part has the expected format
            try:
                year = parts[1].split('=')[1]
                month = parts[2].split('=')[1]
                day = parts[3].split('=')[1]
                hour = parts[4].split('=')[1]
            except (IndexError, ValueError) as e:
                logger.warning(f"Could not parse date from key {key}: {e}")
                continue
            
            s3_bucket = 's3://' + record['s3']['bucket']['name']
            partition_location = f"{s3_bucket}/events/{parts[1]}/{parts[2]}/{parts[3]}/{parts[4]}"
            
            # Create partition timestamp
            partition_timestamp = f"{year}-{month}-{day} {hour}:00:00"
            
            # Add partition to Athena table
            query = f"""
            ALTER TABLE {os.environ['TABLE_NAME']} 
            ADD IF NOT EXISTS PARTITION (ingest_timestamp='{partition_timestamp}') 
            LOCATION '{partition_location}'
            """
            
            logger.info(f"Adding partition: {partition_timestamp}")
            logger.info(f"Location: {partition_location}")
            
            response = athena.start_query_execution(
                QueryString=query,
                QueryExecutionContext={'Database': os.environ['DATABASE_NAME']},
                ResultConfiguration={'OutputLocation': os.environ['OUTPUT_LOCATION']}
            )
            
            logger.info(f"Partition query started: {response['QueryExecutionId']}")
            
    except Exception as error:
        logger.error(f"Error processing S3 event: {str(error)}", exc_info=True)
        # Don't raise - we want to continue processing other records
