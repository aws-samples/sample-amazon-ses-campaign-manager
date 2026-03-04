# SES Scheduled Campaigns

> **Part of the [Amazon SES Campaign Manager Project](../README.md)** - Deploy this stack to enable scheduled email campaigns that execute automatically in AWS Lambda.

## What It Is

Cloud-based scheduled campaign system for Amazon SES that enables scheduling bulk email sends without requiring local machine availability. Schedule campaigns days or weeks in advance with automatic execution, efficient rate limiting, and smart retry logic.

## Prerequisites

- AWS CLI configured
- Node.js 18+ and npm
- AWS CDK CLI: `npm install -g aws-cdk`
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

1. **S3 Bucket** - Stores CSV files with recipient data
2. **DynamoDB Table** - Campaign metadata and status tracking
3. **EventBridge Scheduler** - Triggers campaigns at scheduled time
4. **SQS FIFO Queue** - Buffers email messages for rate-limited processing
5. **Lambda: Campaign Processor** - Reads CSV, enqueues messages to SQS
6. **Lambda: Email Sender** - Processes batches of 20 emails, sends via SES
7. **Lambda: TTL Cleanup** - Removes EventBridge rules and S3 files after execution

### Rate Limiting (AWS Best Practice)

Based on [AWS Load Testing Sample](https://github.com/aws-samples/load-testing-sample-amazon-ses):

**Why batch size 20?**
- SES SendEmail API: ~40-60ms per call (average 50ms)
- One Lambda can process ~20 emails/second
- Batch processing reduces Lambda invocations by 20x

**Formula:** `Target TPS = Reserved Concurrency × 20 emails/sec`

**Examples:**
- 100 TPS: 5-7 concurrent Lambdas
- 1000 TPS: 50-70 concurrent Lambdas
- 2000 TPS: 100-140 concurrent Lambdas

**Note:** Add ~40% buffer for Lambda-SQS scaling overhead.

### Error Handling

- **Transient errors** (throttling): Message requeued to SQS automatically
- **Permanent errors** (invalid email): Sent to DLQ after 3 attempts
- **Partial batch failures**: Only failed messages requeue, not entire batch

### Automatic Cleanup (TTL)

- **1 hour after execution**: DynamoDB TTL expires campaign record
- **DynamoDB Stream**: Triggers cleanup Lambda
- **Cleanup actions**: Removes EventBridge rule + deletes S3 CSV file
- **Analytics preserved**: Campaign data retained in separate analytics table

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
  "unsubscribeBaseUrl": "",
  "unsubscribeEndpointUrl": "",
  "unsubscribeMailto": ""
}
```

**Configuration Fields:**

- `enableNotifications` (true/false): Enable SNS email notifications for campaign events
- `notificationEmail`: Email address to receive campaign notifications (required if notifications enabled)
- `sendingRateTPS` (1-14000): Target sending rate in emails per second. CDK auto-calculates Lambda concurrency: `ceil(TPS / 20)`
  - Examples: 100 TPS = 5 concurrent Lambdas, 1000 TPS = 50 concurrent Lambdas
- `sqsVisibilityTimeout` (300): Seconds a message is hidden after being picked up by Lambda (5 minutes recommended)
  - If Lambda doesn't finish within this time, message becomes visible again for retry
- `unsubscribeEncryptionKey`: Fernet encryption key from Amazon SES Campaign Manager `config/settings.json` (leave empty if not using unsubscribe)
- `unsubscribeBaseUrl`: Landing page URL from Amazon SES Campaign Manager settings (leave empty if not using unsubscribe)
- `unsubscribeEndpointUrl`: API endpoint URL from Amazon SES Campaign Manager settings (leave empty if not using unsubscribe)
- `unsubscribeMailto`: Mailto address from Amazon SES Campaign Manager settings (leave empty if not using unsubscribe)

**Note:** Unsubscribe fields should match your Amazon SES Campaign Manager `config/settings.json` if you're using unsubscribe features. Keep them in sync between both configurations.

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
2. Lambda updates status to PROCESSING
3. Lambda reads CSV from S3, creates individual SQS messages
4. Email Sender Lambdas process queue (rate-limited by concurrency)
5. Each Lambda processes batches of 20 emails
6. Campaign status updated to COMPLETED when done

### 3. Monitoring & Management

- View scheduled campaigns in Amazon SES Campaign Manager
- Delete campaigns before execution
- Monitor progress in DynamoDB
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

The sending rate is configured in Lambda concurrency, not per-campaign.

### Via AWS Console

1. Go to AWS Lambda Console
2. Find function: `SesScheduledCampaignsStack-EmailSender...`
3. Go to **Configuration** → **Concurrency**
4. Set **Reserved concurrent executions**

**Formula:** `Reserved Concurrency × 20 = Target TPS`

### Via AWS CLI

```bash
# Set to 100 TPS (5 concurrent executions)
aws lambda put-function-concurrency \
  --function-name SesScheduledCampaignsStack-EmailSender... \
  --reserved-concurrent-executions 5
```

## Monitoring

### CloudWatch Metrics

- **SQS Visible Messages**: Should decrease steadily
- **Lambda Concurrency**: Actual concurrent executions
- **Lambda Duration**: Should be ~1 second for batch of 20
- **DLQ Messages**: Permanent failures
- **Campaign Progress**: In DynamoDB

## Cost Example

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

1. Use the same SES `configuration_set` in both stacks
2. Set appropriate `campaign_id` and `campaign_name` tags
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
