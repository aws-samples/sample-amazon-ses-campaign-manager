# SES Scheduled Campaigns

> **Part of the [Amazon SES Campaign Manager Project](../README.md)** - Deploy this stack to enable scheduled email campaigns that execute automatically in AWS Lambda.

## What It Is

Cloud-based scheduled campaign system for Amazon SES that enables scheduling bulk email sends without requiring local machine availability. Schedule campaigns days or weeks in advance with automatic execution, efficient rate limiting, and smart retry logic.

## Prerequisites

- AWS CLI configured
- Node.js 18+ and npm
- AWS CDK CLI: `npm install -g aws-cdk`
- Python 3 with pip (or Docker) - used during `cdk deploy` to build the `cryptography` Lambda layer
- SES verified domain/email
- (Optional) SES in production mode for high throughput

## Architecture

```
Amazon SES Campaign Manager → S3 (CSV) → DynamoDB → EventBridge (Schedule)
                        ↓               ↓
                    DynamoDB       Campaign Processor → SQS → Email Sender (batch 20) → SES
                    Stream              ↓
                        ↓           Update Status
                    TTL Cleanup
                    (EventBridge + S3)
```

### Key Components

1. **S3 Bucket** - Stores CSV files with recipient data (deleted once a campaign is enqueued)
2. **DynamoDB Table** - Campaign metadata, status and sent/failed counts (kept 90 days)
3. **EventBridge Rules** - One-time rule per campaign triggers it at the scheduled time
4. **SQS Standard Queue** - Buffers email messages for rate-limited processing, with a dead-letter queue
5. **Lambda: Campaign Processor** - Reads CSV, enqueues messages to SQS
6. **Lambda: Email Sender** - Processes batches of 20 emails, sends via SES
7. **Lambda: TTL Cleanup** - Removes EventBridge rules and S3 files when a campaign record is deleted

### Rate Limiting

Based on [AWS Load Testing Sample](https://github.com/aws-samples/load-testing-sample-amazon-ses):

**Why batch size 20?**
- SES SendEmail API: ~40-60ms per call (average 50ms)
- One Lambda can process ~20 emails/second
- Batch processing reduces Lambda invocations by 20x

**How `sendingRateTPS` is enforced:**
- The SQS trigger's **maximum concurrency** is `ceil(TPS / 20)` (minimum 2, maximum 1000)
- Each invocation **paces its sends** to `TPS / concurrency` emails per second
- Total rate never exceeds `sendingRateTPS`, even when SES responds faster than 50ms

**Examples:**
- 10 TPS: 2 concurrent Lambdas, each sending 5 emails/second
- 100 TPS: 5 concurrent Lambdas, each sending 20 emails/second
- 1000 TPS: 50 concurrent Lambdas, each sending 20 emails/second

**Note:** Set `sendingRateTPS` at or below your SES account's maximum send rate (SES console → Account dashboard). Reserved concurrency is not used: it throttles the SQS trigger, which pushes unsent messages to the dead-letter queue.

### Error Handling

- **Throttling** (maximum send rate exceeded): Requeued with exponential backoff (up to 15 minutes), dead-lettered after 10 throttles
- **Daily sending quota exceeded**: Dead-lettered immediately (retrying for hours won't help)
- **Permanent errors** (rejected message, missing template, paused sending, invalid parameters): Dead-lettered immediately
- **Unknown errors**: Retried by SQS up to 5 times, then dead-lettered
- **Partial batch failures**: Only failed messages are retried, not the entire batch
- Every dead-lettered email counts toward the campaign's `failed_count` and includes the error in its message body, so campaigns always reach COMPLETED

### Campaign Statuses

| Status | Meaning |
|--------|---------|
| `SCHEDULED` | Waiting for its scheduled time |
| `PROCESSING` | Recipients are being enqueued and sent |
| `COMPLETED` | Every recipient was sent or failed |
| `FAILED` | The campaign could not be processed (see `error_message`) |
| `CANCELLED` | Cancelled before it started |
| `STOPPED` | Stopped while sending; queued emails were discarded |

If the scheduled time is less than a minute away (or up to 5 minutes in the past, e.g. after a slow CSV upload), the campaign starts immediately instead of waiting for an EventBridge rule that would never fire.

### Retention and Cleanup

- **Campaign records**: Kept for 90 days after the scheduled time (DynamoDB TTL), so status and sent/failed counts stay available in Amazon SES Campaign Manager
- **CSV files**: Deleted as soon as every recipient is enqueued; old object versions are purged after 1 day
- **EventBridge rules**: Deleted when the campaign starts or is cancelled
- **Deleting a campaign** in Amazon SES Campaign Manager: DynamoDB Stream triggers the cleanup Lambda, which removes its EventBridge rule and CSV file

## Installation & Configuration

```bash
cd ses_scheduled_campaigns
npm install
```

Edit `config.json`:

```json
{
  "enableNotifications": true,
  "notificationEmail": "YOUR_EMAIL@example.com",
  "sendingRateTPS": 100,
  "sqsVisibilityTimeout": 300,
  "unsubscribeEncryptionKey": "",
  "unsubscribeEncryptionKeyParameter": "",
  "unsubscribeBaseUrl": "",
  "unsubscribeEndpointUrl": "",
  "unsubscribeMailto": ""
}
```

**Configuration Fields:**

- `enableNotifications` (true/false): Enable SNS email notifications for campaign events
- `notificationEmail`: Email address to receive campaign notifications (required if notifications enabled)
- `sendingRateTPS` (1-14000): Maximum sending rate in emails per second, shared by all campaigns. CDK sets the SQS trigger's maximum concurrency to `ceil(TPS / 20)` and paces each invocation (see [Rate Limiting](#rate-limiting))
  - Examples: 100 TPS = 5 concurrent Lambdas, 1000 TPS = 50 concurrent Lambdas
- `sqsVisibilityTimeout` (300): Minimum seconds a message is hidden after being picked up by Lambda. The stack raises it to at least 6x the Email Sender timeout, as AWS recommends for Lambda triggers
- `unsubscribeEncryptionKey`: Fernet encryption key from Amazon SES Campaign Manager `config/settings.json` (leave empty if not using unsubscribe). Don't commit a real key - prefer `unsubscribeEncryptionKeyParameter`
- `unsubscribeEncryptionKeyParameter`: Name of an SSM Parameter Store **SecureString** holding the Fernet key. When set, it is used instead of `unsubscribeEncryptionKey` and the key is not stored in Lambda environment variables (uses the default `aws/ssm` KMS key)
- `unsubscribeBaseUrl`: Landing page URL from Amazon SES Campaign Manager settings (leave empty if not using unsubscribe)
- `unsubscribeEndpointUrl`: API endpoint URL from Amazon SES Campaign Manager settings (leave empty if not using unsubscribe)
- `unsubscribeMailto`: Mailto address from Amazon SES Campaign Manager settings (leave empty if not using unsubscribe)

**Note:** Unsubscribe fields must match your Amazon SES Campaign Manager `config/settings.json`. If the encryption key differs, the stack rejects the campaign when you schedule it (your unsubscribe endpoint couldn't decrypt the links). The stack also rejects unsubscribe-enabled campaigns when the URLs needed for the selected unsubscribe type aren't configured.

To store the key in SSM instead of `config.json`:

```bash
aws ssm put-parameter --name /ses-campaigns/unsubscribe-key --type SecureString \
  --value "$(python3 -c "import json; print(json.load(open('../config/settings.json'))['email']['unsubscribe_encryption_key'])")"
```

Then set `"unsubscribeEncryptionKeyParameter": "/ses-campaigns/unsubscribe-key"` and leave `unsubscribeEncryptionKey` empty.

## Deployment

```bash
# Bootstrap (first time only)
cdk bootstrap

# Deploy
cdk deploy
```

## How It Works with Amazon SES Campaign Manager

### 1. Scheduling a Campaign

1. User creates campaign in Amazon SES Campaign Manager "Scheduled Campaigns" tab
2. CSV uploaded to S3
3. Campaign metadata saved to DynamoDB (status: SCHEDULED)
4. EventBridge rule created for schedule time
5. Amazon SES Campaign Manager confirms campaign scheduled

### 2. Campaign Execution

1. EventBridge triggers Campaign Processor Lambda at scheduled time
2. Lambda claims the campaign (SCHEDULED → PROCESSING) in a single conditional write, so duplicate triggers can't send it twice
3. Lambda reads CSV from S3, creates individual SQS messages, and saves progress every 100 recipients (if it runs out of time, the retry resumes where it stopped)
4. Email Sender Lambdas process queue (rate-limited by `sendingRateTPS`)
5. Each Lambda processes batches of 20 emails
6. Campaign status updated to COMPLETED once every recipient was sent or failed

### 3. Monitoring & Management

- View campaigns of every status, with sent/failed progress, in Amazon SES Campaign Manager
- Cancel a scheduled campaign, or stop one that is sending
- Delete campaigns that aren't currently sending
- Check CloudWatch logs for details

### CSV Format

Same format as Amazon SES Campaign Manager bulk campaigns:

```csv
To_Address,sub_first_name,sub_company
john@example.com,John,Acme Corp
jane@example.com,Jane,Tech Inc
```

- `To_Address` column (required)
- `sub_*` columns for template variables (e.g., `sub_name` → `{{name}}`)

## Adjusting Sending Rate

The sending rate applies to all campaigns. Change `sendingRateTPS` in `config.json` and redeploy:

```bash
cdk deploy
```

## Monitoring

### CloudWatch Alarms

Sent to the notification email when `enableNotifications` is true:

- **EmailDlqAlarm**: Emails failed permanently - inspect the dead-letter queue (`EmailDLQUrl` output); each message includes `error_message`
- **CampaignProcessorErrorsAlarm**: A campaign could not be processed
- **EmailSenderErrorsAlarm**: Email Sender invocations are failing

### CloudWatch Metrics

- **SQS Visible Messages**: Should decrease steadily
- **Lambda Concurrency**: Actual concurrent executions
- **Lambda Duration**: Should be ~1 second for batch of 20
- **DLQ Messages**: Permanent failures
- **Campaign Progress**: In DynamoDB

### Upgrading

Upgrade in place with `git pull` and `cdk deploy` - don't destroy and redeploy. Deploy when no campaign is currently sending: messages queued by the previous version keep sending, but campaigns that were mid-send may not be marked COMPLETED. See [Upgrading from a Previous Version](../docs/INSTALLATION.md#upgrading-from-a-previous-version) for the full steps.

## Cost Example

Amazon SES Campaign Manager accepts CSV files of up to 100,000 recipients by default (`email.max_csv_rows` in `config/settings.json`); larger campaigns can also be scheduled by calling the `CampaignScheduler` Lambda directly.

**1 Million emails @ 100 TPS:**
- Lambda invocations: 50,000 (20 emails each)
- Duration: ~2.8 hours
- Cost: ~$0.60

**10 Million emails @ 1000 TPS:**
- Lambda invocations: 500,000
- Duration: ~2.8 hours
- Cost: ~$6.00

## Integration with Campaign Analytics

Works seamlessly with [Campaign Analytics Stack](../ses_campaign_analytics/):

1. Select the analytics configuration set (`ses-analytics-<account>`) when scheduling - Amazon SES Campaign Manager selects it by default and warns if it isn't selected
2. `campaign_id` and `campaign_name` tags are added to every email automatically
3. Events automatically flow: SES → Kinesis Firehose → S3 → Athena
4. Query campaign metrics in analytics stack

## Cleanup

```bash
cdk destroy
# Note: S3 bucket must be manually deleted (RETAIN policy)
```

## References

- [AWS Load Testing Sample](https://github.com/aws-samples/load-testing-sample-amazon-ses)
- [Lambda Concurrency](https://docs.aws.amazon.com/lambda/latest/dg/configuration-concurrency.html)
