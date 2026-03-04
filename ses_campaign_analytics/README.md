# SES Campaign Analytics

> **Part of the [Amazon SES Campaign Manager Project](../README.md)** - Deploy this stack to enable comprehensive campaign analytics with materialized views for fast, cost-effective queries.

## What It Is

A complete analytics pipeline for SES email campaigns using Kinesis Firehose, S3, Glue, and Athena. Features daily materialized views for high-performance queries at 95%+ lower cost than querying raw events.

## Prerequisites

- AWS CLI configured
- Node.js 18+ and npm
- AWS CDK CLI: `npm install -g aws-cdk`
- SES verified domain/email

## Architecture

```
SES Events → Kinesis Firehose → S3 (Parquet) → Glue → Athena
                                  ↓
                            Partition Lambda
                                  ↓
                         EventBridge (Daily 2 AM)
                                  ↓
                    Materialized View Refresh Lambda
                                  ↓
                         Pre-aggregated Metrics
```

### Key Components

1. **Kinesis Data Firehose** - Ingests SES events, converts to Parquet
2. **S3 Data Lake** - Stores raw events (partitioned) and materialized views
3. **Partition Lambda** - Auto-adds partitions when data arrives
4. **DynamoDB** - Stores campaign metadata (template, creator, description)
5. **Glue Database** - Catalog for Athena queries
6. **Materialized View Lambda** - Daily aggregation (2 AM UTC)
7. **Athena** - Query engine with 7-day result caching

### Cost Optimization

- **Materialized Views**: Pre-aggregated daily metrics reduce query costs by 95%+
- **Parquet Format**: Columnar storage with Snappy compression
- **Partition Projection**: No manual partition management
- **Query Result Caching**: 7-day cache for identical queries

## Installation & Configuration

```bash
cd ses_campaign_analytics
npm install
```

Edit `config.json`:

```json
{
  "sesConfigurationSetName": "ses-campaign-analytics",
  "sesRefreshScheduleCron": "cron(0 2 * * ? *)",
  "sesDataRetentionDays": 90,
  "sesEnableNotifications": true,
  "sesNotificationEmail": "your-email@example.com"
}
```

**Key Settings:**
- `sesConfigurationSetName`: Name for SES configuration set (auto-created)
- `sesRefreshScheduleCron`: Schedule for materialized view refresh (default: 2 AM UTC)
- `sesNotificationEmail`: Email for SNS notifications

## Deployment

```bash
# Bootstrap (first time only)
cdk bootstrap

# Deploy
cdk deploy

# Confirm SNS subscription email
```

**Deployment time:** ~5-10 minutes

## How It Works with Amazon SES Campaign Manager

### Automatic Detection

Amazon SES Campaign Manager automatically detects the deployed stack via CloudFormation outputs and configures itself.

### Campaign Tagging (Required)

Both tags are **required** for proper tracking:
- `campaign_id`: Unique identifier (auto-generated: `timestamp-random`)
- `campaign_name`: Human-readable name (can be reused)

This dual-tag system prevents data collisions while allowing meaningful grouping.

### Campaign Metadata

When sending emails via Amazon SES Campaign Manager, metadata is automatically stored in DynamoDB:
- Template name and sender address
- Campaign description and creator
- Custom attributes and timestamps

### Amazon SES Campaign Manager Features

- **Campaign Performance Dashboard**: View metrics across all campaigns
- **Date Range Filtering**: Analyze specific time periods
- **Campaign Details**: Click any campaign to view enriched metadata
- **Manual Refresh**: Trigger materialized view refresh for same-day analytics
- **Campaign Search**: Filter by campaign name

### Automatic Partition Management

When Firehose delivers files to S3, a Lambda function automatically:
- Parses S3 key structure (year/month/day/hour)
- Registers partition with Athena
- Makes data queryable within seconds

**Benefits:**
- Zero latency - data available immediately
- No manual maintenance
- Event-driven (pay only for actual data)

## Querying Data

### Wait for Data

After sending emails:
1. Events arrive in Firehose within seconds
2. Firehose buffers (configurable: 0-300 seconds)
3. Data lands in S3 as Parquet
4. Query immediately with Athena

### Query Raw Events

```sql
SELECT * FROM ses_campaign_analytics_db.ses_events
WHERE year = 2025 AND month = 1 AND day = 11
LIMIT 10;
```

### Query Materialized Views (Recommended)

```sql
SELECT * FROM ses_campaign_analytics_db.campaign_metrics_daily
WHERE campaign_name = 'newsletter-2025'
ORDER BY date DESC;
```

### Query Campaign Summary

```sql
SELECT * FROM ses_campaign_analytics_db.campaign_summary
ORDER BY total_sent DESC;
```

## Monitoring

### CloudWatch Metrics

- Firehose delivery success/failure rates
- Lambda execution duration and errors
- Athena query execution times
- S3 storage metrics

### SNS Notifications

Email notifications for:
- Successful materialized view refreshes
- Failed refresh attempts with error details
- Daily summary of processed data

## Troubleshooting

### No Data in Athena

1. Check Firehose delivery: `aws firehose describe-delivery-stream --delivery-stream-name ses-campaign-events`
2. Verify SES configuration set event destinations
3. Check S3 for data: `aws s3 ls s3://ses-raw-events-ACCOUNT-REGION/events/ --recursive`
4. Ensure emails have campaign tags
5. Wait 5-10 minutes for Firehose buffering

### Materialized View Refresh Failures

1. Check Lambda logs: `aws logs tail /aws/lambda/MaterializedViewRefresh --follow`
2. Verify data exists for the date being processed
3. Check IAM permissions
4. Manually invoke: `aws lambda invoke --function-name MaterializedViewRefresh --payload '{}' response.json`

### High Query Costs

1. Always query materialized views, not raw events
2. Add date filters: `WHERE date >= current_date - interval '30' day`
3. Use the Athena workgroup (has cost controls)
4. Verify query result cache is working

## Manual Operations

### Trigger Materialized View Refresh

```bash
aws lambda invoke \
  --function-name MaterializedViewRefresh \
  --payload '{}' \
  response.json
```

### Update Refresh Schedule

Edit `config.json` and redeploy:

```json
{
  "sesRefreshScheduleCron": "cron(0 3 * * ? *)"  // 3 AM UTC
}
```

Then: `cdk deploy`

## Integration with Scheduled Campaigns

Works seamlessly with [Scheduled Campaigns Stack](../ses_scheduled_campaigns/):

- Both use the same SES `configuration_set`
- Scheduled campaigns automatically include proper tags
- All events flow into this analytics pipeline
- Query scheduled campaign performance using materialized views

## Cleanup

```bash
cdk destroy
```

**Note:** S3 buckets are retained. To fully clean up:

```bash
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REGION=$(aws configure get region)

# Empty and delete buckets
aws s3 rm s3://ses-raw-events-${ACCOUNT}-${REGION} --recursive
aws s3 rb s3://ses-raw-events-${ACCOUNT}-${REGION}

aws s3 rm s3://ses-processed-events-${ACCOUNT}-${REGION} --recursive
aws s3 rb s3://ses-processed-events-${ACCOUNT}-${REGION}

aws s3 rm s3://ses-athena-results-${ACCOUNT}-${REGION} --recursive
aws s3 rb s3://ses-athena-results-${ACCOUNT}-${REGION}
```

## Cost Tracking

All resources tagged for AWS Cost Explorer:
- `Project`: `SES-Campaign-Analytics`
- `ManagedBy`: `CDK`
- `Environment`: `Production`

**View Costs:**
1. AWS Cost Explorer → Activate Cost Allocation Tags
2. Enable `Project` tag
3. Wait 24 hours
4. Filter by `Project = SES-Campaign-Analytics`

