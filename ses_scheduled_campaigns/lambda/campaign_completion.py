"""
Campaign completion check shared by Campaign Processor and Email Sender

Both call this with the item returned by their own UpdateItem (ReturnValues=ALL_NEW).
DynamoDB serializes updates to an item, so whichever write comes last sees the final
counts - and the conditional update guarantees only one caller marks it complete.
"""

import time

from botocore.exceptions import ClientError


def complete_if_done(campaign_table, campaign, notify):
    """
    Mark a PROCESSING campaign COMPLETED once every recipient was sent or failed

    Args:
        campaign_table: boto3 DynamoDB Table resource
        campaign: Campaign item as returned by an UpdateItem with ReturnValues=ALL_NEW
        notify: Callable(subject, message) used to send the completion notification

    Returns:
        True if this call marked the campaign complete
    """

    if campaign.get('status') != 'PROCESSING':
        return False

    # Items created before enqueue tracking existed have no flag - treat them as fully enqueued
    if not campaign.get('enqueue_complete', True):
        return False

    total = int(campaign.get('total_recipients', 0))
    sent = int(campaign.get('sent_count', 0))
    failed = int(campaign.get('failed_count', 0))

    if sent + failed < total:
        return False

    try:
        campaign_table.update_item(
            Key={
                'campaign_id': campaign['campaign_id'],
                'schedule_timestamp': campaign['schedule_timestamp']
            },
            UpdateExpression='SET #status = :completed, completed_at = :completed_at',
            ConditionExpression='#status = :processing',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':completed': 'COMPLETED',
                ':processing': 'PROCESSING',
                ':completed_at': int(time.time())
            }
        )
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            return False  # Another invocation completed (or stopped) it first
        raise

    campaign_id = campaign['campaign_id']
    print(f'Campaign {campaign_id} completed: {sent} sent, {failed} failed')

    notify(
        f'Campaign Completed: {campaign.get("campaign_name", campaign_id)}',
        f'Campaign {campaign_id} has completed.\n\n'
        f'Total Recipients: {total}\n'
        f'Successfully Sent: {sent}\n'
        f'Failed: {failed}\n'
        f'Success Rate: {round((sent / total * 100) if total > 0 else 0, 2)}%'
        + ('\n\nFailed messages are in the dead-letter queue with the error for each.' if failed else '')
    )
    return True
