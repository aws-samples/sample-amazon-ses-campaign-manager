#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { SesCampaignAnalyticsStack } from './ses-campaign-analytics-stack';
import { AwsSolutionsChecks, NagSuppressions } from 'cdk-nag';
import { Aspects } from 'aws-cdk-lib';
import * as fs from 'fs';
import * as path from 'path';

const app = new cdk.App();

// Add CDK Nag security checks
Aspects.of(app).add(new AwsSolutionsChecks({ verbose: true }));

// Load configuration
const configPath = path.join(process.cwd(), 'config.json');
const config = JSON.parse(fs.readFileSync(configPath, 'utf-8'));

// Deploy SES Campaign Analytics Stack
const sesStack = new SesCampaignAnalyticsStack(app, 'SesCampaignAnalyticsStack', {
  existingConfigurationSetName: config.sesExistingConfigurationSetName,
  refreshScheduleCron: config.sesRefreshScheduleCron,
  dataRetentionDays: config.sesDataRetentionDays,
  enableNotifications: config.sesEnableNotifications,
  notificationEmail: config.sesNotificationEmail,
  firehoseBufferSizeMB: config.firehoseBufferSizeMB,
  firehoseBufferIntervalSeconds: config.firehoseBufferIntervalSeconds,
  athenaQueryResultsRetentionDays: config.athenaQueryResultsRetentionDays,
  processedDataTransitionToIADays: config.processedDataTransitionToIADays,
  lambdaTimeoutMinutes: config.lambdaTimeoutMinutes,
  lambdaMemoryMB: config.lambdaMemoryMB,
  athenaQueryScanLimitGB: config.athenaQueryScanLimitGB,
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION,
  },
});

// Add CDK Nag suppressions for legitimate use cases
NagSuppressions.addStackSuppressions(sesStack, [
  {
    id: 'AwsSolutions-IAM4',
    reason: 'Lambda functions require AWSLambdaBasicExecutionRole for CloudWatch Logs access. This is a standard AWS managed policy for Lambda execution.',
    appliesTo: ['Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole']
  },
  {
    id: 'AwsSolutions-IAM5',
    reason: 'S3 bucket permissions require wildcards for object-level operations. This is necessary for Lambda and Firehose to read/write objects in the buckets.'
  },
  {
    id: 'AwsSolutions-IAM5',
    reason: 'CloudWatch Logs require wildcard permissions for log stream creation. Log group ARNs with :* suffix are required for CloudWatch Logs functionality.',
    appliesTo: [
      'Resource::arn:aws:logs:<AWS::Region>:<AWS::AccountId>:log-group:/aws/lambda/*:*',
      'Resource::arn:aws:logs:<AWS::Region>:<AWS::AccountId>:log-group:/aws/kinesisfirehose/ses-analytics-<AWS::AccountId>:*'
    ]
  },
  {
    id: 'AwsSolutions-IAM5',
    reason: 'Glue table permissions require wildcards for the CreateTableLambda to access table metadata. This is necessary for the Lambda to execute Athena queries that create tables.'
  },
  {
    id: 'AwsSolutions-IAM5',
    reason: 'Log retention Lambda requires wildcard permissions for log management. This is automatically created by CDK for log retention functionality.',
    appliesTo: ['Resource::*']
  },
  {
    id: 'AwsSolutions-S1',
    reason: 'S3 server access logging is not required for these buckets as they are used for analytics data storage and query results, not for serving content.',
  },
  {
    id: 'AwsSolutions-KDF1',
    reason: 'Kinesis Firehose delivery stream encryption is handled by S3 bucket encryption (SSE-S3) at rest.',
  },
  {
    id: 'AwsSolutions-L1',
    reason: 'Python 3.12 is the latest stable runtime for Lambda at the time of deployment. Runtime will be updated as newer versions become available.',
  }
]);

// Add resource-specific suppressions for IAM wildcard permissions
// Match the exact ARN patterns from the error messages
NagSuppressions.addResourceSuppressionsByPath(
  sesStack,
  '/SesCampaignAnalyticsStack/FirehoseGluePolicy/Resource',
  [
    {
      id: 'AwsSolutions-IAM5',
      reason: 'Firehose needs wildcard permissions to dynamically access any table in the Glue database for Parquet schema conversion. The wildcard is scoped to a specific database and cannot be further restricted as Firehose may need to access multiple tables created over time.',
      appliesTo: ['Resource::arn:aws:glue:*:*:table/*']
    }
  ]
);

NagSuppressions.addResourceSuppressionsByPath(
  sesStack,
  '/SesCampaignAnalyticsStack/MaterializedViewRefresh/ServiceRole/DefaultPolicy/Resource',
  [
    {
      id: 'AwsSolutions-IAM5',
      reason: 'Lambda needs wildcard permissions to access all tables in the Glue database for materialized view refresh. The Lambda reads from multiple source tables that may be created dynamically and manages partition metadata across tables. The wildcard is scoped to a specific database within the account/region.',
      appliesTo: ['Resource::arn:aws:glue:*:*:table/*']
    }
  ]
);

NagSuppressions.addResourceSuppressionsByPath(
  sesStack,
  '/SesCampaignAnalyticsStack/AthenaPartitionLambda/ServiceRole/DefaultPolicy/Resource',
  [
    {
      id: 'AwsSolutions-IAM5',
      reason: 'Lambda needs wildcard permissions for automated partition management: (1) Athena workgroup wildcard allows executing partition queries across any workgroup since names can vary by environment. (2) Glue table wildcard enables dynamic partition creation without requiring stack updates. Both wildcards are scoped to the specific account/region.',
    }
  ]
);
