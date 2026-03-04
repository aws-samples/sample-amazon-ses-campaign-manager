# Installation Guide

Complete setup instructions, configuration, optional CDK stacks, and troubleshooting.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Local Installation](#local-installation)
- [First Run Configuration](#first-run-configuration)
- [Optional CDK Stacks](#optional-cdk-stacks)
- [Configuration](#configuration)
- [Troubleshooting](#troubleshooting)

---

## Prerequisites

### Required

**Python 3.8+**
```bash
python3 --version
# Should show 3.8 or higher
```

**AWS CLI configured with credentials**
```bash
aws configure list
# Should show configured profile

aws configure list-profiles
# Should list available profiles
```

**Amazon SES Account**
- Account in production mode (or sandbox with verified test addresses)
- At least one verified email identity or domain
- IAM permissions for SES operations

### IAM Permissions Required

Minimum permissions for TUI operations:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ses:SendEmail",
        "ses:SendTemplatedEmail",
        "ses:GetAccount",
        "ses:ListIdentities",
        "ses:ListTemplates",
        "ses:GetTemplate",
        "ses:CreateTemplate",
        "ses:UpdateTemplate",
        "ses:DeleteTemplate",
        "ses:ListConfigurationSets",
        "cloudwatch:GetMetricStatistics"
      ],
      "Resource": "*"
    }
  ]
}
```

**For Scheduled Campaigns** (additional):
```json
{
  "Effect": "Allow",
  "Action": [
    "s3:PutObject",
    "s3:GetObject",
    "dynamodb:PutItem",
    "dynamodb:Query",
    "dynamodb:DeleteItem",
    "events:PutRule",
    "events:PutTargets",
    "events:DeleteRule",
    "events:RemoveTargets",
    "cloudformation:DescribeStacks"
  ],
  "Resource": [
    "arn:aws:s3:::sesscheduledcampaignsstack-*/*",
    "arn:aws:dynamodb:*:*:table/SesScheduledCampaignsStack-*",
    "arn:aws:events:*:*:rule/ses-campaign-*",
    "arn:aws:cloudformation:*:*:stack/SesScheduledCampaignsStack/*"
  ]
}
```

**For Campaign Analytics** (additional):
```json
{
  "Effect": "Allow",
  "Action": [
    "athena:StartQueryExecution",
    "athena:GetQueryExecution",
    "athena:GetQueryResults",
    "glue:GetDatabase",
    "glue:GetTable",
    "s3:GetObject",
    "s3:PutObject",
    "dynamodb:PutItem",
    "dynamodb:GetItem",
    "dynamodb:Query",
    "lambda:InvokeFunction",
    "cloudformation:DescribeStacks"
  ],
  "Resource": [
    "arn:aws:athena:*:*:workgroup/ses-analytics-wg-*",
    "arn:aws:glue:*:*:database/ses_analytics_db_*",
    "arn:aws:glue:*:*:table/ses_analytics_db_*/*",
    "arn:aws:s3:::sescampaignanalyticsstack-*/*",
    "arn:aws:dynamodb:*:*:table/SesCampaignAnalyticsStack-*",
    "arn:aws:lambda:*:*:function:SesCampaignAnalyticsStack-*",
    "arn:aws:cloudformation:*:*:stack/SesCampaignAnalyticsStack/*"
  ]
}
```

---

## Local Installation

### Step 1: Clone Repository

```bash
# Clone the repository
git clone <repository-url>
cd ses_tui
```

### Step 2: Create Virtual Environment

```bash
# Create virtual environment
python3 -m venv venv

# Activate (macOS/Linux)
source venv/bin/activate

# Activate (Windows)
venv\Scripts\activate
```

### Step 3: Install Dependencies

```bash
# Install required packages
pip install -r requirements.txt
```

**Requirements**:
- `textual>=0.41.0` - Terminal UI framework
- `boto3>=1.26.0` - AWS SDK for Python
- `cryptography>=41.0.0` - Encryption for unsubscribe links

### Step 4: Verify Installation

```bash
# Run application
python3 ses_manager_modular.py

# Or use shell script
chmod +x run_modular.sh
./run_modular.sh
```

---

## First Run Configuration

### Profile Selection

On first launch, you'll see the Profile Selection screen:

1. **List of AWS Profiles**: Shows all profiles from `~/.aws/credentials`
2. **Select Profile**: Use arrow keys to navigate, Enter to select
3. **Choose Region**: Select AWS region for SES operations
4. **Confirmation**: Settings saved to `~/.ses_manager_settings.json`

### Initial Setup Checklist

After first run, verify:

- [ ] **Profile selected**: Settings shows correct AWS profile
- [ ] **Region selected**: Matches your SES verified identities
- [ ] **Identities loaded**: Dashboard shows verified email addresses/domains
- [ ] **Templates loaded**: Templates tab shows existing templates (if any)
- [ ] **Account details**: Dashboard displays send quota and rate

### Settings File Location

Settings stored in: `~/.ses_manager_settings.json`

Contains:
- AWS profile and region
- Unsubscribe configuration
- Email statistics
- Debug preferences
- Default configuration set

---

## Optional CDK Stacks

### Prerequisites for CDK Deployment

**AWS CDK installed**:
```bash
npm install -g aws-cdk
cdk --version
```

**AWS Account bootstrapped**:
```bash
cdk bootstrap aws://ACCOUNT-ID/REGION
```

### Scheduled Campaigns Stack

**Purpose**: Schedule bulk email campaigns for future execution.

**Repository**: [ses-scheduled-campaigns](../../ses_scheduled_campaigns/)

**Deployment**:
```bash
cd ses_scheduled_campaigns

# Install dependencies
npm install

# Review configuration
cat config.json

# Deploy
cdk deploy
```

**Configuration** (`config.json`):
```json
{
  "enableNotifications": true,
  "notificationEmail": "YOUR_EMAIL@example.com",
  "sendingRateTPS": 100,
  "sqsVisibilityTimeout": 300,
  "unsubscribeEncryptionKey": "YOUR_FERNET_KEY_HERE",
  "unsubscribeBaseUrl": "https://example.com",
  "unsubscribeEndpointUrl": "https://api.example.com/unsubscribe",
  "unsubscribeMailto": "unsubscribe@example.com"
}
```

**Key Parameters**:
- `enableNotifications`: Enable SNS notifications for campaign events
- `notificationEmail`: Email address for notifications
- `sendingRateTPS`: Target sending rate in emails per second (controls Lambda concurrency)
  - Formula: `sendingRateTPS ÷ 20 = Lambda Reserved Concurrency`
  - Example: 100 TPS ÷ 20 = 5 reserved concurrency
- `sqsVisibilityTimeout`: SQS visibility timeout in seconds (should match Lambda timeout)
- `unsubscribeEncryptionKey`: Fernet encryption key (copy from TUI settings)
- `unsubscribeBaseUrl`: Base URL for unsubscribe landing page
- `unsubscribeEndpointUrl`: API endpoint for one-click unsubscribe
- `unsubscribeMailto`: Email address for mailto unsubscribe links

**Resources Created**:
- S3 bucket for CSV files
- DynamoDB table for campaign metadata
- SQS queue for email messages with DLQ
- EventBridge rules for scheduling
- 3 Lambda functions: CampaignScheduler, CampaignProcessor, EmailSender
- TTL cleanup Lambda
- SNS topic for notifications (optional)
- IAM roles and policies

**Cost Estimate**: $5-15/month (depending on usage)
- S3: $0.023 per GB
- DynamoDB: $0.25 per GB (on-demand)
- Lambda: First 1M requests free, $0.20 per 1M after
- SQS: First 1M requests free, $0.40 per 1M after

**Verification**:
1. Open TUI → Send Email tab
2. Mode dropdown should show "Scheduled Campaign" option
3. Send Email → Scheduled Campaigns tab should show stack info

### Campaign Analytics Stack

**Purpose**: Track detailed campaign performance with cost-efficient queries.

**Repository**: [ses-campaign-analytics](../../ses_campaign_analytics/)

**Deployment**:
```bash
cd ses_campaign_analytics

# Install dependencies
npm install

# Review configuration
cat config.json

# Deploy
cdk deploy
```

**Configuration** (`config.json`):
```json
{
  "sesExistingConfigurationSetName": "",
  "sesRefreshScheduleCron": "cron(0 2 * * ? *)",
  "sesDataRetentionDays": 90,
  "sesEnableNotifications": true,
  "sesNotificationEmail": "your-email@example.com",
  "firehoseBufferSizeMB": 64,
  "firehoseBufferIntervalSeconds": 0,
  "athenaQueryResultsRetentionDays": 7,
  "processedDataTransitionToIADays": 30,
  "lambdaTimeoutMinutes": 15,
  "lambdaMemoryMB": 1024,
  "athenaQueryScanLimitGB": 10
}
```

**Key Parameters**:
- `sesExistingConfigurationSetName`: Use existing SES configuration set (leave empty to create new)
- `sesRefreshScheduleCron`: Cron expression for nightly materialized view refresh
- `sesDataRetentionDays`: How long to retain raw event data in S3
- `sesEnableNotifications`: Enable SNS notifications for analytics events
- `sesNotificationEmail`: Email address for notifications
- `firehoseBufferSizeMB`: Firehose buffer size (minimum 64 MB for Parquet conversion)
- `firehoseBufferIntervalSeconds`: Firehose buffer interval (0 = flush immediately)
- `athenaQueryResultsRetentionDays`: Retention for Athena query results
- `processedDataTransitionToIADays`: Days before transitioning processed data to Infrequent Access
- `lambdaTimeoutMinutes`: Timeout for materialized view refresh Lambda
- `lambdaMemoryMB`: Memory allocation for refresh Lambda
- `athenaQueryScanLimitGB`: Maximum GB scanned per Athena query (cost control)

**Resources Created**:
- Kinesis Firehose delivery stream
- S3 buckets for raw and processed data
- Glue database and tables
- Athena workgroup
- DynamoDB table for campaign metadata
- Lambda function for materialized view refresh
- SES configuration set with event destinations
- IAM roles and policies

**Cost Estimate**: $10-30/month (depending on volume)
- Kinesis Firehose: $0.029 per GB ingested
- S3: $0.023 per GB stored
- Athena: $5 per TB scanned
- Glue: $0.44 per DPU-hour
- Lambda: Within free tier for most usage

**Verification**:
1. Open TUI → Campaign Analytics tab
2. Should show "Stack detected" with configuration set name
3. Can query campaigns and view metrics

### Uninstalling CDK Stacks

**Scheduled Campaigns**:
```bash
cd ses_scheduled_campaigns
cdk destroy
```

**Campaign Analytics**:
```bash
cd ses_campaign_analytics
cdk destroy
```

**Important**: 
- Destroy command does NOT delete S3 buckets with data (prevents accidental data loss)
- Manually delete S3 buckets if you want to remove all data
- DynamoDB tables with data may also need manual deletion

---

## Configuration

### AWS Configuration

**Switch Profile/Region**:
1. Settings tab → AWS Configuration
2. Click "Change Profile/Region"
3. Select new profile and region
4. Application reloads with new configuration

**Multiple Profiles**:
Create multiple AWS CLI profiles:
```bash
# Configure new profile
aws configure --profile production
aws configure --profile staging
aws configure --profile development
```

Switch between them in TUI Settings.

### Unsubscribe Configuration

**Generate Encryption Key**:
1. Settings tab → Unsubscribe Configuration
2. Click "Generate Key"
3. Key saved automatically
4. Or paste existing Fernet key

**Configure URLs**:
1. **Unsubscribe Page URL**: `https://example.com/unsubscribe`
   - GET endpoint receiving `user` (encrypted email) and optional `topic` parameters
2. **One-Click API URL**: `https://api.example.com/unsubscribe`
   - POST endpoint receiving `address` (encrypted email) and optional `topic` parameters
3. **Mailto Address** (optional): `unsubscribe@example.com`

**Implementation Example** (your backend):
```python
from cryptography.fernet import Fernet

# Your Fernet key from TUI settings
key = b'your-fernet-key-here'
cipher = Fernet(key)

# Decrypt email from URL parameter
encrypted_email = request.args.get('user')
email = cipher.decrypt(encrypted_email.encode()).decode()

# Process unsubscribe for this email
# ... your logic ...
```

### Debug Logging

**Enable**:
1. Settings tab → Application Settings
2. Toggle "Debug Logging" on
3. Logs written to `debug_logs.txt`

**Categories**:
- `[API]`: AWS API calls and responses
- `[CACHE]`: Cache hits/misses and operations
- `[UI]`: UI events and state changes
- `[EMAIL]`: Email sending operations

**Use for**:
- Troubleshooting API errors
- Understanding cache behavior
- Debugging template issues
- Tracking email send failures

### Cache Management

**View Statistics**:
Settings tab → Cache Management → "View Stats"
- Hit/miss rates
- File count
- Total cache size

**Clear Cache**:
Settings tab → Cache Management → "Clear All"
- Removes all cached data
- Next requests fetch fresh from AWS

**Manual Invalidation**:
Delete specific cache files:
```bash
rm cache/get_templates.json
rm cache/get_identities.json
# etc.
```

---

## Troubleshooting

### Common Issues

#### "No AWS profiles found"

**Cause**: AWS CLI not configured

**Solution**:
```bash
aws configure
# Enter AWS Access Key ID
# Enter AWS Secret Access Key
# Enter Default region
# Enter Default output format
```

#### "Access Denied" errors

**Cause**: Missing IAM permissions

**Solution**:
1. Verify IAM user/role has required permissions (see [Prerequisites](#prerequisites))
2. Check AWS profile has correct credentials
3. Test with AWS CLI:
```bash
aws ses list-identities --profile your-profile
aws ses get-account --profile your-profile
```

#### "Rate exceeded" errors

**Cause**: Hitting Amazon SES API rate limits (1 TPS for management APIs)

**Solution**:
1. Clear cache to reset: Settings → Cache Management → Clear All
2. Wait 1 minute before retrying
3. Caching should prevent this in normal usage

#### Templates not loading

**Cause**: 
- No templates in SES
- Wrong region selected
- Cache issue

**Solution**:
1. Verify templates exist in AWS Console → SES → Email Templates
2. Check correct region selected in Settings
3. Clear template cache: `rm cache/get_templates.json`
4. Refresh Templates tab

#### Bulk sending throttled

**Cause**: Sending rate exceeds Amazon SES MaxSendRate

**Solution**:
1. Check MaxSendRate in Dashboard tab
2. Reduce configured rate to 85-90% of MaxSendRate
3. Monitor "⚠️ Throttled" counter during send
4. Adjust rate if throttling continues

#### Scheduled campaigns not appearing

**Cause**:
- CDK stack not deployed
- Wrong region
- Stack not detected

**Solution**:
1. Verify CDK stack deployed: `aws cloudformation describe-stacks --stack-name SesScheduledCampaignsStack`
2. Check same region as TUI
3. Click "Refresh Stack Detection" in Scheduled Campaigns tab

#### Campaign analytics not loading

**Cause**:
- CDK stack not deployed
- Configuration set not used when sending
- No campaign data yet

**Solution**:
1. Verify CDK stack deployed
2. Check configuration set used when sending emails
3. Verify campaign tag included in email
4. Process today's data manually: Campaign Analytics → "Process Today's Data"

#### "Invalid JSON" errors

**Cause**: Malformed JSON in template data or SES tags fields

**Solution**:
1. Validate JSON syntax (use online validator)
2. Check for missing commas, brackets, quotes
3. Example valid JSON:
```json
{
  "name": "John",
  "company": "Acme Corp"
}
```

### Getting Help

**Check Logs**:
1. Enable debug logging in Settings
2. Reproduce issue
3. Review `debug_logs.txt` for details

**Verify AWS Setup**:
```bash
# Test SES access
aws ses get-account

# List identities
aws ses list-identities

# List templates
aws ses list-templates

# Check CloudFormation stacks
aws cloudformation describe-stacks
```

**Common Log Patterns**:
- `[ERROR] [API]`: AWS API error - check permissions
- `[ERROR] [CACHE]`: Cache file issue - clear cache
- `[ERROR] [EMAIL]`: Sending error - check template/recipients

### Performance Optimization

**Slow Loading**:
- Caching should speed up after first load
- Check network latency to AWS region
- Consider switching to closer region

**High Memory Usage**:
- Clear cache periodically
- Restart TUI for long sessions
- Large CSV files may use more memory

**Slow Bulk Sending**:
- Check configured rate vs MaxSendRate
- Monitor network connection
- Verify no throttling occurring

---

For feature details, see [Features Guide](FEATURES.md).  
For usage instructions, see [Usage Guide](USAGE.md).
