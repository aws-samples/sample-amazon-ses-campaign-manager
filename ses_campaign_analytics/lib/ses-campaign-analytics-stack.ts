import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { Duration, RemovalPolicy, CfnOutput, Tags } from 'aws-cdk-lib';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3Notifications from 'aws-cdk-lib/aws-s3-notifications';
import * as firehose from 'aws-cdk-lib/aws-kinesisfirehose';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as glue from 'aws-cdk-lib/aws-glue';
import * as athena from 'aws-cdk-lib/aws-athena';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as subscriptions from 'aws-cdk-lib/aws-sns-subscriptions';
import * as ses from 'aws-cdk-lib/aws-ses';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import { NagSuppressions } from 'cdk-nag';
import * as path from 'path';

export interface SesCampaignAnalyticsStackProps extends cdk.StackProps {
  existingConfigurationSetName?: string;
  refreshScheduleCron: string;
  dataRetentionDays: number;
  enableNotifications: boolean;
  notificationEmail?: string;
  firehoseBufferSizeMB: number;
  firehoseBufferIntervalSeconds: number;
  athenaQueryResultsRetentionDays: number;
  processedDataTransitionToIADays: number;
  lambdaTimeoutMinutes: number;
  lambdaMemoryMB: number;
  athenaQueryScanLimitGB: number;
}

export class SesCampaignAnalyticsStack extends cdk.Stack {
  public readonly rawDataBucket: s3.Bucket;
  public readonly processedDataBucket: s3.Bucket;
  public readonly athenaResultsBucket: s3.Bucket;
  public readonly glueDatabase: glue.CfnDatabase;
  public readonly workGroup: athena.CfnWorkGroup;
  public readonly configurationSet: ses.CfnConfigurationSet;

  constructor(scope: Construct, id: string, props: SesCampaignAnalyticsStackProps) {
    super(scope, id, {
      ...props,
      description: 'SES Campaign Analytics - Kinesis Firehose, Athena, and Glue for detailed email campaign metrics and reporting',
    });

    // Add cost allocation tags for tracking expenses
    Tags.of(this).add('Project', 'SES-Campaign-Analytics');
    Tags.of(this).add('ManagedBy', 'CDK');
    Tags.of(this).add('Environment', 'Production');

    //////// SES CONFIGURATION SET ////////
    
    // Use existing configuration set if provided, otherwise create a new one
    let configSetName: string;
    
    if (props.existingConfigurationSetName && props.existingConfigurationSetName.trim() !== '') {
      // Use existing configuration set
      configSetName = props.existingConfigurationSetName;
      console.log(`Using existing SES Configuration Set: ${configSetName}`);
    } else {
      // Create new configuration set
      this.configurationSet = new ses.CfnConfigurationSet(this, 'SesConfigurationSet', {
        name: `ses-analytics-${cdk.Stack.of(this).account}`,
      });
      configSetName = this.configurationSet.name!;
    }

    //////// S3 BUCKETS FOR DATA STORAGE ////////
    
    // Raw SES events from Firehose
    this.rawDataBucket = new s3.Bucket(this, 'SesRawDataBucket', {
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      removalPolicy: RemovalPolicy.RETAIN,
      enforceSSL: true,
      lifecycleRules: [
        {
          id: 'DeleteOldRawData',
          enabled: true,
          expiration: Duration.days(props.dataRetentionDays),
        },
        {
          id: 'TransitionToGlacier',
          enabled: true,
          transitions: [
            {
              storageClass: s3.StorageClass.GLACIER,
              transitionAfter: Duration.days(Math.floor(props.dataRetentionDays / 3)),
            },
          ],
        },
      ],
    });

    // Processed/transformed data
    this.processedDataBucket = new s3.Bucket(this, 'SesProcessedDataBucket', {
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      removalPolicy: RemovalPolicy.RETAIN,
      enforceSSL: true,
      lifecycleRules: [
        {
          id: 'TransitionToIA',
          enabled: true,
          transitions: [
            {
              storageClass: s3.StorageClass.INFREQUENT_ACCESS,
              transitionAfter: Duration.days(props.processedDataTransitionToIADays),
            },
          ],
        },
      ],
    });

    // Athena query results
    this.athenaResultsBucket = new s3.Bucket(this, 'AthenaResultsBucket', {
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      removalPolicy: RemovalPolicy.RETAIN,
      enforceSSL: true,
      lifecycleRules: [
        {
          id: 'DeleteOldQueryResults',
          enabled: true,
          expiration: Duration.days(props.athenaQueryResultsRetentionDays),
        },
      ],
    });

    //////// AWS GLUE DATABASE AND TABLES ////////
    
    this.glueDatabase = new glue.CfnDatabase(this, 'SesEventDatabase', {
      catalogId: cdk.Stack.of(this).account,
      databaseInput: {
        name: `ses_analytics_db_${cdk.Stack.of(this).account}`,
        description: 'Database for SES campaign analytics events and materialized views',
      },
    });

    // Create Athena Named Query for table creation (following CloudFormation pattern)
    const createTableQuery = new athena.CfnNamedQuery(this, 'CreateSesEventsTable', {
      database: this.glueDatabase.ref,
      description: 'Create table for SES events',
      name: 'create_ses_events_table',
      queryString: `CREATE EXTERNAL TABLE IF NOT EXISTS ses_events_raw (
  eventType string,
  mail struct<
    timestamp: string,
    source: string,
    sourceArn: string,
    sendingAccountId: string,
    messageId: string,
    destination: array<string>,
    headersTruncated: boolean,
    headers: array<struct<name: string, value: string>>,
    commonHeaders: struct<\`from\`: array<string>, \`to\`: array<string>, messageId: string, subject: string>,
    tags: struct<
      campaign_id: array<string>,
      campaign_name: array<string>
    >
  >,
  send map<string,string>,
  delivery struct<
    timestamp: string,
    processingTimeMillis: bigint,
    recipients: array<string>,
    smtpResponse: string,
    reportingMTA: string
  >,
  open struct<
    ipAddress: string,
    timestamp: string,
    userAgent: string
  >,
  click struct<
    ipAddress: string,
    link: string,
    linkTags: map<string,array<string>>,
    timestamp: string,
    userAgent: string
  >,
  bounce struct<
    bounceType: string,
    bounceSubType: string,
    bouncedRecipients: array<struct<
      emailAddress: string,
      action: string,
      status: string,
      diagnosticCode: string
    >>,
    timestamp: string,
    feedbackId: string,
    reportingMTA: string
  >,
  complaint struct<
    complainedRecipients: array<struct<
      emailAddress: string
    >>,
    timestamp: string,
    feedbackId: string,
    userAgent: string,
    complaintFeedbackType: string,
    arrivalDate: string
  >,
  reject struct<
    reason: string
  >,
  renderingFailure struct<
    errorMessage: string,
    templateName: string
  >
)
PARTITIONED BY (ingest_timestamp timestamp)
STORED AS parquet
LOCATION "s3://${this.rawDataBucket.bucketName}/events"
TBLPROPERTIES (
  "parquet.compression"="SNAPPY",
  "projection.enabled"="false"
)`,
    });

    // Lambda to execute the named query and create the table
    const createTableLambda = new lambda.Function(this, 'CreateTableLambda', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'createTable.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambda')),
      timeout: Duration.minutes(10), // Increased from 5 to 10 minutes for Athena query completion
      environment: {
        NAMED_QUERY_ID: createTableQuery.attrNamedQueryId,
        DATABASE_NAME: this.glueDatabase.ref,
        OUTPUT_LOCATION: `s3://${this.athenaResultsBucket.bucketName}/table-creation/`,
      },
    });

    this.athenaResultsBucket.grantReadWrite(createTableLambda);
    
    createTableLambda.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          'athena:GetNamedQuery',
          'athena:StartQueryExecution',
          'athena:GetQueryExecution',
        ],
        resources: ['*'],
      }),
    );

    createTableLambda.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          'glue:GetDatabase',
          'glue:GetTable',
          'glue:CreateTable',
        ],
        resources: [
          `arn:aws:glue:${cdk.Stack.of(this).region}:${cdk.Stack.of(this).account}:catalog`,
          `arn:aws:glue:${cdk.Stack.of(this).region}:${cdk.Stack.of(this).account}:database/${this.glueDatabase.ref}`,
          `arn:aws:glue:${cdk.Stack.of(this).region}:${cdk.Stack.of(this).account}:table/${this.glueDatabase.ref}/*`,
        ],
      }),
    );

    // Suppress CDK Nag warning for Glue table wildcard permissions
    NagSuppressions.addResourceSuppressions(
      createTableLambda,
      [
        {
          id: 'AwsSolutions-IAM5',
          reason: 'Lambda needs wildcard permissions to access Glue table metadata for Athena query execution that creates tables.',
        },
      ],
      true, // applyToChildren - applies to the Lambda's role and policies
    );

    // Custom resource to trigger table creation
    const tableCreationTrigger = new cdk.CustomResource(this, 'TableCreationTrigger', {
      serviceToken: createTableLambda.functionArn,
    });

    tableCreationTrigger.node.addDependency(this.glueDatabase);
    tableCreationTrigger.node.addDependency(createTableQuery);

    // Reference for Firehose - use the table name directly
    const rawEventsTableName = 'ses_events_raw';

    // Materialized view table for campaign metrics
    const campaignMetricsTable = new glue.CfnTable(this, 'CampaignMetricsTable', {
      catalogId: cdk.Stack.of(this).account,
      databaseName: this.glueDatabase.ref,
      tableInput: {
        name: 'campaign_metrics_daily',
        description: 'Materialized view of daily campaign metrics',
        tableType: 'EXTERNAL_TABLE',
        parameters: {
          'projection.enabled': 'true',
          'projection.date.type': 'date',
          'projection.date.range': '2024-01-01,NOW',
          'projection.date.format': 'yyyy-MM-dd',
          'storage.location.template': `s3://${this.processedDataBucket.bucketName}/materialized-views/campaign_metrics_daily/date=\${date}`,
        },
        partitionKeys: [
          { name: 'date', type: 'string' },
        ],
        storageDescriptor: {
          columns: [
            { name: 'campaign_id', type: 'string' },
            { name: 'campaign_name', type: 'string' },
            { name: 'emails_sent', type: 'bigint' },
            { name: 'emails_delivered', type: 'bigint' },
            { name: 'emails_opened', type: 'bigint' },
            { name: 'emails_clicked', type: 'bigint' },
            { name: 'hard_bounces', type: 'bigint' },
            { name: 'soft_bounces', type: 'bigint' },
            { name: 'complaints', type: 'bigint' },
            { name: 'rejects', type: 'bigint' },
            { name: 'rendering_failures', type: 'bigint' },
            { name: 'delivery_rate', type: 'double' },
            { name: 'open_rate', type: 'double' },
            { name: 'click_rate', type: 'double' },
            { name: 'hard_bounce_rate', type: 'double' },
            { name: 'complaint_rate', type: 'double' },
            { name: 'rendering_failure_rate', type: 'double' },
            { name: 'unique_recipients', type: 'bigint' },
            { name: 'avg_delivery_time_ms', type: 'double' },
            { name: 'from_address', type: 'string' },
            { name: 'sample_subject', type: 'string' },
          ],
          location: `s3://${this.processedDataBucket.bucketName}/materialized-views/campaign_metrics_daily/`,
          inputFormat: 'org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat',
          outputFormat: 'org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat',
          serdeInfo: {
            serializationLibrary: 'org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe',
            parameters: {
              'serialization.format': '1',
            },
          },
        },
      },
    });

    //////// ATHENA WORKGROUP ////////
    
    this.workGroup = new athena.CfnWorkGroup(this, 'SesAnalyticsWorkGroup', {
      name: `ses-analytics-wg-${cdk.Stack.of(this).account}`,
      description: 'Workgroup for SES campaign analytics queries',
      recursiveDeleteOption: true, // Enable recursive deletion to clean up queries
      workGroupConfiguration: {
        resultConfiguration: {
          outputLocation: `s3://${this.athenaResultsBucket.bucketName}/query-results/`,
          encryptionConfiguration: {
            encryptionOption: 'SSE_S3',
          },
        },
        enforceWorkGroupConfiguration: true,
        publishCloudWatchMetricsEnabled: true,
        bytesScannedCutoffPerQuery: props.athenaQueryScanLimitGB * 1024 * 1024 * 1024,
        engineVersion: {
          selectedEngineVersion: 'Athena engine version 3',
        },
      },
    });
    
    // Apply removal policy to allow deletion
    this.workGroup.applyRemovalPolicy(RemovalPolicy.DESTROY);

    //////// IAM ROLES FOR KINESIS FIREHOSE ////////
    
    // Role for Firehose service itself
    const firehoseRole = new iam.Role(this, 'FirehoseRole', {
      assumedBy: new iam.ServicePrincipal('firehose.amazonaws.com'),
      description: 'Role for Kinesis Firehose to write SES events to S3 and convert to Parquet',
    });

    this.rawDataBucket.grantWrite(firehoseRole);
    
    // Separate role for SES to put records into Firehose
    const sesFirehoseRole = new iam.Role(this, 'SesFirehoseRole', {
      assumedBy: new iam.ServicePrincipal('ses.amazonaws.com'),
      description: 'Role for SES to put records into Kinesis Firehose',
    });

    // CloudWatch Logs for Firehose
    const firehoseLogGroup = new logs.LogGroup(this, 'FirehoseLogGroup', {
      logGroupName: `/aws/kinesisfirehose/ses-analytics-${cdk.Stack.of(this).account}`,
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: RemovalPolicy.DESTROY,
    });

    const firehoseLogStream = new logs.LogStream(this, 'FirehoseLogStream', {
      logGroup: firehoseLogGroup,
      logStreamName: 'S3Delivery',
    });

    firehoseLogGroup.grantWrite(firehoseRole);

    //////// KINESIS DATA FIREHOSE DELIVERY STREAM ////////
    
    const deliveryStream = new firehose.CfnDeliveryStream(this, 'SesEventsDeliveryStream', {
      deliveryStreamName: `ses-analytics-stream-${cdk.Stack.of(this).account}`,
      deliveryStreamType: 'DirectPut',
      extendedS3DestinationConfiguration: {
        bucketArn: this.rawDataBucket.bucketArn,
        roleArn: firehoseRole.roleArn,
        prefix: 'events/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/hour=!{timestamp:HH}/',
        errorOutputPrefix: 'errors/!{firehose:error-output-type}/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/',
        bufferingHints: {
          sizeInMBs: Math.max(props.firehoseBufferSizeMB, 64), // Minimum 64 MB when data format conversion is enabled
          intervalInSeconds: props.firehoseBufferIntervalSeconds,
        },
        compressionFormat: 'UNCOMPRESSED',
        dataFormatConversionConfiguration: {
          enabled: true,
          schemaConfiguration: {
            databaseName: this.glueDatabase.ref,
            tableName: rawEventsTableName,
            region: cdk.Stack.of(this).region,
            roleArn: firehoseRole.roleArn,
          },
          inputFormatConfiguration: {
            deserializer: {
              openXJsonSerDe: {},
            },
          },
          outputFormatConfiguration: {
            serializer: {
              parquetSerDe: {
                compression: 'SNAPPY',
              },
            },
          },
        },
        cloudWatchLoggingOptions: {
          enabled: true,
          logGroupName: firehoseLogGroup.logGroupName,
          logStreamName: firehoseLogStream.logStreamName,
        },
      },
    });

    // Grant Glue permissions to Firehose role
    const gluePolicy = new iam.Policy(this, 'FirehoseGluePolicy', {
      statements: [
        new iam.PolicyStatement({
          actions: [
            'glue:GetTable',
            'glue:GetTableVersion',
            'glue:GetTableVersions',
          ],
          resources: [
            `arn:aws:glue:${cdk.Stack.of(this).region}:${cdk.Stack.of(this).account}:catalog`,
            `arn:aws:glue:${cdk.Stack.of(this).region}:${cdk.Stack.of(this).account}:database/${this.glueDatabase.ref}`,
            `arn:aws:glue:${cdk.Stack.of(this).region}:${cdk.Stack.of(this).account}:table/${this.glueDatabase.ref}/*`,
          ],
        }),
      ],
    });
    
    gluePolicy.attachToRole(firehoseRole);
    
    // Ensure delivery stream is created after the policy is attached and table is created
    deliveryStream.node.addDependency(gluePolicy);
    deliveryStream.node.addDependency(tableCreationTrigger);
    
    // Grant SES role permission to put records to Firehose
    sesFirehoseRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['firehose:PutRecord', 'firehose:PutRecordBatch', 'firehose:DescribeDeliveryStream'],
        resources: [deliveryStream.attrArn],
      }),
    );
    
    // Suppress CDK Nag warnings for Firehose role Glue permissions
    NagSuppressions.addResourceSuppressions(
      firehoseRole,
      [
        {
          id: 'AwsSolutions-IAM5',
          reason: 'Glue catalog operations require wildcard permissions for table access within the database. This allows Firehose to access all tables for schema conversion during Parquet transformation.',
          appliesTo: [
            `Resource::arn:aws:glue:${cdk.Stack.of(this).region}:${cdk.Stack.of(this).account}:table/<SesEventDatabase>/*`
          ]
        }
      ],
      true // applyToChildren
    );

    //////// SES EVENT DESTINATION ////////
    
    const sesEventDestination = new ses.CfnConfigurationSetEventDestination(this, 'SesEventDestination', {
      configurationSetName: configSetName,
      eventDestination: {
        name: 'firehose-destination',
        enabled: true,
        matchingEventTypes: ['send', 'reject', 'bounce', 'complaint', 'delivery', 'open', 'click', 'renderingFailure'],
        kinesisFirehoseDestination: {
          deliveryStreamArn: deliveryStream.attrArn,
          iamRoleArn: sesFirehoseRole.roleArn,
        },
      },
    });
    
    // Ensure SES event destination is created after all policies are attached
    sesEventDestination.node.addDependency(deliveryStream);
    sesEventDestination.node.addDependency(sesFirehoseRole);

    //////// SNS TOPIC FOR NOTIFICATIONS ////////
    
    let notificationTopic: sns.Topic | undefined;
    
    if (props.enableNotifications) {
      notificationTopic = new sns.Topic(this, 'AnalyticsNotificationTopic', {
        displayName: 'SES Campaign Analytics Notifications',
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

    //////// LAMBDA FOR MATERIALIZED VIEW REFRESH ////////
    
    const refreshLambda = new lambda.Function(this, 'MaterializedViewRefresh', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'materializedViewRefresh.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambda')),
      timeout: Duration.minutes(props.lambdaTimeoutMinutes),
      memorySize: props.lambdaMemoryMB,
      logRetention: logs.RetentionDays.ONE_WEEK,
      environment: {
        DATABASE_NAME: this.glueDatabase.ref,
        RAW_EVENTS_TABLE: rawEventsTableName,
        CAMPAIGN_METRICS_TABLE: campaignMetricsTable.ref,
        PROCESSED_BUCKET: this.processedDataBucket.bucketName,
        ATHENA_OUTPUT_LOCATION: `s3://${this.athenaResultsBucket.bucketName}/refresh-results/`,
        WORKGROUP_NAME: this.workGroup.ref,
        NOTIFICATION_TOPIC_ARN: notificationTopic?.topicArn || '',
      },
    });

    // Grant permissions to refresh Lambda
    this.processedDataBucket.grantReadWrite(refreshLambda);
    this.rawDataBucket.grantRead(refreshLambda);
    this.athenaResultsBucket.grantReadWrite(refreshLambda);

    refreshLambda.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          'athena:StartQueryExecution',
          'athena:GetQueryExecution',
          'athena:GetQueryResults',
          'athena:StopQueryExecution',
        ],
        resources: [
          `arn:aws:athena:${cdk.Stack.of(this).region}:${cdk.Stack.of(this).account}:workgroup/${this.workGroup.ref}`,
        ],
      }),
    );

    refreshLambda.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          'glue:GetDatabase',
          'glue:GetTable',
          'glue:GetPartitions',
          'glue:CreatePartition',
          'glue:UpdatePartition',
          'glue:DeletePartition',
          'glue:BatchCreatePartition',
          'glue:BatchDeletePartition',
        ],
        resources: [
          `arn:aws:glue:${cdk.Stack.of(this).region}:${cdk.Stack.of(this).account}:catalog`,
          `arn:aws:glue:${cdk.Stack.of(this).region}:${cdk.Stack.of(this).account}:database/${this.glueDatabase.ref}`,
          `arn:aws:glue:${cdk.Stack.of(this).region}:${cdk.Stack.of(this).account}:table/${this.glueDatabase.ref}/*`,
        ],
      }),
    );

    if (notificationTopic) {
      notificationTopic.grantPublish(refreshLambda);
    }

    //////// LAMBDA FOR ATHENA PARTITION MANAGEMENT ////////
    
    // Lambda to automatically add partitions when new data arrives in S3
    const athenaPartitionLambda = new lambda.Function(this, 'AthenaPartitionLambda', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'partitionManager.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambda')),
      timeout: Duration.minutes(5),
      memorySize: 256,
      environment: {
        DATABASE_NAME: this.glueDatabase.ref,
        TABLE_NAME: rawEventsTableName,
        OUTPUT_LOCATION: `s3://${this.athenaResultsBucket.bucketName}/partition-mgmt/`,
      },
    });

    // Grant permissions to partition Lambda
    this.rawDataBucket.grantRead(athenaPartitionLambda);
    this.athenaResultsBucket.grantReadWrite(athenaPartitionLambda);

    athenaPartitionLambda.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['athena:StartQueryExecution'],
        resources: [`arn:aws:athena:${cdk.Stack.of(this).region}:${cdk.Stack.of(this).account}:workgroup/*`],
      }),
    );

    athenaPartitionLambda.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          'glue:GetDatabase',
          'glue:GetTable',
          'glue:CreatePartition',
          'glue:BatchCreatePartition',
        ],
        resources: [
          `arn:aws:glue:${cdk.Stack.of(this).region}:${cdk.Stack.of(this).account}:catalog`,
          `arn:aws:glue:${cdk.Stack.of(this).region}:${cdk.Stack.of(this).account}:database/${this.glueDatabase.ref}`,
          `arn:aws:glue:${cdk.Stack.of(this).region}:${cdk.Stack.of(this).account}:table/${this.glueDatabase.ref}/*`,
        ],
      }),
    );

    // Grant S3 permission to invoke Lambda
    athenaPartitionLambda.addPermission('S3InvokeLambda', {
      principal: new iam.ServicePrincipal('s3.amazonaws.com'),
      action: 'lambda:InvokeFunction',
      sourceAccount: cdk.Stack.of(this).account,
      sourceArn: this.rawDataBucket.bucketArn,
    });

    // Add S3 notification to trigger Lambda when new objects are created
    this.rawDataBucket.addEventNotification(
      s3.EventType.OBJECT_CREATED,
      new s3Notifications.LambdaDestination(athenaPartitionLambda),
      { prefix: 'events/' }
    );

    //////// DYNAMODB TABLE FOR CAMPAIGN METADATA ////////
    
    const campaignMetadataTable = new dynamodb.Table(this, 'CampaignMetadataTable', {
      // Let CDK generate unique table name to avoid conflicts on redeploy
      partitionKey: { name: 'campaign_id', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      pointInTimeRecovery: true,
      removalPolicy: RemovalPolicy.DESTROY, // Changed to DESTROY for easier redeployment
      timeToLiveAttribute: 'ttl',
    });

    // Grant Lambda read/write access to DynamoDB (for potential future automation)
    campaignMetadataTable.grantReadWriteData(refreshLambda);

    //////// EVENTBRIDGE RULE FOR SCHEDULED REFRESH ////////
    
    const refreshRule = new events.Rule(this, 'DailyRefreshRule', {
      schedule: events.Schedule.expression(props.refreshScheduleCron),
      description: 'Trigger daily refresh of SES campaign materialized views',
    });

    refreshRule.addTarget(new targets.LambdaFunction(refreshLambda));

    //////// OUTPUTS ////////
    
    new CfnOutput(this, 'SesConfigurationSetName', {
      value: configSetName,
      description: 'SES Configuration Set name - use this when sending emails',
    });

    new CfnOutput(this, 'RawDataBucketName', {
      value: this.rawDataBucket.bucketName,
      description: 'S3 bucket for raw SES events',
    });

    new CfnOutput(this, 'ProcessedDataBucketName', {
      value: this.processedDataBucket.bucketName,
      description: 'S3 bucket for processed data and materialized views',
    });

    new CfnOutput(this, 'AthenaResultsBucketName', {
      value: this.athenaResultsBucket.bucketName,
      description: 'S3 bucket for Athena query results',
    });

    new CfnOutput(this, 'GlueDatabaseName', {
      value: this.glueDatabase.ref,
      description: 'Glue database name for SES analytics',
    });

    new CfnOutput(this, 'AthenaWorkGroupName', {
      value: this.workGroup.ref,
      description: 'Athena workgroup for campaign analytics queries',
    });

    new CfnOutput(this, 'FirehoseDeliveryStreamName', {
      value: deliveryStream.ref,
      description: 'Kinesis Firehose delivery stream name',
    });

    new CfnOutput(this, 'FirehoseDeliveryStreamArn', {
      value: deliveryStream.attrArn,
      description: 'Kinesis Firehose delivery stream ARN',
    });

    new CfnOutput(this, 'RefreshLambdaName', {
      value: refreshLambda.functionName,
      description: 'Lambda function for materialized view refresh',
    });

    new CfnOutput(this, 'CampaignMetadataTableName', {
      value: campaignMetadataTable.tableName,
      description: 'DynamoDB table for campaign metadata storage',
    });

    if (notificationTopic) {
      new CfnOutput(this, 'NotificationTopicArn', {
        value: notificationTopic.topicArn,
        description: 'SNS topic for analytics notifications',
      });
    }

    new CfnOutput(this, 'QueryExampleCampaignSummary', {
      value: `SELECT * FROM ${this.glueDatabase.ref}.campaign_metrics_daily WHERE date >= current_date - interval '30' day ORDER BY date DESC`,
      description: 'Example Athena query for campaign summary',
    });
  }
}
