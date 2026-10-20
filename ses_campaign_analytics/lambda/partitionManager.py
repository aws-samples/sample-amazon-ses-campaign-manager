"""
Lambda function to automatically add Glue partitions when new data arrives in S3.
Triggered by S3 events when Firehose writes Parquet files.

Uses the Glue API directly (rather than fire-and-forget Athena DDL) so a failure
raises, shows up in Lambda error metrics, and is retried by the S3 async invoke.
"""
import copy
import boto3
import logging
import os
from urllib.parse import unquote_plus

logger = logging.getLogger()
logger.setLevel(logging.INFO)

glue = boto3.client('glue')

DATABASE_NAME = os.environ['DATABASE_NAME']
TABLE_NAME = os.environ['TABLE_NAME']

# Partitions this container has already registered (one Firehose hour = many files)
_known_partitions = set()
_table_storage_descriptor = None


def handler(event, context):
    """
    Process S3 events and create Glue partitions for new data.

    Args:
        event: S3 event notification
        context: Lambda context object
    """
    partitions = {}

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

        # Create partition timestamp
        partition_timestamp = f"{year}-{month}-{day} {hour}:00:00"

        if partition_timestamp in _known_partitions:
            continue

        s3_bucket = 's3://' + record['s3']['bucket']['name']
        partitions[partition_timestamp] = f"{s3_bucket}/events/{parts[1]}/{parts[2]}/{parts[3]}/{parts[4]}/"

    if partitions:
        create_partitions(partitions)


def create_partitions(partitions):
    """
    Register partitions in Glue, ignoring ones that already exist.

    Args:
        partitions: Dict of partition timestamp -> S3 location
    """
    storage_descriptor = get_table_storage_descriptor()

    partition_inputs = []
    for partition_timestamp, location in partitions.items():
        partition_descriptor = copy.deepcopy(storage_descriptor)
        partition_descriptor['Location'] = location
        partition_inputs.append({'Values': [partition_timestamp], 'StorageDescriptor': partition_descriptor})
        logger.info(f"Adding partition: {partition_timestamp} at {location}")

    response = glue.batch_create_partition(
        DatabaseName=DATABASE_NAME,
        TableName=TABLE_NAME,
        PartitionInputList=partition_inputs
    )

    failed = {}
    for error in response.get('Errors', []):
        partition_timestamp = error['PartitionValues'][0]
        if error['ErrorDetail']['ErrorCode'] == 'AlreadyExistsException':
            continue
        failed[partition_timestamp] = error['ErrorDetail'].get('ErrorMessage', error['ErrorDetail']['ErrorCode'])

    for partition_timestamp in partitions:
        if partition_timestamp not in failed:
            _known_partitions.add(partition_timestamp)

    if failed:
        # Raise so the invocation is retried and the failure is visible
        raise RuntimeError(f"Failed to create partitions: {failed}")


def get_table_storage_descriptor():
    """Storage descriptor (columns, SerDe) of the raw events table, cached per container"""
    global _table_storage_descriptor

    if _table_storage_descriptor is None:
        table = glue.get_table(DatabaseName=DATABASE_NAME, Name=TABLE_NAME)['Table']
        descriptor = table['StorageDescriptor']
        _table_storage_descriptor = {
            key: descriptor[key]
            for key in ('Columns', 'InputFormat', 'OutputFormat', 'SerdeInfo', 'Compressed', 'Parameters')
            if key in descriptor
        }

    return _table_storage_descriptor
