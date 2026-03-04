import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { Duration, RemovalPolicy, CfnOutput, Tags } from 'aws-cdk-lib';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as subscriptions from 'aws-cdk-lib/aws-sns-subscriptions';
import * as lambdaEventSources from 'aws-cdk-lib/aws-lambda-event-sources';
import { NagSuppressions } from 'cdk-nag';
import * as path from 'path';

export interface SesScheduledCampaignsStackProps extends cdk.StackProps {
  campaignRetentionDays: number;
  csvRetentionDays: number;
  enableNotifications: boolean;
  notificationEmail?: string;
  lambdaTimeout: number;
  lambdaMemory: number;
  emailSenderMemory: number;
  sendingRateTPS: number;
  sqsVisibilityTimeout: number;
  sqsMessageRetention: number;
  sqsMaxReceiveCount: number;
  dlqMessageRetention: number;
  enablePointInTimeRecovery: boolean;
  unsubscribeEncryptionKey: string;
  unsubscribeBaseUrl: string;
  unsubscribeEndpointUrl: string;
  unsubscribeMailto: string;
}

export class SesScheduledCampaignsStack extends cdk.Stack {
  public readonly campaignBucket: s3.Bucket;
  public readonly campaignTable: dynamodb.Table;
  public readonly emailQueue: sqs.Queue;
  public readonly campaignProcessorFunction: lambda.Function;
  public readonly emailSenderFunction: lambda.Function;

  constructor(scope: Construct, id: string, props: SesScheduledCampaignsStackProps) {
    super(scope, id, props);

    // Add cost allocation tags for tracking expenses
    Tags.of(this).add('Project', 'SES-Scheduled-Campaigns');
    Tags.of(this).add('ManagedBy', 'CDK');
    Tags.of(this).add('Environment', 'Production');

    //////// S3 BUCKET FOR CAMPAIGN CSV FILES ////////

    this.campaignBucket = new s3.Bucket(this, 'CampaignBucket', {
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      removalPolicy: RemovalPolicy.RETAIN,
      enforceSSL: true,
      versioned: true, // Enable versioning for audit trail
      lifecycleRules: [
        {
          id: 'ArchiveOldCampaigns',
          enabled: true,
          transitions: [
            {
              storageClass: s3.StorageClass.INTELLIGENT_TIERING,
              transitionAfter: Duration.days(7),
            },
          ],
          expiration: Duration.days(props.csvRetentionDays),
        },
      ],
    });

    //////// DYNAMODB TABLE FOR CAMPAIGN METADATA ////////

    this.campaignTable = new dynamodb.Table(this, 'CampaignTable', {
      partitionKey: { name: 'campaign_id', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'schedule_timestamp', type: dynamodb.AttributeType.NUMBER },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      pointInTimeRecovery: props.enablePointInTimeRecovery,
      removalPolicy: RemovalPolicy.DESTROY,
      timeToLiveAttribute: 'ttl',
      stream: dynamodb.StreamViewType.OLD_IMAGE, // Enable stream for TTL cleanup
    });

    // GSI for querying campaigns by status
    this.campaignTable.addGlobalSecondaryIndex({
      indexName: 'status-index',
      partitionKey: { name: 'status', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'schedule_timestamp', type: dynamodb.AttributeType.NUMBER },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    //////// SQS QUEUES FOR EMAIL PROCESSING ////////

    // Dead Letter Queue
    const emailDlq = new sqs.Queue(this, 'EmailDLQ', {
      queueName: `ses-scheduled-campaigns-dlq-${cdk.Stack.of(this).account}`,
      encryption: sqs.QueueEncryption.SQS_MANAGED,
      retentionPeriod: Duration.seconds(props.dlqMessageRetention),
    });

    // Main Standard Queue for email processing (like AWS sample)
    this.emailQueue = new sqs.Queue(this, 'EmailQueue', {
      queueName: `ses-scheduled-campaigns-queue-${cdk.Stack.of(this).account}`,
      encryption: sqs.QueueEncryption.SQS_MANAGED,
      visibilityTimeout: Duration.seconds(props.sqsVisibilityTimeout),
      retentionPeriod: Duration.seconds(props.sqsMessageRetention),
      deadLetterQueue: {
        queue: emailDlq,
        maxReceiveCount: props.sqsMaxReceiveCount,
      },
    });

    //////// SNS TOPIC FOR NOTIFICATIONS ////////

    let notificationTopic: sns.Topic | undefined;

    if (props.enableNotifications) {
      notificationTopic = new sns.Topic(this, 'CampaignNotificationTopic', {
        displayName: 'SES Scheduled Campaign Notifications',
      });

      // Enforce SSL
      notificationTopic.addToResourcePolicy(
        new iam.PolicyStatement({
          effect: iam.Effect.DENY,
          actions: ['sns:Publish'],
          resources: [notificationTopic.topicArn],
          principals: [new iam.AnyPrincipal()],
          conditions: { Bool: { 'aws:SecureTransport': 'false' } },
        }),
      );

      if (props.notificationEmail) {
        notificationTopic.addSubscription(
          new subscriptions.EmailSubscription(props.notificationEmail),
        );
      }
    }

    //////// LAMBDA FUNCTION: CAMPAIGN PROCESSOR ////////

    // Use AWS public Lambda layer for cryptography (Python 3.12)
    // This is a public layer maintained by Klayers: https://github.com/keithrozario/Klayers
    const cryptographyLayer = lambda.LayerVersion.fromLayerVersionArn(
      this,
      'CryptographyLayer',
      `arn:aws:lambda:${cdk.Stack.of(this).region}:770693421928:layer:Klayers-p312-cryptography:13`
    );

    // Lambda code without dependencies
    const lambdaCode = lambda.Code.fromAsset(path.join(__dirname, '../lambda'));
    
    // Note: EmailSender function must be created before CampaignProcessor
    // so we can reference its name in the environment variable
    
    // Calculate Lambda concurrency from desired TPS
    // Formula: Concurrency = ceil(TPS / 20)
    // Each Lambda processes 20 emails, so TPS/20 = required concurrent executions
    const targetTPS = props.sendingRateTPS || 1;
    const calculatedConcurrency = Math.max(1, Math.ceil(targetTPS / 20)); // Minimum 1, no decimals
    
    this.emailSenderFunction = new lambda.Function(this, 'EmailSender', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'email_sender.handler',
      code: lambdaCode,
      layers: [cryptographyLayer],
      timeout: Duration.minutes(5),
      memorySize: props.emailSenderMemory,
      logRetention: logs.RetentionDays.ONE_WEEK,
      reservedConcurrentExecutions: calculatedConcurrency, // Auto-calculated from config (min 1)
      environment: {
        CAMPAIGN_TABLE_NAME: this.campaignTable.tableName,
        EMAIL_QUEUE_URL: this.emailQueue.queueUrl,
        DLQ_QUEUE_URL: emailDlq.queueUrl,
        NOTIFICATION_TOPIC_ARN: notificationTopic?.topicArn || '',
        UNSUBSCRIBE_ENCRYPTION_KEY: props.unsubscribeEncryptionKey || '',
        UNSUBSCRIBE_BASE_URL: props.unsubscribeBaseUrl || '',
        UNSUBSCRIBE_ENDPOINT_URL: props.unsubscribeEndpointUrl || '',
        UNSUBSCRIBE_MAILTO: props.unsubscribeMailto || '',
      },
    });

    this.campaignProcessorFunction = new lambda.Function(this, 'CampaignProcessor', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'campaign_processor.handler',
      code: lambdaCode,
      layers: [cryptographyLayer],
      timeout: Duration.minutes(props.lambdaTimeout),
      memorySize: props.lambdaMemory,
      logRetention: logs.RetentionDays.ONE_WEEK,
      environment: {
        CAMPAIGN_TABLE_NAME: this.campaignTable.tableName,
        CAMPAIGN_BUCKET_NAME: this.campaignBucket.bucketName,
        EMAIL_QUEUE_URL: this.emailQueue.queueUrl,
        NOTIFICATION_TOPIC_ARN: notificationTopic?.topicArn || '',
        EMAIL_SENDER_FUNCTION_NAME: this.emailSenderFunction.functionName,
        UNSUBSCRIBE_ENCRYPTION_KEY: props.unsubscribeEncryptionKey || '',
        UNSUBSCRIBE_BASE_URL: props.unsubscribeBaseUrl || '',
        UNSUBSCRIBE_ENDPOINT_URL: props.unsubscribeEndpointUrl || '',
        UNSUBSCRIBE_MAILTO: props.unsubscribeMailto || '',
      },
    });

    // Grant specific permissions to Campaign Processor (least privilege)
    this.campaignProcessorFunction.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['dynamodb:Query', 'dynamodb:UpdateItem'],
        resources: [this.campaignTable.tableArn],
      }),
    );
    
    this.campaignProcessorFunction.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['s3:GetObject'],
        resources: [`${this.campaignBucket.bucketArn}/*`],
      }),
    );
    
    this.emailQueue.grantSendMessages(this.campaignProcessorFunction);

    if (notificationTopic) {
      notificationTopic.grantPublish(this.campaignProcessorFunction);
    }

    // Grant permission to create and delete EventBridge rules
    this.campaignProcessorFunction.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          'events:PutRule',
          'events:DeleteRule',
          'events:PutTargets',
          'events:RemoveTargets',
          'events:DescribeRule',
        ],
        resources: [
          `arn:aws:events:${cdk.Stack.of(this).region}:${cdk.Stack.of(this).account}:rule/ses-campaign-*`,
        ],
      }),
    );

    // Grant permission for EventBridge to invoke this Lambda
    this.campaignProcessorFunction.addPermission('EventBridgeInvoke', {
      principal: new iam.ServicePrincipal('events.amazonaws.com'),
      action: 'lambda:InvokeFunction',
      sourceArn: `arn:aws:events:${cdk.Stack.of(this).region}:${cdk.Stack.of(this).account}:rule/ses-campaign-*`,
    });

    NagSuppressions.addResourceSuppressions(
      this.campaignProcessorFunction,
      [
        {
          id: 'AwsSolutions-IAM5',
          reason: 'Lambda needs wildcard permissions for EventBridge rules to manage scheduled campaigns dynamically.',
          appliesTo: [
            `Resource::arn:aws:events:${cdk.Stack.of(this).region}:${cdk.Stack.of(this).account}:rule/ses-campaign-*`,
          ],
        },
      ],
      true,
    );

    // Grant specific permissions to Email Sender (least privilege)
    this.emailSenderFunction.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['dynamodb:Query', 'dynamodb:UpdateItem'],
        resources: [this.campaignTable.tableArn],
      }),
    );

    if (notificationTopic) {
      notificationTopic.grantPublish(this.emailSenderFunction);
    }

    // Grant SQS permissions for manual message handling
    this.emailSenderFunction.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['sqs:DeleteMessage', 'sqs:SendMessage', 'sqs:ReceiveMessage'],
        resources: [this.emailQueue.queueArn, emailDlq.queueArn],
      }),
    );

    // Grant SES permissions
    this.emailSenderFunction.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          'ses:SendEmail',
          'ses:SendRawEmail',
          'ses:SendTemplatedEmail',
        ],
        resources: ['*'], // SES doesn't support resource-level permissions
      }),
    );

    NagSuppressions.addResourceSuppressions(
      this.emailSenderFunction,
      [
        {
          id: 'AwsSolutions-IAM5',
          reason: 'SES actions do not support resource-level permissions and require wildcard.',
          appliesTo: ['Resource::*'],
        },
      ],
      true,
    );

    // Add SQS trigger to Email Sender
    // Batch size of 20 means Lambda processes up to 20 emails per invocation
    // Manual message handling - no automatic DLQ or batch failures
    this.emailSenderFunction.addEventSource(
      new lambdaEventSources.SqsEventSource(this.emailQueue, {
        batchSize: 20, // Process 20 messages per Lambda invocation
        maxBatchingWindow: Duration.seconds(1), // Required to be > 0 when batch size > 10
      }),
    );

    //////// LAMBDA FUNCTION: CAMPAIGN SCHEDULER (API) ////////

    const campaignSchedulerFunction = new lambda.Function(this, 'CampaignScheduler', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'campaign_scheduler.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambda')),
      timeout: Duration.minutes(2),
      memorySize: 512,
      logRetention: logs.RetentionDays.ONE_WEEK,
      environment: {
        CAMPAIGN_TABLE_NAME: this.campaignTable.tableName,
        CAMPAIGN_BUCKET_NAME: this.campaignBucket.bucketName,
        CAMPAIGN_PROCESSOR_ARN: this.campaignProcessorFunction.functionArn,
      },
    });

    // Grant specific permissions to Campaign Scheduler (least privilege)
    campaignSchedulerFunction.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['dynamodb:PutItem', 'dynamodb:Query', 'dynamodb:UpdateItem', 'dynamodb:DeleteItem'],
        resources: [this.campaignTable.tableArn, `${this.campaignTable.tableArn}/index/*`],
      }),
    );
    
    campaignSchedulerFunction.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['s3:PutObject', 's3:GetObject', 's3:DeleteObject'],
        resources: [`${this.campaignBucket.bucketArn}/*`],
      }),
    );

    // Grant permission to create EventBridge rules
    campaignSchedulerFunction.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          'events:PutRule',
          'events:PutTargets',
          'events:DescribeRule',
        ],
        resources: [
          `arn:aws:events:${cdk.Stack.of(this).region}:${cdk.Stack.of(this).account}:rule/ses-campaign-*`,
        ],
      }),
    );

    // Grant permission to pass IAM role to EventBridge
    campaignSchedulerFunction.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['iam:PassRole'],
        resources: [this.campaignProcessorFunction.role!.roleArn],
      }),
    );

    // Grant permission to invoke campaign processor
    this.campaignProcessorFunction.grantInvoke(campaignSchedulerFunction);

    NagSuppressions.addResourceSuppressions(
      campaignSchedulerFunction,
      [
        {
          id: 'AwsSolutions-IAM5',
          reason: 'Lambda needs wildcard permissions for EventBridge rules to create scheduled campaigns dynamically.',
          appliesTo: [
            `Resource::arn:aws:events:${cdk.Stack.of(this).region}:${cdk.Stack.of(this).account}:rule/ses-campaign-*`,
          ],
        },
      ],
      true,
    );

    //////// LAMBDA FUNCTION: TTL CLEANUP ////////

    const ttlCleanupFunction = new lambda.Function(this, 'TTLCleanup', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'ttl_cleanup.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambda')),
      timeout: Duration.minutes(2),
      memorySize: 256,
      logRetention: logs.RetentionDays.ONE_WEEK,
      description: 'Cleans up EventBridge rules and S3 files when campaigns expire (TTL)',
    });

    // Grant permissions to delete EventBridge rules
    ttlCleanupFunction.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          'events:RemoveTargets',
          'events:DeleteRule',
        ],
        resources: [
          `arn:aws:events:${cdk.Stack.of(this).region}:${cdk.Stack.of(this).account}:rule/ses-campaign-*`,
        ],
      }),
    );

    // Grant permissions to delete S3 objects
    ttlCleanupFunction.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['s3:DeleteObject'],
        resources: [`${this.campaignBucket.bucketArn}/*`],
      }),
    );

    // Add DynamoDB Stream trigger
    ttlCleanupFunction.addEventSource(
      new lambdaEventSources.DynamoEventSource(this.campaignTable, {
        startingPosition: lambda.StartingPosition.LATEST,
        batchSize: 10,
        bisectBatchOnError: true,
        retryAttempts: 2,
      }),
    );

    NagSuppressions.addResourceSuppressions(
      ttlCleanupFunction,
      [
        {
          id: 'AwsSolutions-IAM5',
          reason: 'Lambda needs wildcard permissions to clean up dynamically created EventBridge rules.',
          appliesTo: [
            `Resource::arn:aws:events:${cdk.Stack.of(this).region}:${cdk.Stack.of(this).account}:rule/ses-campaign-*`,
          ],
        },
      ],
      true,
    );

    //////// OUTPUTS ////////

    new CfnOutput(this, 'CampaignBucketName', {
      value: this.campaignBucket.bucketName,
      description: 'S3 bucket for campaign CSV files',
    });

    new CfnOutput(this, 'CampaignTableName', {
      value: this.campaignTable.tableName,
      description: 'DynamoDB table for campaign metadata',
    });

    new CfnOutput(this, 'EmailQueueUrl', {
      value: this.emailQueue.queueUrl,
      description: 'SQS queue URL for email processing',
    });

    new CfnOutput(this, 'CampaignProcessorFunctionName', {
      value: this.campaignProcessorFunction.functionName,
      description: 'Lambda function for campaign processing',
    });

    new CfnOutput(this, 'EmailSenderFunctionName', {
      value: this.emailSenderFunction.functionName,
      description: 'Lambda function for email sending',
    });

    new CfnOutput(this, 'CampaignSchedulerFunctionName', {
      value: campaignSchedulerFunction.functionName,
      description: 'Lambda function for scheduling campaigns (called from Amazon SES Campaign Manager)',
    });


    if (notificationTopic) {
      new CfnOutput(this, 'NotificationTopicArn', {
        value: notificationTopic.topicArn,
        description: 'SNS topic for campaign notifications',
      });
    }

    new CfnOutput(this, 'DeploymentRegion', {
      value: cdk.Stack.of(this).region,
      description: 'AWS region where the stack is deployed',
    });
  }
}
