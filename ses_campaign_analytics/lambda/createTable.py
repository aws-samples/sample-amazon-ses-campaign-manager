"""
Lambda function to create Athena table via Custom Resource.
Triggered during CloudFormation stack deployment to execute a named query.
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
MAX_POLL_ATTEMPTS = 240  # 8 minutes max wait (leaving 2 min buffer for 10 min timeout)


def wait_for_query_completion(execution_id):
    """
    Poll Athena until query completes or times out.
    Uses exponential backoff to reduce API calls.
    
    Returns:
        tuple: (success: bool, state: str, error_message: str or None)
    """
    poll_interval = INITIAL_POLL_INTERVAL_SECONDS
    
    for attempt in range(MAX_POLL_ATTEMPTS):
        try:
            response = athena.get_query_execution(QueryExecutionId=execution_id)
            state = response['QueryExecution']['Status']['State']
            
            logger.info(f"Query state (attempt {attempt + 1}/{MAX_POLL_ATTEMPTS}): {state}")
            
            if state == 'SUCCEEDED':
                return True, state, None
            elif state in ('FAILED', 'CANCELLED'):
                error_reason = response['QueryExecution']['Status'].get(
                    'StateChangeReason', 
                    'Unknown error'
                )
                return False, state, error_reason
            
            # Exponential backoff with cap
            time.sleep(poll_interval)
            poll_interval = min(poll_interval * 1.5, MAX_POLL_INTERVAL_SECONDS)
            
        except Exception as e:
            logger.warning(f"Error polling query status: {str(e)}")
            time.sleep(poll_interval)
    
    return False, 'TIMEOUT', f'Query did not complete within {MAX_POLL_ATTEMPTS} attempts'


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


def handler(event, context):
    """
    Custom Resource handler to create Athena table from a named query.
    
    Args:
        event: CloudFormation custom resource event
        context: Lambda context object
    """
    try:
        logger.info(f"Received event: {event['RequestType']}")
        
        if event['RequestType'] == 'Create' or event['RequestType'] == 'Update':
            query_id = os.environ['NAMED_QUERY_ID']
            database = os.environ['DATABASE_NAME']
            output_location = os.environ['OUTPUT_LOCATION']
            
            logger.info(f"Creating table in database: {database}")
            
            # Get the named query
            response = athena.get_named_query(NamedQueryId=query_id)
            query_string = response['NamedQuery']['QueryString']
            
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
                cfnresponse.send(
                    event,
                    context,
                    cfnresponse.SUCCESS,
                    {'Message': f'Table {table_name} already exists', 'Idempotent': 'true'}
                )
                return
            
            logger.info(f"Executing query: {query_string[:200]}...")
            
            # Execute the query
            execution = athena.start_query_execution(
                QueryString=query_string,
                QueryExecutionContext={'Database': database},
                ResultConfiguration={'OutputLocation': output_location}
            )
            
            execution_id = execution['QueryExecutionId']
            logger.info(f"Query execution started: {execution_id}")
            
            # Wait for query to complete
            success, state, error_message = wait_for_query_completion(execution_id)
            
            if success:
                logger.info(f"Table created successfully. Execution ID: {execution_id}")
                cfnresponse.send(
                    event,
                    context,
                    cfnresponse.SUCCESS,
                    {
                        'QueryExecutionId': execution_id,
                        'State': state,
                        'Message': 'Table created successfully'
                    }
                )
            else:
                error_msg = f"Query {state}: {error_message}"
                logger.error(f"{error_msg} (Execution ID: {execution_id})")
                cfnresponse.send(
                    event,
                    context,
                    cfnresponse.FAILED,
                    {
                        'Error': error_msg,
                        'QueryExecutionId': execution_id,
                        'State': state
                    }
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
