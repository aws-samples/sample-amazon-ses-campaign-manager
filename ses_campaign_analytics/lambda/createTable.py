"""
Lambda function to create the Athena table and views via Custom Resource.
Triggered during CloudFormation stack deployment to execute named queries.
"""
import boto3
import cfnresponse
import os
import logging
import time

logger = logging.getLogger()
logger.setLevel(logging.INFO)

athena = boto3.client('athena')
glue = boto3.client('glue')

# Polling configuration
INITIAL_POLL_INTERVAL_SECONDS = 2
MAX_POLL_INTERVAL_SECONDS = 10

# Stop polling with this much time left so CloudFormation always gets a response
# (otherwise the stack waits an hour for the custom resource to time out)
RESPONSE_BUFFER_MS = 30000


def wait_for_query_completion(execution_id, context):
    """
    Poll Athena until query completes or the Lambda is about to time out.
    Uses exponential backoff to reduce API calls.

    Returns:
        tuple: (success: bool, state: str, error_message: str or None)
    """
    poll_interval = INITIAL_POLL_INTERVAL_SECONDS
    attempt = 0

    while context.get_remaining_time_in_millis() > RESPONSE_BUFFER_MS + poll_interval * 1000:
        attempt += 1
        try:
            response = athena.get_query_execution(QueryExecutionId=execution_id)
            state = response['QueryExecution']['Status']['State']

            logger.info(f"Query state (attempt {attempt}): {state}")

            if state == 'SUCCEEDED':
                return True, state, None
            elif state in ('FAILED', 'CANCELLED'):
                error_reason = response['QueryExecution']['Status'].get(
                    'StateChangeReason',
                    'Unknown error'
                )
                return False, state, error_reason

        except Exception as e:
            logger.warning(f"Error polling query status: {str(e)}")

        # Exponential backoff with cap
        time.sleep(poll_interval)
        poll_interval = min(poll_interval * 1.5, MAX_POLL_INTERVAL_SECONDS)

    return False, 'TIMEOUT', f'Query did not complete before the Lambda timeout ({attempt} polls)'


def table_exists(database, table_name):
    """
    Check if table already exists in Glue catalog.

    Returns:
        bool: True if table exists, False otherwise
    """
    try:
        glue.get_table(DatabaseName=database, Name=table_name)
        logger.info(f"Table {table_name} already exists in database {database}")
        return True
    except glue.exceptions.EntityNotFoundException:
        return False
    except Exception as e:
        logger.warning(f"Error checking table existence: {str(e)}")
        return False


def run_named_query(query_id, database, output_location, context):
    """
    Execute a named query and wait for it.

    Returns:
        tuple: (execution_id, error message or None)
    """
    response = athena.get_named_query(NamedQueryId=query_id)
    query_string = response['NamedQuery']['QueryString']

    logger.info(f"Executing query: {query_string[:200]}...")

    execution = athena.start_query_execution(
        QueryString=query_string,
        QueryExecutionContext={'Database': database},
        ResultConfiguration={'OutputLocation': output_location}
    )

    execution_id = execution['QueryExecutionId']
    logger.info(f"Query execution started: {execution_id}")

    success, state, error_message = wait_for_query_completion(execution_id, context)
    if success:
        return execution_id, None
    return execution_id, f"Query {state}: {error_message}"


def create_table(database, output_location, context):
    """
    Create the raw events table from its named query unless it already exists.

    Returns:
        Error message, or None on success
    """
    query_id = os.environ['NAMED_QUERY_ID']
    query_string = athena.get_named_query(NamedQueryId=query_id)['NamedQuery']['QueryString']

    # Extract table name from query for idempotency check
    # Query format: "CREATE EXTERNAL TABLE IF NOT EXISTS table_name ..."
    table_name = None
    if 'CREATE EXTERNAL TABLE' in query_string.upper():
        parts = query_string.upper().split('IF NOT EXISTS')
        if len(parts) > 1:
            table_name = parts[1].split('(')[0].strip().split()[0].lower()

    # Check if table already exists (idempotency)
    if table_name and table_exists(database, table_name):
        logger.info(f"Table {table_name} already exists, skipping creation")
        return None

    execution_id, error = run_named_query(query_id, database, output_location, context)
    if error:
        logger.error(f"{error} (Execution ID: {execution_id})")
        return error

    logger.info(f"Table created successfully. Execution ID: {execution_id}")
    return None


def handler(event, context):
    """
    Custom Resource handler to create the Athena table and views from named queries.

    Args:
        event: CloudFormation custom resource event
        context: Lambda context object
    """
    try:
        logger.info(f"Received event: {event['RequestType']}")

        if event['RequestType'] == 'Create' or event['RequestType'] == 'Update':
            database = os.environ['DATABASE_NAME']
            output_location = os.environ['OUTPUT_LOCATION']

            logger.info(f"Creating table in database: {database}")

            error = create_table(database, output_location, context)
            if error:
                cfnresponse.send(event, context, cfnresponse.FAILED, {'Error': error})
                return

            # Views use CREATE OR REPLACE, so they are re-run on every deploy that changes them
            view_query_ids = [q for q in os.environ.get('VIEW_QUERY_IDS', '').split(',') if q]
            for view_query_id in view_query_ids:
                execution_id, error = run_named_query(view_query_id, database, output_location, context)
                if error:
                    logger.error(f"View creation failed: {error} (Execution ID: {execution_id})")
                    cfnresponse.send(event, context, cfnresponse.FAILED, {'Error': f'View creation failed: {error}'})
                    return

            cfnresponse.send(
                event,
                context,
                cfnresponse.SUCCESS,
                {'Message': f'Table ready, {len(view_query_ids)} views created'}
            )
        else:
            # Delete operation - nothing to do (table cleanup handled by CloudFormation)
            logger.info("Delete operation - no action needed")
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {})

    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}", exc_info=True)
        cfnresponse.send(
            event,
            context,
            cfnresponse.FAILED,
            {'Error': f'Unexpected error: {str(e)}'}
        )
