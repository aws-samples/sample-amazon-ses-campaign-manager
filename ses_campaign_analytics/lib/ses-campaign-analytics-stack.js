"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.SesCampaignAnalyticsStack = void 0;
const cdk = require("aws-cdk-lib");
const aws_cdk_lib_1 = require("aws-cdk-lib");
const s3 = require("aws-cdk-lib/aws-s3");
const s3Notifications = require("aws-cdk-lib/aws-s3-notifications");
const firehose = require("aws-cdk-lib/aws-kinesisfirehose");
const iam = require("aws-cdk-lib/aws-iam");
const glue = require("aws-cdk-lib/aws-glue");
const athena = require("aws-cdk-lib/aws-athena");
const lambda = require("aws-cdk-lib/aws-lambda");
const events = require("aws-cdk-lib/aws-events");
const targets = require("aws-cdk-lib/aws-events-targets");
const logs = require("aws-cdk-lib/aws-logs");
const sns = require("aws-cdk-lib/aws-sns");
const subscriptions = require("aws-cdk-lib/aws-sns-subscriptions");
const ses = require("aws-cdk-lib/aws-ses");
const dynamodb = require("aws-cdk-lib/aws-dynamodb");
const cdk_nag_1 = require("cdk-nag");
const path = require("path");
class SesCampaignAnalyticsStack extends cdk.Stack {
    constructor(scope, id, props) {
        super(scope, id, {
            ...props,
            description: 'SES Campaign Analytics - Kinesis Firehose, Athena, and Glue for detailed email campaign metrics and reporting',
        });
        // Add cost allocation tags for tracking expenses
        aws_cdk_lib_1.Tags.of(this).add('Project', 'SES-Campaign-Analytics');
        aws_cdk_lib_1.Tags.of(this).add('ManagedBy', 'CDK');
        aws_cdk_lib_1.Tags.of(this).add('Environment', 'Production');
        //////// SES CONFIGURATION SET ////////
        // Use existing configuration set if provided, otherwise create a new one
        let configSetName;
        if (props.existingConfigurationSetName && props.existingConfigurationSetName.trim() !== '') {
            // Use existing configuration set
            configSetName = props.existingConfigurationSetName;
            console.log(`Using existing SES Configuration Set: ${configSetName}`);
        }
        else {
            // Create new configuration set
            this.configurationSet = new ses.CfnConfigurationSet(this, 'SesConfigurationSet', {
                name: `ses-analytics-${cdk.Stack.of(this).account}`,
            });
            configSetName = this.configurationSet.name;
        }
        //////// S3 BUCKETS FOR DATA STORAGE ////////
        // Raw SES events from Firehose
        this.rawDataBucket = new s3.Bucket(this, 'SesRawDataBucket', {
            encryption: s3.BucketEncryption.S3_MANAGED,
            blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
            removalPolicy: aws_cdk_lib_1.RemovalPolicy.RETAIN,
            enforceSSL: true,
            lifecycleRules: [
                {
                    id: 'DeleteOldRawData',
                    enabled: true,
                    expiration: aws_cdk_lib_1.Duration.days(props.dataRetentionDays),
                },
                {
                    id: 'TransitionToGlacier',
                    enabled: true,
                    transitions: [
                        {
                            storageClass: s3.StorageClass.GLACIER,
                            transitionAfter: aws_cdk_lib_1.Duration.days(Math.floor(props.dataRetentionDays / 3)),
                        },
                    ],
                },
            ],
        });
        // Processed/transformed data
        this.processedDataBucket = new s3.Bucket(this, 'SesProcessedDataBucket', {
            encryption: s3.BucketEncryption.S3_MANAGED,
            blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
            removalPolicy: aws_cdk_lib_1.RemovalPolicy.RETAIN,
            enforceSSL: true,
            lifecycleRules: [
                {
                    id: 'TransitionToIA',
                    enabled: true,
                    transitions: [
                        {
                            storageClass: s3.StorageClass.INFREQUENT_ACCESS,
                            transitionAfter: aws_cdk_lib_1.Duration.days(props.processedDataTransitionToIADays),
                        },
                    ],
                },
            ],
        });
        // Athena query results
        this.athenaResultsBucket = new s3.Bucket(this, 'AthenaResultsBucket', {
            encryption: s3.BucketEncryption.S3_MANAGED,
            blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
            removalPolicy: aws_cdk_lib_1.RemovalPolicy.RETAIN,
            enforceSSL: true,
            lifecycleRules: [
                {
                    id: 'DeleteOldQueryResults',
                    enabled: true,
                    expiration: aws_cdk_lib_1.Duration.days(props.athenaQueryResultsRetentionDays),
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
            timeout: aws_cdk_lib_1.Duration.minutes(10), // Increased from 5 to 10 minutes for Athena query completion
            environment: {
                NAMED_QUERY_ID: createTableQuery.attrNamedQueryId,
                DATABASE_NAME: this.glueDatabase.ref,
                OUTPUT_LOCATION: `s3://${this.athenaResultsBucket.bucketName}/table-creation/`,
            },
        });
        this.athenaResultsBucket.grantReadWrite(createTableLambda);
        createTableLambda.addToRolePolicy(new iam.PolicyStatement({
            actions: [
                'athena:GetNamedQuery',
                'athena:StartQueryExecution',
                'athena:GetQueryExecution',
            ],
            resources: ['*'],
        }));
        createTableLambda.addToRolePolicy(new iam.PolicyStatement({
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
        }));
        // Suppress CDK Nag warning for Glue table wildcard permissions
        cdk_nag_1.NagSuppressions.addResourceSuppressions(createTableLambda, [
            {
                id: 'AwsSolutions-IAM5',
                reason: 'Lambda needs wildcard permissions to access Glue table metadata for Athena query execution that creates tables.',
            },
        ], true);
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
        this.workGroup.applyRemovalPolicy(aws_cdk_lib_1.RemovalPolicy.DESTROY);
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
            removalPolicy: aws_cdk_lib_1.RemovalPolicy.DESTROY,
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
        sesFirehoseRole.addToPolicy(new iam.PolicyStatement({
            actions: ['firehose:PutRecord', 'firehose:PutRecordBatch', 'firehose:DescribeDeliveryStream'],
            resources: [deliveryStream.attrArn],
        }));
        // Suppress CDK Nag warnings for Firehose role Glue permissions
        cdk_nag_1.NagSuppressions.addResourceSuppressions(firehoseRole, [
            {
                id: 'AwsSolutions-IAM5',
                reason: 'Glue catalog operations require wildcard permissions for table access within the database. This allows Firehose to access all tables for schema conversion during Parquet transformation.',
                appliesTo: [
                    `Resource::arn:aws:glue:${cdk.Stack.of(this).region}:${cdk.Stack.of(this).account}:table/<SesEventDatabase>/*`
                ]
            }
        ], true // applyToChildren
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
        let notificationTopic;
        if (props.enableNotifications) {
            notificationTopic = new sns.Topic(this, 'AnalyticsNotificationTopic', {
                displayName: 'SES Campaign Analytics Notifications',
            });
            // Enforce SSL
            notificationTopic.addToResourcePolicy(new iam.PolicyStatement({
                effect: iam.Effect.DENY,
                actions: ['sns:Publish'],
                resources: [notificationTopic.topicArn],
                principals: [new iam.AnyPrincipal()],
                conditions: { Bool: { 'aws:SecureTransport': 'false' } },
            }));
            if (props.notificationEmail) {
                notificationTopic.addSubscription(new subscriptions.EmailSubscription(props.notificationEmail));
            }
        }
        //////// LAMBDA FOR MATERIALIZED VIEW REFRESH ////////
        const refreshLambda = new lambda.Function(this, 'MaterializedViewRefresh', {
            runtime: lambda.Runtime.PYTHON_3_12,
            handler: 'materializedViewRefresh.handler',
            code: lambda.Code.fromAsset(path.join(__dirname, '../lambda')),
            timeout: aws_cdk_lib_1.Duration.minutes(props.lambdaTimeoutMinutes),
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
        refreshLambda.addToRolePolicy(new iam.PolicyStatement({
            actions: [
                'athena:StartQueryExecution',
                'athena:GetQueryExecution',
                'athena:GetQueryResults',
                'athena:StopQueryExecution',
            ],
            resources: [
                `arn:aws:athena:${cdk.Stack.of(this).region}:${cdk.Stack.of(this).account}:workgroup/${this.workGroup.ref}`,
            ],
        }));
        refreshLambda.addToRolePolicy(new iam.PolicyStatement({
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
        }));
        if (notificationTopic) {
            notificationTopic.grantPublish(refreshLambda);
        }
        //////// LAMBDA FOR ATHENA PARTITION MANAGEMENT ////////
        // Lambda to automatically add partitions when new data arrives in S3
        const athenaPartitionLambda = new lambda.Function(this, 'AthenaPartitionLambda', {
            runtime: lambda.Runtime.PYTHON_3_12,
            handler: 'partitionManager.handler',
            code: lambda.Code.fromAsset(path.join(__dirname, '../lambda')),
            timeout: aws_cdk_lib_1.Duration.minutes(5),
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
        athenaPartitionLambda.addToRolePolicy(new iam.PolicyStatement({
            actions: ['athena:StartQueryExecution'],
            resources: [`arn:aws:athena:${cdk.Stack.of(this).region}:${cdk.Stack.of(this).account}:workgroup/*`],
        }));
        athenaPartitionLambda.addToRolePolicy(new iam.PolicyStatement({
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
        }));
        // Grant S3 permission to invoke Lambda
        athenaPartitionLambda.addPermission('S3InvokeLambda', {
            principal: new iam.ServicePrincipal('s3.amazonaws.com'),
            action: 'lambda:InvokeFunction',
            sourceAccount: cdk.Stack.of(this).account,
            sourceArn: this.rawDataBucket.bucketArn,
        });
        // Add S3 notification to trigger Lambda when new objects are created
        this.rawDataBucket.addEventNotification(s3.EventType.OBJECT_CREATED, new s3Notifications.LambdaDestination(athenaPartitionLambda), { prefix: 'events/' });
        //////// DYNAMODB TABLE FOR CAMPAIGN METADATA ////////
        const campaignMetadataTable = new dynamodb.Table(this, 'CampaignMetadataTable', {
            // Let CDK generate unique table name to avoid conflicts on redeploy
            partitionKey: { name: 'campaign_id', type: dynamodb.AttributeType.STRING },
            billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption: dynamodb.TableEncryption.AWS_MANAGED,
            pointInTimeRecovery: true,
            removalPolicy: aws_cdk_lib_1.RemovalPolicy.DESTROY, // Changed to DESTROY for easier redeployment
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
        new aws_cdk_lib_1.CfnOutput(this, 'SesConfigurationSetName', {
            value: configSetName,
            description: 'SES Configuration Set name - use this when sending emails',
        });
        new aws_cdk_lib_1.CfnOutput(this, 'RawDataBucketName', {
            value: this.rawDataBucket.bucketName,
            description: 'S3 bucket for raw SES events',
        });
        new aws_cdk_lib_1.CfnOutput(this, 'ProcessedDataBucketName', {
            value: this.processedDataBucket.bucketName,
            description: 'S3 bucket for processed data and materialized views',
        });
        new aws_cdk_lib_1.CfnOutput(this, 'AthenaResultsBucketName', {
            value: this.athenaResultsBucket.bucketName,
            description: 'S3 bucket for Athena query results',
        });
        new aws_cdk_lib_1.CfnOutput(this, 'GlueDatabaseName', {
            value: this.glueDatabase.ref,
            description: 'Glue database name for SES analytics',
        });
        new aws_cdk_lib_1.CfnOutput(this, 'AthenaWorkGroupName', {
            value: this.workGroup.ref,
            description: 'Athena workgroup for campaign analytics queries',
        });
        new aws_cdk_lib_1.CfnOutput(this, 'FirehoseDeliveryStreamName', {
            value: deliveryStream.ref,
            description: 'Kinesis Firehose delivery stream name',
        });
        new aws_cdk_lib_1.CfnOutput(this, 'FirehoseDeliveryStreamArn', {
            value: deliveryStream.attrArn,
            description: 'Kinesis Firehose delivery stream ARN',
        });
        new aws_cdk_lib_1.CfnOutput(this, 'RefreshLambdaName', {
            value: refreshLambda.functionName,
            description: 'Lambda function for materialized view refresh',
        });
        new aws_cdk_lib_1.CfnOutput(this, 'CampaignMetadataTableName', {
            value: campaignMetadataTable.tableName,
            description: 'DynamoDB table for campaign metadata storage',
        });
        if (notificationTopic) {
            new aws_cdk_lib_1.CfnOutput(this, 'NotificationTopicArn', {
                value: notificationTopic.topicArn,
                description: 'SNS topic for analytics notifications',
            });
        }
        new aws_cdk_lib_1.CfnOutput(this, 'QueryExampleCampaignSummary', {
            value: `SELECT * FROM ${this.glueDatabase.ref}.campaign_metrics_daily WHERE date >= current_date - interval '30' day ORDER BY date DESC`,
            description: 'Example Athena query for campaign summary',
        });
    }
}
exports.SesCampaignAnalyticsStack = SesCampaignAnalyticsStack;
//# sourceMappingURL=data:application/json;base64,eyJ2ZXJzaW9uIjozLCJmaWxlIjoic2VzLWNhbXBhaWduLWFuYWx5dGljcy1zdGFjay5qcyIsInNvdXJjZVJvb3QiOiIiLCJzb3VyY2VzIjpbInNlcy1jYW1wYWlnbi1hbmFseXRpY3Mtc3RhY2sudHMiXSwibmFtZXMiOltdLCJtYXBwaW5ncyI6Ijs7O0FBQUEsbUNBQW1DO0FBRW5DLDZDQUF1RTtBQUN2RSx5Q0FBeUM7QUFDekMsb0VBQW9FO0FBQ3BFLDREQUE0RDtBQUM1RCwyQ0FBMkM7QUFDM0MsNkNBQTZDO0FBQzdDLGlEQUFpRDtBQUNqRCxpREFBaUQ7QUFDakQsaURBQWlEO0FBQ2pELDBEQUEwRDtBQUMxRCw2Q0FBNkM7QUFDN0MsMkNBQTJDO0FBQzNDLG1FQUFtRTtBQUNuRSwyQ0FBMkM7QUFDM0MscURBQXFEO0FBQ3JELHFDQUEwQztBQUMxQyw2QkFBNkI7QUFpQjdCLE1BQWEseUJBQTBCLFNBQVEsR0FBRyxDQUFDLEtBQUs7SUFRdEQsWUFBWSxLQUFnQixFQUFFLEVBQVUsRUFBRSxLQUFxQztRQUM3RSxLQUFLLENBQUMsS0FBSyxFQUFFLEVBQUUsRUFBRTtZQUNmLEdBQUcsS0FBSztZQUNSLFdBQVcsRUFBRSwrR0FBK0c7U0FDN0gsQ0FBQyxDQUFDO1FBRUgsaURBQWlEO1FBQ2pELGtCQUFJLENBQUMsRUFBRSxDQUFDLElBQUksQ0FBQyxDQUFDLEdBQUcsQ0FBQyxTQUFTLEVBQUUsd0JBQXdCLENBQUMsQ0FBQztRQUN2RCxrQkFBSSxDQUFDLEVBQUUsQ0FBQyxJQUFJLENBQUMsQ0FBQyxHQUFHLENBQUMsV0FBVyxFQUFFLEtBQUssQ0FBQyxDQUFDO1FBQ3RDLGtCQUFJLENBQUMsRUFBRSxDQUFDLElBQUksQ0FBQyxDQUFDLEdBQUcsQ0FBQyxhQUFhLEVBQUUsWUFBWSxDQUFDLENBQUM7UUFFL0MsdUNBQXVDO1FBRXZDLHlFQUF5RTtRQUN6RSxJQUFJLGFBQXFCLENBQUM7UUFFMUIsSUFBSSxLQUFLLENBQUMsNEJBQTRCLElBQUksS0FBSyxDQUFDLDRCQUE0QixDQUFDLElBQUksRUFBRSxLQUFLLEVBQUUsRUFBRSxDQUFDO1lBQzNGLGlDQUFpQztZQUNqQyxhQUFhLEdBQUcsS0FBSyxDQUFDLDRCQUE0QixDQUFDO1lBQ25ELE9BQU8sQ0FBQyxHQUFHLENBQUMseUNBQXlDLGFBQWEsRUFBRSxDQUFDLENBQUM7UUFDeEUsQ0FBQzthQUFNLENBQUM7WUFDTiwrQkFBK0I7WUFDL0IsSUFBSSxDQUFDLGdCQUFnQixHQUFHLElBQUksR0FBRyxDQUFDLG1CQUFtQixDQUFDLElBQUksRUFBRSxxQkFBcUIsRUFBRTtnQkFDL0UsSUFBSSxFQUFFLGlCQUFpQixHQUFHLENBQUMsS0FBSyxDQUFDLEVBQUUsQ0FBQyxJQUFJLENBQUMsQ0FBQyxPQUFPLEVBQUU7YUFDcEQsQ0FBQyxDQUFDO1lBQ0gsYUFBYSxHQUFHLElBQUksQ0FBQyxnQkFBZ0IsQ0FBQyxJQUFLLENBQUM7UUFDOUMsQ0FBQztRQUVELDZDQUE2QztRQUU3QywrQkFBK0I7UUFDL0IsSUFBSSxDQUFDLGFBQWEsR0FBRyxJQUFJLEVBQUUsQ0FBQyxNQUFNLENBQUMsSUFBSSxFQUFFLGtCQUFrQixFQUFFO1lBQzNELFVBQVUsRUFBRSxFQUFFLENBQUMsZ0JBQWdCLENBQUMsVUFBVTtZQUMxQyxpQkFBaUIsRUFBRSxFQUFFLENBQUMsaUJBQWlCLENBQUMsU0FBUztZQUNqRCxhQUFhLEVBQUUsMkJBQWEsQ0FBQyxNQUFNO1lBQ25DLFVBQVUsRUFBRSxJQUFJO1lBQ2hCLGNBQWMsRUFBRTtnQkFDZDtvQkFDRSxFQUFFLEVBQUUsa0JBQWtCO29CQUN0QixPQUFPLEVBQUUsSUFBSTtvQkFDYixVQUFVLEVBQUUsc0JBQVEsQ0FBQyxJQUFJLENBQUMsS0FBSyxDQUFDLGlCQUFpQixDQUFDO2lCQUNuRDtnQkFDRDtvQkFDRSxFQUFFLEVBQUUscUJBQXFCO29CQUN6QixPQUFPLEVBQUUsSUFBSTtvQkFDYixXQUFXLEVBQUU7d0JBQ1g7NEJBQ0UsWUFBWSxFQUFFLEVBQUUsQ0FBQyxZQUFZLENBQUMsT0FBTzs0QkFDckMsZUFBZSxFQUFFLHNCQUFRLENBQUMsSUFBSSxDQUFDLElBQUksQ0FBQyxLQUFLLENBQUMsS0FBSyxDQUFDLGlCQUFpQixHQUFHLENBQUMsQ0FBQyxDQUFDO3lCQUN4RTtxQkFDRjtpQkFDRjthQUNGO1NBQ0YsQ0FBQyxDQUFDO1FBRUgsNkJBQTZCO1FBQzdCLElBQUksQ0FBQyxtQkFBbUIsR0FBRyxJQUFJLEVBQUUsQ0FBQyxNQUFNLENBQUMsSUFBSSxFQUFFLHdCQUF3QixFQUFFO1lBQ3ZFLFVBQVUsRUFBRSxFQUFFLENBQUMsZ0JBQWdCLENBQUMsVUFBVTtZQUMxQyxpQkFBaUIsRUFBRSxFQUFFLENBQUMsaUJBQWlCLENBQUMsU0FBUztZQUNqRCxhQUFhLEVBQUUsMkJBQWEsQ0FBQyxNQUFNO1lBQ25DLFVBQVUsRUFBRSxJQUFJO1lBQ2hCLGNBQWMsRUFBRTtnQkFDZDtvQkFDRSxFQUFFLEVBQUUsZ0JBQWdCO29CQUNwQixPQUFPLEVBQUUsSUFBSTtvQkFDYixXQUFXLEVBQUU7d0JBQ1g7NEJBQ0UsWUFBWSxFQUFFLEVBQUUsQ0FBQyxZQUFZLENBQUMsaUJBQWlCOzRCQUMvQyxlQUFlLEVBQUUsc0JBQVEsQ0FBQyxJQUFJLENBQUMsS0FBSyxDQUFDLCtCQUErQixDQUFDO3lCQUN0RTtxQkFDRjtpQkFDRjthQUNGO1NBQ0YsQ0FBQyxDQUFDO1FBRUgsdUJBQXVCO1FBQ3ZCLElBQUksQ0FBQyxtQkFBbUIsR0FBRyxJQUFJLEVBQUUsQ0FBQyxNQUFNLENBQUMsSUFBSSxFQUFFLHFCQUFxQixFQUFFO1lBQ3BFLFVBQVUsRUFBRSxFQUFFLENBQUMsZ0JBQWdCLENBQUMsVUFBVTtZQUMxQyxpQkFBaUIsRUFBRSxFQUFFLENBQUMsaUJBQWlCLENBQUMsU0FBUztZQUNqRCxhQUFhLEVBQUUsMkJBQWEsQ0FBQyxNQUFNO1lBQ25DLFVBQVUsRUFBRSxJQUFJO1lBQ2hCLGNBQWMsRUFBRTtnQkFDZDtvQkFDRSxFQUFFLEVBQUUsdUJBQXVCO29CQUMzQixPQUFPLEVBQUUsSUFBSTtvQkFDYixVQUFVLEVBQUUsc0JBQVEsQ0FBQyxJQUFJLENBQUMsS0FBSyxDQUFDLCtCQUErQixDQUFDO2lCQUNqRTthQUNGO1NBQ0YsQ0FBQyxDQUFDO1FBRUgsOENBQThDO1FBRTlDLElBQUksQ0FBQyxZQUFZLEdBQUcsSUFBSSxJQUFJLENBQUMsV0FBVyxDQUFDLElBQUksRUFBRSxrQkFBa0IsRUFBRTtZQUNqRSxTQUFTLEVBQUUsR0FBRyxDQUFDLEtBQUssQ0FBQyxFQUFFLENBQUMsSUFBSSxDQUFDLENBQUMsT0FBTztZQUNyQyxhQUFhLEVBQUU7Z0JBQ2IsSUFBSSxFQUFFLG9CQUFvQixHQUFHLENBQUMsS0FBSyxDQUFDLEVBQUUsQ0FBQyxJQUFJLENBQUMsQ0FBQyxPQUFPLEVBQUU7Z0JBQ3RELFdBQVcsRUFBRSxtRUFBbUU7YUFDakY7U0FDRixDQUFDLENBQUM7UUFFSCxrRkFBa0Y7UUFDbEYsTUFBTSxnQkFBZ0IsR0FBRyxJQUFJLE1BQU0sQ0FBQyxhQUFhLENBQUMsSUFBSSxFQUFFLHNCQUFzQixFQUFFO1lBQzlFLFFBQVEsRUFBRSxJQUFJLENBQUMsWUFBWSxDQUFDLEdBQUc7WUFDL0IsV0FBVyxFQUFFLDZCQUE2QjtZQUMxQyxJQUFJLEVBQUUseUJBQXlCO1lBQy9CLFdBQVcsRUFBRTs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7OztpQkFzRUYsSUFBSSxDQUFDLGFBQWEsQ0FBQyxVQUFVOzs7O0VBSTVDO1NBQ0csQ0FBQyxDQUFDO1FBRUgseURBQXlEO1FBQ3pELE1BQU0saUJBQWlCLEdBQUcsSUFBSSxNQUFNLENBQUMsUUFBUSxDQUFDLElBQUksRUFBRSxtQkFBbUIsRUFBRTtZQUN2RSxPQUFPLEVBQUUsTUFBTSxDQUFDLE9BQU8sQ0FBQyxXQUFXO1lBQ25DLE9BQU8sRUFBRSxxQkFBcUI7WUFDOUIsSUFBSSxFQUFFLE1BQU0sQ0FBQyxJQUFJLENBQUMsU0FBUyxDQUFDLElBQUksQ0FBQyxJQUFJLENBQUMsU0FBUyxFQUFFLFdBQVcsQ0FBQyxDQUFDO1lBQzlELE9BQU8sRUFBRSxzQkFBUSxDQUFDLE9BQU8sQ0FBQyxFQUFFLENBQUMsRUFBRSw2REFBNkQ7WUFDNUYsV0FBVyxFQUFFO2dCQUNYLGNBQWMsRUFBRSxnQkFBZ0IsQ0FBQyxnQkFBZ0I7Z0JBQ2pELGFBQWEsRUFBRSxJQUFJLENBQUMsWUFBWSxDQUFDLEdBQUc7Z0JBQ3BDLGVBQWUsRUFBRSxRQUFRLElBQUksQ0FBQyxtQkFBbUIsQ0FBQyxVQUFVLGtCQUFrQjthQUMvRTtTQUNGLENBQUMsQ0FBQztRQUVILElBQUksQ0FBQyxtQkFBbUIsQ0FBQyxjQUFjLENBQUMsaUJBQWlCLENBQUMsQ0FBQztRQUUzRCxpQkFBaUIsQ0FBQyxlQUFlLENBQy9CLElBQUksR0FBRyxDQUFDLGVBQWUsQ0FBQztZQUN0QixPQUFPLEVBQUU7Z0JBQ1Asc0JBQXNCO2dCQUN0Qiw0QkFBNEI7Z0JBQzVCLDBCQUEwQjthQUMzQjtZQUNELFNBQVMsRUFBRSxDQUFDLEdBQUcsQ0FBQztTQUNqQixDQUFDLENBQ0gsQ0FBQztRQUVGLGlCQUFpQixDQUFDLGVBQWUsQ0FDL0IsSUFBSSxHQUFHLENBQUMsZUFBZSxDQUFDO1lBQ3RCLE9BQU8sRUFBRTtnQkFDUCxrQkFBa0I7Z0JBQ2xCLGVBQWU7Z0JBQ2Ysa0JBQWtCO2FBQ25CO1lBQ0QsU0FBUyxFQUFFO2dCQUNULGdCQUFnQixHQUFHLENBQUMsS0FBSyxDQUFDLEVBQUUsQ0FBQyxJQUFJLENBQUMsQ0FBQyxNQUFNLElBQUksR0FBRyxDQUFDLEtBQUssQ0FBQyxFQUFFLENBQUMsSUFBSSxDQUFDLENBQUMsT0FBTyxVQUFVO2dCQUNqRixnQkFBZ0IsR0FBRyxDQUFDLEtBQUssQ0FBQyxFQUFFLENBQUMsSUFBSSxDQUFDLENBQUMsTUFBTSxJQUFJLEdBQUcsQ0FBQyxLQUFLLENBQUMsRUFBRSxDQUFDLElBQUksQ0FBQyxDQUFDLE9BQU8sYUFBYSxJQUFJLENBQUMsWUFBWSxDQUFDLEdBQUcsRUFBRTtnQkFDM0csZ0JBQWdCLEdBQUcsQ0FBQyxLQUFLLENBQUMsRUFBRSxDQUFDLElBQUksQ0FBQyxDQUFDLE1BQU0sSUFBSSxHQUFHLENBQUMsS0FBSyxDQUFDLEVBQUUsQ0FBQyxJQUFJLENBQUMsQ0FBQyxPQUFPLFVBQVUsSUFBSSxDQUFDLFlBQVksQ0FBQyxHQUFHLElBQUk7YUFDM0c7U0FDRixDQUFDLENBQ0gsQ0FBQztRQUVGLCtEQUErRDtRQUMvRCx5QkFBZSxDQUFDLHVCQUF1QixDQUNyQyxpQkFBaUIsRUFDakI7WUFDRTtnQkFDRSxFQUFFLEVBQUUsbUJBQW1CO2dCQUN2QixNQUFNLEVBQUUsaUhBQWlIO2FBQzFIO1NBQ0YsRUFDRCxJQUFJLENBQ0wsQ0FBQztRQUVGLDRDQUE0QztRQUM1QyxNQUFNLG9CQUFvQixHQUFHLElBQUksR0FBRyxDQUFDLGNBQWMsQ0FBQyxJQUFJLEVBQUUsc0JBQXNCLEVBQUU7WUFDaEYsWUFBWSxFQUFFLGlCQUFpQixDQUFDLFdBQVc7U0FDNUMsQ0FBQyxDQUFDO1FBRUgsb0JBQW9CLENBQUMsSUFBSSxDQUFDLGFBQWEsQ0FBQyxJQUFJLENBQUMsWUFBWSxDQUFDLENBQUM7UUFDM0Qsb0JBQW9CLENBQUMsSUFBSSxDQUFDLGFBQWEsQ0FBQyxnQkFBZ0IsQ0FBQyxDQUFDO1FBRTFELHVEQUF1RDtRQUN2RCxNQUFNLGtCQUFrQixHQUFHLGdCQUFnQixDQUFDO1FBRTVDLCtDQUErQztRQUMvQyxNQUFNLG9CQUFvQixHQUFHLElBQUksSUFBSSxDQUFDLFFBQVEsQ0FBQyxJQUFJLEVBQUUsc0JBQXNCLEVBQUU7WUFDM0UsU0FBUyxFQUFFLEdBQUcsQ0FBQyxLQUFLLENBQUMsRUFBRSxDQUFDLElBQUksQ0FBQyxDQUFDLE9BQU87WUFDckMsWUFBWSxFQUFFLElBQUksQ0FBQyxZQUFZLENBQUMsR0FBRztZQUNuQyxVQUFVLEVBQUU7Z0JBQ1YsSUFBSSxFQUFFLHdCQUF3QjtnQkFDOUIsV0FBVyxFQUFFLDZDQUE2QztnQkFDMUQsU0FBUyxFQUFFLGdCQUFnQjtnQkFDM0IsVUFBVSxFQUFFO29CQUNWLG9CQUFvQixFQUFFLE1BQU07b0JBQzVCLHNCQUFzQixFQUFFLE1BQU07b0JBQzlCLHVCQUF1QixFQUFFLGdCQUFnQjtvQkFDekMsd0JBQXdCLEVBQUUsWUFBWTtvQkFDdEMsMkJBQTJCLEVBQUUsUUFBUSxJQUFJLENBQUMsbUJBQW1CLENBQUMsVUFBVSwwREFBMEQ7aUJBQ25JO2dCQUNELGFBQWEsRUFBRTtvQkFDYixFQUFFLElBQUksRUFBRSxNQUFNLEVBQUUsSUFBSSxFQUFFLFFBQVEsRUFBRTtpQkFDakM7Z0JBQ0QsaUJBQWlCLEVBQUU7b0JBQ2pCLE9BQU8sRUFBRTt3QkFDUCxFQUFFLElBQUksRUFBRSxhQUFhLEVBQUUsSUFBSSxFQUFFLFFBQVEsRUFBRTt3QkFDdkMsRUFBRSxJQUFJLEVBQUUsZUFBZSxFQUFFLElBQUksRUFBRSxRQUFRLEVBQUU7d0JBQ3pDLEVBQUUsSUFBSSxFQUFFLGFBQWEsRUFBRSxJQUFJLEVBQUUsUUFBUSxFQUFFO3dCQUN2QyxFQUFFLElBQUksRUFBRSxrQkFBa0IsRUFBRSxJQUFJLEVBQUUsUUFBUSxFQUFFO3dCQUM1QyxFQUFFLElBQUksRUFBRSxlQUFlLEVBQUUsSUFBSSxFQUFFLFFBQVEsRUFBRTt3QkFDekMsRUFBRSxJQUFJLEVBQUUsZ0JBQWdCLEVBQUUsSUFBSSxFQUFFLFFBQVEsRUFBRTt3QkFDMUMsRUFBRSxJQUFJLEVBQUUsY0FBYyxFQUFFLElBQUksRUFBRSxRQUFRLEVBQUU7d0JBQ3hDLEVBQUUsSUFBSSxFQUFFLGNBQWMsRUFBRSxJQUFJLEVBQUUsUUFBUSxFQUFFO3dCQUN4QyxFQUFFLElBQUksRUFBRSxZQUFZLEVBQUUsSUFBSSxFQUFFLFFBQVEsRUFBRTt3QkFDdEMsRUFBRSxJQUFJLEVBQUUsU0FBUyxFQUFFLElBQUksRUFBRSxRQUFRLEVBQUU7d0JBQ25DLEVBQUUsSUFBSSxFQUFFLG9CQUFvQixFQUFFLElBQUksRUFBRSxRQUFRLEVBQUU7d0JBQzlDLEVBQUUsSUFBSSxFQUFFLGVBQWUsRUFBRSxJQUFJLEVBQUUsUUFBUSxFQUFFO3dCQUN6QyxFQUFFLElBQUksRUFBRSxXQUFXLEVBQUUsSUFBSSxFQUFFLFFBQVEsRUFBRTt3QkFDckMsRUFBRSxJQUFJLEVBQUUsWUFBWSxFQUFFLElBQUksRUFBRSxRQUFRLEVBQUU7d0JBQ3RDLEVBQUUsSUFBSSxFQUFFLGtCQUFrQixFQUFFLElBQUksRUFBRSxRQUFRLEVBQUU7d0JBQzVDLEVBQUUsSUFBSSxFQUFFLGdCQUFnQixFQUFFLElBQUksRUFBRSxRQUFRLEVBQUU7d0JBQzFDLEVBQUUsSUFBSSxFQUFFLHdCQUF3QixFQUFFLElBQUksRUFBRSxRQUFRLEVBQUU7d0JBQ2xELEVBQUUsSUFBSSxFQUFFLG1CQUFtQixFQUFFLElBQUksRUFBRSxRQUFRLEVBQUU7d0JBQzdDLEVBQUUsSUFBSSxFQUFFLHNCQUFzQixFQUFFLElBQUksRUFBRSxRQUFRLEVBQUU7d0JBQ2hELEVBQUUsSUFBSSxFQUFFLGNBQWMsRUFBRSxJQUFJLEVBQUUsUUFBUSxFQUFFO3dCQUN4QyxFQUFFLElBQUksRUFBRSxnQkFBZ0IsRUFBRSxJQUFJLEVBQUUsUUFBUSxFQUFFO3FCQUMzQztvQkFDRCxRQUFRLEVBQUUsUUFBUSxJQUFJLENBQUMsbUJBQW1CLENBQUMsVUFBVSw2Q0FBNkM7b0JBQ2xHLFdBQVcsRUFBRSwrREFBK0Q7b0JBQzVFLFlBQVksRUFBRSxnRUFBZ0U7b0JBQzlFLFNBQVMsRUFBRTt3QkFDVCxvQkFBb0IsRUFBRSw2REFBNkQ7d0JBQ25GLFVBQVUsRUFBRTs0QkFDVixzQkFBc0IsRUFBRSxHQUFHO3lCQUM1QjtxQkFDRjtpQkFDRjthQUNGO1NBQ0YsQ0FBQyxDQUFDO1FBRUgsa0NBQWtDO1FBRWxDLElBQUksQ0FBQyxTQUFTLEdBQUcsSUFBSSxNQUFNLENBQUMsWUFBWSxDQUFDLElBQUksRUFBRSx1QkFBdUIsRUFBRTtZQUN0RSxJQUFJLEVBQUUsb0JBQW9CLEdBQUcsQ0FBQyxLQUFLLENBQUMsRUFBRSxDQUFDLElBQUksQ0FBQyxDQUFDLE9BQU8sRUFBRTtZQUN0RCxXQUFXLEVBQUUsOENBQThDO1lBQzNELHFCQUFxQixFQUFFLElBQUksRUFBRSxnREFBZ0Q7WUFDN0Usc0JBQXNCLEVBQUU7Z0JBQ3RCLG1CQUFtQixFQUFFO29CQUNuQixjQUFjLEVBQUUsUUFBUSxJQUFJLENBQUMsbUJBQW1CLENBQUMsVUFBVSxpQkFBaUI7b0JBQzVFLHVCQUF1QixFQUFFO3dCQUN2QixnQkFBZ0IsRUFBRSxRQUFRO3FCQUMzQjtpQkFDRjtnQkFDRCw2QkFBNkIsRUFBRSxJQUFJO2dCQUNuQywrQkFBK0IsRUFBRSxJQUFJO2dCQUNyQywwQkFBMEIsRUFBRSxLQUFLLENBQUMsc0JBQXNCLEdBQUcsSUFBSSxHQUFHLElBQUksR0FBRyxJQUFJO2dCQUM3RSxhQUFhLEVBQUU7b0JBQ2IscUJBQXFCLEVBQUUseUJBQXlCO2lCQUNqRDthQUNGO1NBQ0YsQ0FBQyxDQUFDO1FBRUgseUNBQXlDO1FBQ3pDLElBQUksQ0FBQyxTQUFTLENBQUMsa0JBQWtCLENBQUMsMkJBQWEsQ0FBQyxPQUFPLENBQUMsQ0FBQztRQUV6RCxnREFBZ0Q7UUFFaEQsbUNBQW1DO1FBQ25DLE1BQU0sWUFBWSxHQUFHLElBQUksR0FBRyxDQUFDLElBQUksQ0FBQyxJQUFJLEVBQUUsY0FBYyxFQUFFO1lBQ3RELFNBQVMsRUFBRSxJQUFJLEdBQUcsQ0FBQyxnQkFBZ0IsQ0FBQyx3QkFBd0IsQ0FBQztZQUM3RCxXQUFXLEVBQUUsNEVBQTRFO1NBQzFGLENBQUMsQ0FBQztRQUVILElBQUksQ0FBQyxhQUFhLENBQUMsVUFBVSxDQUFDLFlBQVksQ0FBQyxDQUFDO1FBRTVDLHFEQUFxRDtRQUNyRCxNQUFNLGVBQWUsR0FBRyxJQUFJLEdBQUcsQ0FBQyxJQUFJLENBQUMsSUFBSSxFQUFFLGlCQUFpQixFQUFFO1lBQzVELFNBQVMsRUFBRSxJQUFJLEdBQUcsQ0FBQyxnQkFBZ0IsQ0FBQyxtQkFBbUIsQ0FBQztZQUN4RCxXQUFXLEVBQUUsbURBQW1EO1NBQ2pFLENBQUMsQ0FBQztRQUVILCtCQUErQjtRQUMvQixNQUFNLGdCQUFnQixHQUFHLElBQUksSUFBSSxDQUFDLFFBQVEsQ0FBQyxJQUFJLEVBQUUsa0JBQWtCLEVBQUU7WUFDbkUsWUFBWSxFQUFFLHNDQUFzQyxHQUFHLENBQUMsS0FBSyxDQUFDLEVBQUUsQ0FBQyxJQUFJLENBQUMsQ0FBQyxPQUFPLEVBQUU7WUFDaEYsU0FBUyxFQUFFLElBQUksQ0FBQyxhQUFhLENBQUMsUUFBUTtZQUN0QyxhQUFhLEVBQUUsMkJBQWEsQ0FBQyxPQUFPO1NBQ3JDLENBQUMsQ0FBQztRQUVILE1BQU0saUJBQWlCLEdBQUcsSUFBSSxJQUFJLENBQUMsU0FBUyxDQUFDLElBQUksRUFBRSxtQkFBbUIsRUFBRTtZQUN0RSxRQUFRLEVBQUUsZ0JBQWdCO1lBQzFCLGFBQWEsRUFBRSxZQUFZO1NBQzVCLENBQUMsQ0FBQztRQUVILGdCQUFnQixDQUFDLFVBQVUsQ0FBQyxZQUFZLENBQUMsQ0FBQztRQUUxQyx1REFBdUQ7UUFFdkQsTUFBTSxjQUFjLEdBQUcsSUFBSSxRQUFRLENBQUMsaUJBQWlCLENBQUMsSUFBSSxFQUFFLHlCQUF5QixFQUFFO1lBQ3JGLGtCQUFrQixFQUFFLHdCQUF3QixHQUFHLENBQUMsS0FBSyxDQUFDLEVBQUUsQ0FBQyxJQUFJLENBQUMsQ0FBQyxPQUFPLEVBQUU7WUFDeEUsa0JBQWtCLEVBQUUsV0FBVztZQUMvQixrQ0FBa0MsRUFBRTtnQkFDbEMsU0FBUyxFQUFFLElBQUksQ0FBQyxhQUFhLENBQUMsU0FBUztnQkFDdkMsT0FBTyxFQUFFLFlBQVksQ0FBQyxPQUFPO2dCQUM3QixNQUFNLEVBQUUsK0ZBQStGO2dCQUN2RyxpQkFBaUIsRUFBRSx3R0FBd0c7Z0JBQzNILGNBQWMsRUFBRTtvQkFDZCxTQUFTLEVBQUUsSUFBSSxDQUFDLEdBQUcsQ0FBQyxLQUFLLENBQUMsb0JBQW9CLEVBQUUsRUFBRSxDQUFDLEVBQUUsdURBQXVEO29CQUM1RyxpQkFBaUIsRUFBRSxLQUFLLENBQUMsNkJBQTZCO2lCQUN2RDtnQkFDRCxpQkFBaUIsRUFBRSxjQUFjO2dCQUNqQyxpQ0FBaUMsRUFBRTtvQkFDakMsT0FBTyxFQUFFLElBQUk7b0JBQ2IsbUJBQW1CLEVBQUU7d0JBQ25CLFlBQVksRUFBRSxJQUFJLENBQUMsWUFBWSxDQUFDLEdBQUc7d0JBQ25DLFNBQVMsRUFBRSxrQkFBa0I7d0JBQzdCLE1BQU0sRUFBRSxHQUFHLENBQUMsS0FBSyxDQUFDLEVBQUUsQ0FBQyxJQUFJLENBQUMsQ0FBQyxNQUFNO3dCQUNqQyxPQUFPLEVBQUUsWUFBWSxDQUFDLE9BQU87cUJBQzlCO29CQUNELHdCQUF3QixFQUFFO3dCQUN4QixZQUFZLEVBQUU7NEJBQ1osY0FBYyxFQUFFLEVBQUU7eUJBQ25CO3FCQUNGO29CQUNELHlCQUF5QixFQUFFO3dCQUN6QixVQUFVLEVBQUU7NEJBQ1YsWUFBWSxFQUFFO2dDQUNaLFdBQVcsRUFBRSxRQUFROzZCQUN0Qjt5QkFDRjtxQkFDRjtpQkFDRjtnQkFDRCx3QkFBd0IsRUFBRTtvQkFDeEIsT0FBTyxFQUFFLElBQUk7b0JBQ2IsWUFBWSxFQUFFLGdCQUFnQixDQUFDLFlBQVk7b0JBQzNDLGFBQWEsRUFBRSxpQkFBaUIsQ0FBQyxhQUFhO2lCQUMvQzthQUNGO1NBQ0YsQ0FBQyxDQUFDO1FBRUgsMENBQTBDO1FBQzFDLE1BQU0sVUFBVSxHQUFHLElBQUksR0FBRyxDQUFDLE1BQU0sQ0FBQyxJQUFJLEVBQUUsb0JBQW9CLEVBQUU7WUFDNUQsVUFBVSxFQUFFO2dCQUNWLElBQUksR0FBRyxDQUFDLGVBQWUsQ0FBQztvQkFDdEIsT0FBTyxFQUFFO3dCQUNQLGVBQWU7d0JBQ2Ysc0JBQXNCO3dCQUN0Qix1QkFBdUI7cUJBQ3hCO29CQUNELFNBQVMsRUFBRTt3QkFDVCxnQkFBZ0IsR0FBRyxDQUFDLEtBQUssQ0FBQyxFQUFFLENBQUMsSUFBSSxDQUFDLENBQUMsTUFBTSxJQUFJLEdBQUcsQ0FBQyxLQUFLLENBQUMsRUFBRSxDQUFDLElBQUksQ0FBQyxDQUFDLE9BQU8sVUFBVTt3QkFDakYsZ0JBQWdCLEdBQUcsQ0FBQyxLQUFLLENBQUMsRUFBRSxDQUFDLElBQUksQ0FBQyxDQUFDLE1BQU0sSUFBSSxHQUFHLENBQUMsS0FBSyxDQUFDLEVBQUUsQ0FBQyxJQUFJLENBQUMsQ0FBQyxPQUFPLGFBQWEsSUFBSSxDQUFDLFlBQVksQ0FBQyxHQUFHLEVBQUU7d0JBQzNHLGdCQUFnQixHQUFHLENBQUMsS0FBSyxDQUFDLEVBQUUsQ0FBQyxJQUFJLENBQUMsQ0FBQyxNQUFNLElBQUksR0FBRyxDQUFDLEtBQUssQ0FBQyxFQUFFLENBQUMsSUFBSSxDQUFDLENBQUMsT0FBTyxVQUFVLElBQUksQ0FBQyxZQUFZLENBQUMsR0FBRyxJQUFJO3FCQUMzRztpQkFDRixDQUFDO2FBQ0g7U0FDRixDQUFDLENBQUM7UUFFSCxVQUFVLENBQUMsWUFBWSxDQUFDLFlBQVksQ0FBQyxDQUFDO1FBRXRDLHNGQUFzRjtRQUN0RixjQUFjLENBQUMsSUFBSSxDQUFDLGFBQWEsQ0FBQyxVQUFVLENBQUMsQ0FBQztRQUM5QyxjQUFjLENBQUMsSUFBSSxDQUFDLGFBQWEsQ0FBQyxvQkFBb0IsQ0FBQyxDQUFDO1FBRXhELHVEQUF1RDtRQUN2RCxlQUFlLENBQUMsV0FBVyxDQUN6QixJQUFJLEdBQUcsQ0FBQyxlQUFlLENBQUM7WUFDdEIsT0FBTyxFQUFFLENBQUMsb0JBQW9CLEVBQUUseUJBQXlCLEVBQUUsaUNBQWlDLENBQUM7WUFDN0YsU0FBUyxFQUFFLENBQUMsY0FBYyxDQUFDLE9BQU8sQ0FBQztTQUNwQyxDQUFDLENBQ0gsQ0FBQztRQUVGLCtEQUErRDtRQUMvRCx5QkFBZSxDQUFDLHVCQUF1QixDQUNyQyxZQUFZLEVBQ1o7WUFDRTtnQkFDRSxFQUFFLEVBQUUsbUJBQW1CO2dCQUN2QixNQUFNLEVBQUUsMkxBQTJMO2dCQUNuTSxTQUFTLEVBQUU7b0JBQ1QsMEJBQTBCLEdBQUcsQ0FBQyxLQUFLLENBQUMsRUFBRSxDQUFDLElBQUksQ0FBQyxDQUFDLE1BQU0sSUFBSSxHQUFHLENBQUMsS0FBSyxDQUFDLEVBQUUsQ0FBQyxJQUFJLENBQUMsQ0FBQyxPQUFPLDZCQUE2QjtpQkFDL0c7YUFDRjtTQUNGLEVBQ0QsSUFBSSxDQUFDLGtCQUFrQjtTQUN4QixDQUFDO1FBRUYsdUNBQXVDO1FBRXZDLE1BQU0sbUJBQW1CLEdBQUcsSUFBSSxHQUFHLENBQUMsbUNBQW1DLENBQUMsSUFBSSxFQUFFLHFCQUFxQixFQUFFO1lBQ25HLG9CQUFvQixFQUFFLGFBQWE7WUFDbkMsZ0JBQWdCLEVBQUU7Z0JBQ2hCLElBQUksRUFBRSxzQkFBc0I7Z0JBQzVCLE9BQU8sRUFBRSxJQUFJO2dCQUNiLGtCQUFrQixFQUFFLENBQUMsTUFBTSxFQUFFLFFBQVEsRUFBRSxRQUFRLEVBQUUsV0FBVyxFQUFFLFVBQVUsRUFBRSxNQUFNLEVBQUUsT0FBTyxFQUFFLGtCQUFrQixDQUFDO2dCQUM5RywwQkFBMEIsRUFBRTtvQkFDMUIsaUJBQWlCLEVBQUUsY0FBYyxDQUFDLE9BQU87b0JBQ3pDLFVBQVUsRUFBRSxlQUFlLENBQUMsT0FBTztpQkFDcEM7YUFDRjtTQUNGLENBQUMsQ0FBQztRQUVILDBFQUEwRTtRQUMxRSxtQkFBbUIsQ0FBQyxJQUFJLENBQUMsYUFBYSxDQUFDLGNBQWMsQ0FBQyxDQUFDO1FBQ3ZELG1CQUFtQixDQUFDLElBQUksQ0FBQyxhQUFhLENBQUMsZUFBZSxDQUFDLENBQUM7UUFFeEQsNkNBQTZDO1FBRTdDLElBQUksaUJBQXdDLENBQUM7UUFFN0MsSUFBSSxLQUFLLENBQUMsbUJBQW1CLEVBQUUsQ0FBQztZQUM5QixpQkFBaUIsR0FBRyxJQUFJLEdBQUcsQ0FBQyxLQUFLLENBQUMsSUFBSSxFQUFFLDRCQUE0QixFQUFFO2dCQUNwRSxXQUFXLEVBQUUsc0NBQXNDO2FBQ3BELENBQUMsQ0FBQztZQUVILGNBQWM7WUFDZCxpQkFBaUIsQ0FBQyxtQkFBbUIsQ0FDbkMsSUFBSSxHQUFHLENBQUMsZUFBZSxDQUFDO2dCQUN0QixNQUFNLEVBQUUsR0FBRyxDQUFDLE1BQU0sQ0FBQyxJQUFJO2dCQUN2QixPQUFPLEVBQUUsQ0FBQyxhQUFhLENBQUM7Z0JBQ3hCLFNBQVMsRUFBRSxDQUFDLGlCQUFpQixDQUFDLFFBQVEsQ0FBQztnQkFDdkMsVUFBVSxFQUFFLENBQUMsSUFBSSxHQUFHLENBQUMsWUFBWSxFQUFFLENBQUM7Z0JBQ3BDLFVBQVUsRUFBRSxFQUFFLElBQUksRUFBRSxFQUFFLHFCQUFxQixFQUFFLE9BQU8sRUFBRSxFQUFFO2FBQ3pELENBQUMsQ0FDSCxDQUFDO1lBRUYsSUFBSSxLQUFLLENBQUMsaUJBQWlCLEVBQUUsQ0FBQztnQkFDNUIsaUJBQWlCLENBQUMsZUFBZSxDQUMvQixJQUFJLGFBQWEsQ0FBQyxpQkFBaUIsQ0FBQyxLQUFLLENBQUMsaUJBQWlCLENBQUMsQ0FDN0QsQ0FBQztZQUNKLENBQUM7UUFDSCxDQUFDO1FBRUQsc0RBQXNEO1FBRXRELE1BQU0sYUFBYSxHQUFHLElBQUksTUFBTSxDQUFDLFFBQVEsQ0FBQyxJQUFJLEVBQUUseUJBQXlCLEVBQUU7WUFDekUsT0FBTyxFQUFFLE1BQU0sQ0FBQyxPQUFPLENBQUMsV0FBVztZQUNuQyxPQUFPLEVBQUUsaUNBQWlDO1lBQzFDLElBQUksRUFBRSxNQUFNLENBQUMsSUFBSSxDQUFDLFNBQVMsQ0FBQyxJQUFJLENBQUMsSUFBSSxDQUFDLFNBQVMsRUFBRSxXQUFXLENBQUMsQ0FBQztZQUM5RCxPQUFPLEVBQUUsc0JBQVEsQ0FBQyxPQUFPLENBQUMsS0FBSyxDQUFDLG9CQUFvQixDQUFDO1lBQ3JELFVBQVUsRUFBRSxLQUFLLENBQUMsY0FBYztZQUNoQyxZQUFZLEVBQUUsSUFBSSxDQUFDLGFBQWEsQ0FBQyxRQUFRO1lBQ3pDLFdBQVcsRUFBRTtnQkFDWCxhQUFhLEVBQUUsSUFBSSxDQUFDLFlBQVksQ0FBQyxHQUFHO2dCQUNwQyxnQkFBZ0IsRUFBRSxrQkFBa0I7Z0JBQ3BDLHNCQUFzQixFQUFFLG9CQUFvQixDQUFDLEdBQUc7Z0JBQ2hELGdCQUFnQixFQUFFLElBQUksQ0FBQyxtQkFBbUIsQ0FBQyxVQUFVO2dCQUNyRCxzQkFBc0IsRUFBRSxRQUFRLElBQUksQ0FBQyxtQkFBbUIsQ0FBQyxVQUFVLG1CQUFtQjtnQkFDdEYsY0FBYyxFQUFFLElBQUksQ0FBQyxTQUFTLENBQUMsR0FBRztnQkFDbEMsc0JBQXNCLEVBQUUsaUJBQWlCLEVBQUUsUUFBUSxJQUFJLEVBQUU7YUFDMUQ7U0FDRixDQUFDLENBQUM7UUFFSCxzQ0FBc0M7UUFDdEMsSUFBSSxDQUFDLG1CQUFtQixDQUFDLGNBQWMsQ0FBQyxhQUFhLENBQUMsQ0FBQztRQUN2RCxJQUFJLENBQUMsYUFBYSxDQUFDLFNBQVMsQ0FBQyxhQUFhLENBQUMsQ0FBQztRQUM1QyxJQUFJLENBQUMsbUJBQW1CLENBQUMsY0FBYyxDQUFDLGFBQWEsQ0FBQyxDQUFDO1FBRXZELGFBQWEsQ0FBQyxlQUFlLENBQzNCLElBQUksR0FBRyxDQUFDLGVBQWUsQ0FBQztZQUN0QixPQUFPLEVBQUU7Z0JBQ1AsNEJBQTRCO2dCQUM1QiwwQkFBMEI7Z0JBQzFCLHdCQUF3QjtnQkFDeEIsMkJBQTJCO2FBQzVCO1lBQ0QsU0FBUyxFQUFFO2dCQUNULGtCQUFrQixHQUFHLENBQUMsS0FBSyxDQUFDLEVBQUUsQ0FBQyxJQUFJLENBQUMsQ0FBQyxNQUFNLElBQUksR0FBRyxDQUFDLEtBQUssQ0FBQyxFQUFFLENBQUMsSUFBSSxDQUFDLENBQUMsT0FBTyxjQUFjLElBQUksQ0FBQyxTQUFTLENBQUMsR0FBRyxFQUFFO2FBQzVHO1NBQ0YsQ0FBQyxDQUNILENBQUM7UUFFRixhQUFhLENBQUMsZUFBZSxDQUMzQixJQUFJLEdBQUcsQ0FBQyxlQUFlLENBQUM7WUFDdEIsT0FBTyxFQUFFO2dCQUNQLGtCQUFrQjtnQkFDbEIsZUFBZTtnQkFDZixvQkFBb0I7Z0JBQ3BCLHNCQUFzQjtnQkFDdEIsc0JBQXNCO2dCQUN0QixzQkFBc0I7Z0JBQ3RCLDJCQUEyQjtnQkFDM0IsMkJBQTJCO2FBQzVCO1lBQ0QsU0FBUyxFQUFFO2dCQUNULGdCQUFnQixHQUFHLENBQUMsS0FBSyxDQUFDLEVBQUUsQ0FBQyxJQUFJLENBQUMsQ0FBQyxNQUFNLElBQUksR0FBRyxDQUFDLEtBQUssQ0FBQyxFQUFFLENBQUMsSUFBSSxDQUFDLENBQUMsT0FBTyxVQUFVO2dCQUNqRixnQkFBZ0IsR0FBRyxDQUFDLEtBQUssQ0FBQyxFQUFFLENBQUMsSUFBSSxDQUFDLENBQUMsTUFBTSxJQUFJLEdBQUcsQ0FBQyxLQUFLLENBQUMsRUFBRSxDQUFDLElBQUksQ0FBQyxDQUFDLE9BQU8sYUFBYSxJQUFJLENBQUMsWUFBWSxDQUFDLEdBQUcsRUFBRTtnQkFDM0csZ0JBQWdCLEdBQUcsQ0FBQyxLQUFLLENBQUMsRUFBRSxDQUFDLElBQUksQ0FBQyxDQUFDLE1BQU0sSUFBSSxHQUFHLENBQUMsS0FBSyxDQUFDLEVBQUUsQ0FBQyxJQUFJLENBQUMsQ0FBQyxPQUFPLFVBQVUsSUFBSSxDQUFDLFlBQVksQ0FBQyxHQUFHLElBQUk7YUFDM0c7U0FDRixDQUFDLENBQ0gsQ0FBQztRQUVGLElBQUksaUJBQWlCLEVBQUUsQ0FBQztZQUN0QixpQkFBaUIsQ0FBQyxZQUFZLENBQUMsYUFBYSxDQUFDLENBQUM7UUFDaEQsQ0FBQztRQUVELHdEQUF3RDtRQUV4RCxxRUFBcUU7UUFDckUsTUFBTSxxQkFBcUIsR0FBRyxJQUFJLE1BQU0sQ0FBQyxRQUFRLENBQUMsSUFBSSxFQUFFLHVCQUF1QixFQUFFO1lBQy9FLE9BQU8sRUFBRSxNQUFNLENBQUMsT0FBTyxDQUFDLFdBQVc7WUFDbkMsT0FBTyxFQUFFLDBCQUEwQjtZQUNuQyxJQUFJLEVBQUUsTUFBTSxDQUFDLElBQUksQ0FBQyxTQUFTLENBQUMsSUFBSSxDQUFDLElBQUksQ0FBQyxTQUFTLEVBQUUsV0FBVyxDQUFDLENBQUM7WUFDOUQsT0FBTyxFQUFFLHNCQUFRLENBQUMsT0FBTyxDQUFDLENBQUMsQ0FBQztZQUM1QixVQUFVLEVBQUUsR0FBRztZQUNmLFdBQVcsRUFBRTtnQkFDWCxhQUFhLEVBQUUsSUFBSSxDQUFDLFlBQVksQ0FBQyxHQUFHO2dCQUNwQyxVQUFVLEVBQUUsa0JBQWtCO2dCQUM5QixlQUFlLEVBQUUsUUFBUSxJQUFJLENBQUMsbUJBQW1CLENBQUMsVUFBVSxrQkFBa0I7YUFDL0U7U0FDRixDQUFDLENBQUM7UUFFSCx3Q0FBd0M7UUFDeEMsSUFBSSxDQUFDLGFBQWEsQ0FBQyxTQUFTLENBQUMscUJBQXFCLENBQUMsQ0FBQztRQUNwRCxJQUFJLENBQUMsbUJBQW1CLENBQUMsY0FBYyxDQUFDLHFCQUFxQixDQUFDLENBQUM7UUFFL0QscUJBQXFCLENBQUMsZUFBZSxDQUNuQyxJQUFJLEdBQUcsQ0FBQyxlQUFlLENBQUM7WUFDdEIsT0FBTyxFQUFFLENBQUMsNEJBQTRCLENBQUM7WUFDdkMsU0FBUyxFQUFFLENBQUMsa0JBQWtCLEdBQUcsQ0FBQyxLQUFLLENBQUMsRUFBRSxDQUFDLElBQUksQ0FBQyxDQUFDLE1BQU0sSUFBSSxHQUFHLENBQUMsS0FBSyxDQUFDLEVBQUUsQ0FBQyxJQUFJLENBQUMsQ0FBQyxPQUFPLGNBQWMsQ0FBQztTQUNyRyxDQUFDLENBQ0gsQ0FBQztRQUVGLHFCQUFxQixDQUFDLGVBQWUsQ0FDbkMsSUFBSSxHQUFHLENBQUMsZUFBZSxDQUFDO1lBQ3RCLE9BQU8sRUFBRTtnQkFDUCxrQkFBa0I7Z0JBQ2xCLGVBQWU7Z0JBQ2Ysc0JBQXNCO2dCQUN0QiwyQkFBMkI7YUFDNUI7WUFDRCxTQUFTLEVBQUU7Z0JBQ1QsZ0JBQWdCLEdBQUcsQ0FBQyxLQUFLLENBQUMsRUFBRSxDQUFDLElBQUksQ0FBQyxDQUFDLE1BQU0sSUFBSSxHQUFHLENBQUMsS0FBSyxDQUFDLEVBQUUsQ0FBQyxJQUFJLENBQUMsQ0FBQyxPQUFPLFVBQVU7Z0JBQ2pGLGdCQUFnQixHQUFHLENBQUMsS0FBSyxDQUFDLEVBQUUsQ0FBQyxJQUFJLENBQUMsQ0FBQyxNQUFNLElBQUksR0FBRyxDQUFDLEtBQUssQ0FBQyxFQUFFLENBQUMsSUFBSSxDQUFDLENBQUMsT0FBTyxhQUFhLElBQUksQ0FBQyxZQUFZLENBQUMsR0FBRyxFQUFFO2dCQUMzRyxnQkFBZ0IsR0FBRyxDQUFDLEtBQUssQ0FBQyxFQUFFLENBQUMsSUFBSSxDQUFDLENBQUMsTUFBTSxJQUFJLEdBQUcsQ0FBQyxLQUFLLENBQUMsRUFBRSxDQUFDLElBQUksQ0FBQyxDQUFDLE9BQU8sVUFBVSxJQUFJLENBQUMsWUFBWSxDQUFDLEdBQUcsSUFBSTthQUMzRztTQUNGLENBQUMsQ0FDSCxDQUFDO1FBRUYsdUNBQXVDO1FBQ3ZDLHFCQUFxQixDQUFDLGFBQWEsQ0FBQyxnQkFBZ0IsRUFBRTtZQUNwRCxTQUFTLEVBQUUsSUFBSSxHQUFHLENBQUMsZ0JBQWdCLENBQUMsa0JBQWtCLENBQUM7WUFDdkQsTUFBTSxFQUFFLHVCQUF1QjtZQUMvQixhQUFhLEVBQUUsR0FBRyxDQUFDLEtBQUssQ0FBQyxFQUFFLENBQUMsSUFBSSxDQUFDLENBQUMsT0FBTztZQUN6QyxTQUFTLEVBQUUsSUFBSSxDQUFDLGFBQWEsQ0FBQyxTQUFTO1NBQ3hDLENBQUMsQ0FBQztRQUVILHFFQUFxRTtRQUNyRSxJQUFJLENBQUMsYUFBYSxDQUFDLG9CQUFvQixDQUNyQyxFQUFFLENBQUMsU0FBUyxDQUFDLGNBQWMsRUFDM0IsSUFBSSxlQUFlLENBQUMsaUJBQWlCLENBQUMscUJBQXFCLENBQUMsRUFDNUQsRUFBRSxNQUFNLEVBQUUsU0FBUyxFQUFFLENBQ3RCLENBQUM7UUFFRixzREFBc0Q7UUFFdEQsTUFBTSxxQkFBcUIsR0FBRyxJQUFJLFFBQVEsQ0FBQyxLQUFLLENBQUMsSUFBSSxFQUFFLHVCQUF1QixFQUFFO1lBQzlFLG9FQUFvRTtZQUNwRSxZQUFZLEVBQUUsRUFBRSxJQUFJLEVBQUUsYUFBYSxFQUFFLElBQUksRUFBRSxRQUFRLENBQUMsYUFBYSxDQUFDLE1BQU0sRUFBRTtZQUMxRSxXQUFXLEVBQUUsUUFBUSxDQUFDLFdBQVcsQ0FBQyxlQUFlO1lBQ2pELFVBQVUsRUFBRSxRQUFRLENBQUMsZUFBZSxDQUFDLFdBQVc7WUFDaEQsbUJBQW1CLEVBQUUsSUFBSTtZQUN6QixhQUFhLEVBQUUsMkJBQWEsQ0FBQyxPQUFPLEVBQUUsNkNBQTZDO1lBQ25GLG1CQUFtQixFQUFFLEtBQUs7U0FDM0IsQ0FBQyxDQUFDO1FBRUgsK0VBQStFO1FBQy9FLHFCQUFxQixDQUFDLGtCQUFrQixDQUFDLGFBQWEsQ0FBQyxDQUFDO1FBRXhELHdEQUF3RDtRQUV4RCxNQUFNLFdBQVcsR0FBRyxJQUFJLE1BQU0sQ0FBQyxJQUFJLENBQUMsSUFBSSxFQUFFLGtCQUFrQixFQUFFO1lBQzVELFFBQVEsRUFBRSxNQUFNLENBQUMsUUFBUSxDQUFDLFVBQVUsQ0FBQyxLQUFLLENBQUMsbUJBQW1CLENBQUM7WUFDL0QsV0FBVyxFQUFFLDBEQUEwRDtTQUN4RSxDQUFDLENBQUM7UUFFSCxXQUFXLENBQUMsU0FBUyxDQUFDLElBQUksT0FBTyxDQUFDLGNBQWMsQ0FBQyxhQUFhLENBQUMsQ0FBQyxDQUFDO1FBRWpFLHlCQUF5QjtRQUV6QixJQUFJLHVCQUFTLENBQUMsSUFBSSxFQUFFLHlCQUF5QixFQUFFO1lBQzdDLEtBQUssRUFBRSxhQUFhO1lBQ3BCLFdBQVcsRUFBRSwyREFBMkQ7U0FDekUsQ0FBQyxDQUFDO1FBRUgsSUFBSSx1QkFBUyxDQUFDLElBQUksRUFBRSxtQkFBbUIsRUFBRTtZQUN2QyxLQUFLLEVBQUUsSUFBSSxDQUFDLGFBQWEsQ0FBQyxVQUFVO1lBQ3BDLFdBQVcsRUFBRSw4QkFBOEI7U0FDNUMsQ0FBQyxDQUFDO1FBRUgsSUFBSSx1QkFBUyxDQUFDLElBQUksRUFBRSx5QkFBeUIsRUFBRTtZQUM3QyxLQUFLLEVBQUUsSUFBSSxDQUFDLG1CQUFtQixDQUFDLFVBQVU7WUFDMUMsV0FBVyxFQUFFLHFEQUFxRDtTQUNuRSxDQUFDLENBQUM7UUFFSCxJQUFJLHVCQUFTLENBQUMsSUFBSSxFQUFFLHlCQUF5QixFQUFFO1lBQzdDLEtBQUssRUFBRSxJQUFJLENBQUMsbUJBQW1CLENBQUMsVUFBVTtZQUMxQyxXQUFXLEVBQUUsb0NBQW9DO1NBQ2xELENBQUMsQ0FBQztRQUVILElBQUksdUJBQVMsQ0FBQyxJQUFJLEVBQUUsa0JBQWtCLEVBQUU7WUFDdEMsS0FBSyxFQUFFLElBQUksQ0FBQyxZQUFZLENBQUMsR0FBRztZQUM1QixXQUFXLEVBQUUsc0NBQXNDO1NBQ3BELENBQUMsQ0FBQztRQUVILElBQUksdUJBQVMsQ0FBQyxJQUFJLEVBQUUscUJBQXFCLEVBQUU7WUFDekMsS0FBSyxFQUFFLElBQUksQ0FBQyxTQUFTLENBQUMsR0FBRztZQUN6QixXQUFXLEVBQUUsaURBQWlEO1NBQy9ELENBQUMsQ0FBQztRQUVILElBQUksdUJBQVMsQ0FBQyxJQUFJLEVBQUUsNEJBQTRCLEVBQUU7WUFDaEQsS0FBSyxFQUFFLGNBQWMsQ0FBQyxHQUFHO1lBQ3pCLFdBQVcsRUFBRSx1Q0FBdUM7U0FDckQsQ0FBQyxDQUFDO1FBRUgsSUFBSSx1QkFBUyxDQUFDLElBQUksRUFBRSwyQkFBMkIsRUFBRTtZQUMvQyxLQUFLLEVBQUUsY0FBYyxDQUFDLE9BQU87WUFDN0IsV0FBVyxFQUFFLHNDQUFzQztTQUNwRCxDQUFDLENBQUM7UUFFSCxJQUFJLHVCQUFTLENBQUMsSUFBSSxFQUFFLG1CQUFtQixFQUFFO1lBQ3ZDLEtBQUssRUFBRSxhQUFhLENBQUMsWUFBWTtZQUNqQyxXQUFXLEVBQUUsK0NBQStDO1NBQzdELENBQUMsQ0FBQztRQUVILElBQUksdUJBQVMsQ0FBQyxJQUFJLEVBQUUsMkJBQTJCLEVBQUU7WUFDL0MsS0FBSyxFQUFFLHFCQUFxQixDQUFDLFNBQVM7WUFDdEMsV0FBVyxFQUFFLDhDQUE4QztTQUM1RCxDQUFDLENBQUM7UUFFSCxJQUFJLGlCQUFpQixFQUFFLENBQUM7WUFDdEIsSUFBSSx1QkFBUyxDQUFDLElBQUksRUFBRSxzQkFBc0IsRUFBRTtnQkFDMUMsS0FBSyxFQUFFLGlCQUFpQixDQUFDLFFBQVE7Z0JBQ2pDLFdBQVcsRUFBRSx1Q0FBdUM7YUFDckQsQ0FBQyxDQUFDO1FBQ0wsQ0FBQztRQUVELElBQUksdUJBQVMsQ0FBQyxJQUFJLEVBQUUsNkJBQTZCLEVBQUU7WUFDakQsS0FBSyxFQUFFLGlCQUFpQixJQUFJLENBQUMsWUFBWSxDQUFDLEdBQUcsMkZBQTJGO1lBQ3hJLFdBQVcsRUFBRSwyQ0FBMkM7U0FDekQsQ0FBQyxDQUFDO0lBQ0wsQ0FBQztDQUNGO0FBdHNCRCw4REFzc0JDIiwic291cmNlc0NvbnRlbnQiOlsiaW1wb3J0ICogYXMgY2RrIGZyb20gJ2F3cy1jZGstbGliJztcbmltcG9ydCB7IENvbnN0cnVjdCB9IGZyb20gJ2NvbnN0cnVjdHMnO1xuaW1wb3J0IHsgRHVyYXRpb24sIFJlbW92YWxQb2xpY3ksIENmbk91dHB1dCwgVGFncyB9IGZyb20gJ2F3cy1jZGstbGliJztcbmltcG9ydCAqIGFzIHMzIGZyb20gJ2F3cy1jZGstbGliL2F3cy1zMyc7XG5pbXBvcnQgKiBhcyBzM05vdGlmaWNhdGlvbnMgZnJvbSAnYXdzLWNkay1saWIvYXdzLXMzLW5vdGlmaWNhdGlvbnMnO1xuaW1wb3J0ICogYXMgZmlyZWhvc2UgZnJvbSAnYXdzLWNkay1saWIvYXdzLWtpbmVzaXNmaXJlaG9zZSc7XG5pbXBvcnQgKiBhcyBpYW0gZnJvbSAnYXdzLWNkay1saWIvYXdzLWlhbSc7XG5pbXBvcnQgKiBhcyBnbHVlIGZyb20gJ2F3cy1jZGstbGliL2F3cy1nbHVlJztcbmltcG9ydCAqIGFzIGF0aGVuYSBmcm9tICdhd3MtY2RrLWxpYi9hd3MtYXRoZW5hJztcbmltcG9ydCAqIGFzIGxhbWJkYSBmcm9tICdhd3MtY2RrLWxpYi9hd3MtbGFtYmRhJztcbmltcG9ydCAqIGFzIGV2ZW50cyBmcm9tICdhd3MtY2RrLWxpYi9hd3MtZXZlbnRzJztcbmltcG9ydCAqIGFzIHRhcmdldHMgZnJvbSAnYXdzLWNkay1saWIvYXdzLWV2ZW50cy10YXJnZXRzJztcbmltcG9ydCAqIGFzIGxvZ3MgZnJvbSAnYXdzLWNkay1saWIvYXdzLWxvZ3MnO1xuaW1wb3J0ICogYXMgc25zIGZyb20gJ2F3cy1jZGstbGliL2F3cy1zbnMnO1xuaW1wb3J0ICogYXMgc3Vic2NyaXB0aW9ucyBmcm9tICdhd3MtY2RrLWxpYi9hd3Mtc25zLXN1YnNjcmlwdGlvbnMnO1xuaW1wb3J0ICogYXMgc2VzIGZyb20gJ2F3cy1jZGstbGliL2F3cy1zZXMnO1xuaW1wb3J0ICogYXMgZHluYW1vZGIgZnJvbSAnYXdzLWNkay1saWIvYXdzLWR5bmFtb2RiJztcbmltcG9ydCB7IE5hZ1N1cHByZXNzaW9ucyB9IGZyb20gJ2Nkay1uYWcnO1xuaW1wb3J0ICogYXMgcGF0aCBmcm9tICdwYXRoJztcblxuZXhwb3J0IGludGVyZmFjZSBTZXNDYW1wYWlnbkFuYWx5dGljc1N0YWNrUHJvcHMgZXh0ZW5kcyBjZGsuU3RhY2tQcm9wcyB7XG4gIGV4aXN0aW5nQ29uZmlndXJhdGlvblNldE5hbWU/OiBzdHJpbmc7XG4gIHJlZnJlc2hTY2hlZHVsZUNyb246IHN0cmluZztcbiAgZGF0YVJldGVudGlvbkRheXM6IG51bWJlcjtcbiAgZW5hYmxlTm90aWZpY2F0aW9uczogYm9vbGVhbjtcbiAgbm90aWZpY2F0aW9uRW1haWw/OiBzdHJpbmc7XG4gIGZpcmVob3NlQnVmZmVyU2l6ZU1COiBudW1iZXI7XG4gIGZpcmVob3NlQnVmZmVySW50ZXJ2YWxTZWNvbmRzOiBudW1iZXI7XG4gIGF0aGVuYVF1ZXJ5UmVzdWx0c1JldGVudGlvbkRheXM6IG51bWJlcjtcbiAgcHJvY2Vzc2VkRGF0YVRyYW5zaXRpb25Ub0lBRGF5czogbnVtYmVyO1xuICBsYW1iZGFUaW1lb3V0TWludXRlczogbnVtYmVyO1xuICBsYW1iZGFNZW1vcnlNQjogbnVtYmVyO1xuICBhdGhlbmFRdWVyeVNjYW5MaW1pdEdCOiBudW1iZXI7XG59XG5cbmV4cG9ydCBjbGFzcyBTZXNDYW1wYWlnbkFuYWx5dGljc1N0YWNrIGV4dGVuZHMgY2RrLlN0YWNrIHtcbiAgcHVibGljIHJlYWRvbmx5IHJhd0RhdGFCdWNrZXQ6IHMzLkJ1Y2tldDtcbiAgcHVibGljIHJlYWRvbmx5IHByb2Nlc3NlZERhdGFCdWNrZXQ6IHMzLkJ1Y2tldDtcbiAgcHVibGljIHJlYWRvbmx5IGF0aGVuYVJlc3VsdHNCdWNrZXQ6IHMzLkJ1Y2tldDtcbiAgcHVibGljIHJlYWRvbmx5IGdsdWVEYXRhYmFzZTogZ2x1ZS5DZm5EYXRhYmFzZTtcbiAgcHVibGljIHJlYWRvbmx5IHdvcmtHcm91cDogYXRoZW5hLkNmbldvcmtHcm91cDtcbiAgcHVibGljIHJlYWRvbmx5IGNvbmZpZ3VyYXRpb25TZXQ6IHNlcy5DZm5Db25maWd1cmF0aW9uU2V0O1xuXG4gIGNvbnN0cnVjdG9yKHNjb3BlOiBDb25zdHJ1Y3QsIGlkOiBzdHJpbmcsIHByb3BzOiBTZXNDYW1wYWlnbkFuYWx5dGljc1N0YWNrUHJvcHMpIHtcbiAgICBzdXBlcihzY29wZSwgaWQsIHtcbiAgICAgIC4uLnByb3BzLFxuICAgICAgZGVzY3JpcHRpb246ICdTRVMgQ2FtcGFpZ24gQW5hbHl0aWNzIC0gS2luZXNpcyBGaXJlaG9zZSwgQXRoZW5hLCBhbmQgR2x1ZSBmb3IgZGV0YWlsZWQgZW1haWwgY2FtcGFpZ24gbWV0cmljcyBhbmQgcmVwb3J0aW5nJyxcbiAgICB9KTtcblxuICAgIC8vIEFkZCBjb3N0IGFsbG9jYXRpb24gdGFncyBmb3IgdHJhY2tpbmcgZXhwZW5zZXNcbiAgICBUYWdzLm9mKHRoaXMpLmFkZCgnUHJvamVjdCcsICdTRVMtQ2FtcGFpZ24tQW5hbHl0aWNzJyk7XG4gICAgVGFncy5vZih0aGlzKS5hZGQoJ01hbmFnZWRCeScsICdDREsnKTtcbiAgICBUYWdzLm9mKHRoaXMpLmFkZCgnRW52aXJvbm1lbnQnLCAnUHJvZHVjdGlvbicpO1xuXG4gICAgLy8vLy8vLy8gU0VTIENPTkZJR1VSQVRJT04gU0VUIC8vLy8vLy8vXG4gICAgXG4gICAgLy8gVXNlIGV4aXN0aW5nIGNvbmZpZ3VyYXRpb24gc2V0IGlmIHByb3ZpZGVkLCBvdGhlcndpc2UgY3JlYXRlIGEgbmV3IG9uZVxuICAgIGxldCBjb25maWdTZXROYW1lOiBzdHJpbmc7XG4gICAgXG4gICAgaWYgKHByb3BzLmV4aXN0aW5nQ29uZmlndXJhdGlvblNldE5hbWUgJiYgcHJvcHMuZXhpc3RpbmdDb25maWd1cmF0aW9uU2V0TmFtZS50cmltKCkgIT09ICcnKSB7XG4gICAgICAvLyBVc2UgZXhpc3RpbmcgY29uZmlndXJhdGlvbiBzZXRcbiAgICAgIGNvbmZpZ1NldE5hbWUgPSBwcm9wcy5leGlzdGluZ0NvbmZpZ3VyYXRpb25TZXROYW1lO1xuICAgICAgY29uc29sZS5sb2coYFVzaW5nIGV4aXN0aW5nIFNFUyBDb25maWd1cmF0aW9uIFNldDogJHtjb25maWdTZXROYW1lfWApO1xuICAgIH0gZWxzZSB7XG4gICAgICAvLyBDcmVhdGUgbmV3IGNvbmZpZ3VyYXRpb24gc2V0XG4gICAgICB0aGlzLmNvbmZpZ3VyYXRpb25TZXQgPSBuZXcgc2VzLkNmbkNvbmZpZ3VyYXRpb25TZXQodGhpcywgJ1Nlc0NvbmZpZ3VyYXRpb25TZXQnLCB7XG4gICAgICAgIG5hbWU6IGBzZXMtYW5hbHl0aWNzLSR7Y2RrLlN0YWNrLm9mKHRoaXMpLmFjY291bnR9YCxcbiAgICAgIH0pO1xuICAgICAgY29uZmlnU2V0TmFtZSA9IHRoaXMuY29uZmlndXJhdGlvblNldC5uYW1lITtcbiAgICB9XG5cbiAgICAvLy8vLy8vLyBTMyBCVUNLRVRTIEZPUiBEQVRBIFNUT1JBR0UgLy8vLy8vLy9cbiAgICBcbiAgICAvLyBSYXcgU0VTIGV2ZW50cyBmcm9tIEZpcmVob3NlXG4gICAgdGhpcy5yYXdEYXRhQnVja2V0ID0gbmV3IHMzLkJ1Y2tldCh0aGlzLCAnU2VzUmF3RGF0YUJ1Y2tldCcsIHtcbiAgICAgIGVuY3J5cHRpb246IHMzLkJ1Y2tldEVuY3J5cHRpb24uUzNfTUFOQUdFRCxcbiAgICAgIGJsb2NrUHVibGljQWNjZXNzOiBzMy5CbG9ja1B1YmxpY0FjY2Vzcy5CTE9DS19BTEwsXG4gICAgICByZW1vdmFsUG9saWN5OiBSZW1vdmFsUG9saWN5LlJFVEFJTixcbiAgICAgIGVuZm9yY2VTU0w6IHRydWUsXG4gICAgICBsaWZlY3ljbGVSdWxlczogW1xuICAgICAgICB7XG4gICAgICAgICAgaWQ6ICdEZWxldGVPbGRSYXdEYXRhJyxcbiAgICAgICAgICBlbmFibGVkOiB0cnVlLFxuICAgICAgICAgIGV4cGlyYXRpb246IER1cmF0aW9uLmRheXMocHJvcHMuZGF0YVJldGVudGlvbkRheXMpLFxuICAgICAgICB9LFxuICAgICAgICB7XG4gICAgICAgICAgaWQ6ICdUcmFuc2l0aW9uVG9HbGFjaWVyJyxcbiAgICAgICAgICBlbmFibGVkOiB0cnVlLFxuICAgICAgICAgIHRyYW5zaXRpb25zOiBbXG4gICAgICAgICAgICB7XG4gICAgICAgICAgICAgIHN0b3JhZ2VDbGFzczogczMuU3RvcmFnZUNsYXNzLkdMQUNJRVIsXG4gICAgICAgICAgICAgIHRyYW5zaXRpb25BZnRlcjogRHVyYXRpb24uZGF5cyhNYXRoLmZsb29yKHByb3BzLmRhdGFSZXRlbnRpb25EYXlzIC8gMykpLFxuICAgICAgICAgICAgfSxcbiAgICAgICAgICBdLFxuICAgICAgICB9LFxuICAgICAgXSxcbiAgICB9KTtcblxuICAgIC8vIFByb2Nlc3NlZC90cmFuc2Zvcm1lZCBkYXRhXG4gICAgdGhpcy5wcm9jZXNzZWREYXRhQnVja2V0ID0gbmV3IHMzLkJ1Y2tldCh0aGlzLCAnU2VzUHJvY2Vzc2VkRGF0YUJ1Y2tldCcsIHtcbiAgICAgIGVuY3J5cHRpb246IHMzLkJ1Y2tldEVuY3J5cHRpb24uUzNfTUFOQUdFRCxcbiAgICAgIGJsb2NrUHVibGljQWNjZXNzOiBzMy5CbG9ja1B1YmxpY0FjY2Vzcy5CTE9DS19BTEwsXG4gICAgICByZW1vdmFsUG9saWN5OiBSZW1vdmFsUG9saWN5LlJFVEFJTixcbiAgICAgIGVuZm9yY2VTU0w6IHRydWUsXG4gICAgICBsaWZlY3ljbGVSdWxlczogW1xuICAgICAgICB7XG4gICAgICAgICAgaWQ6ICdUcmFuc2l0aW9uVG9JQScsXG4gICAgICAgICAgZW5hYmxlZDogdHJ1ZSxcbiAgICAgICAgICB0cmFuc2l0aW9uczogW1xuICAgICAgICAgICAge1xuICAgICAgICAgICAgICBzdG9yYWdlQ2xhc3M6IHMzLlN0b3JhZ2VDbGFzcy5JTkZSRVFVRU5UX0FDQ0VTUyxcbiAgICAgICAgICAgICAgdHJhbnNpdGlvbkFmdGVyOiBEdXJhdGlvbi5kYXlzKHByb3BzLnByb2Nlc3NlZERhdGFUcmFuc2l0aW9uVG9JQURheXMpLFxuICAgICAgICAgICAgfSxcbiAgICAgICAgICBdLFxuICAgICAgICB9LFxuICAgICAgXSxcbiAgICB9KTtcblxuICAgIC8vIEF0aGVuYSBxdWVyeSByZXN1bHRzXG4gICAgdGhpcy5hdGhlbmFSZXN1bHRzQnVja2V0ID0gbmV3IHMzLkJ1Y2tldCh0aGlzLCAnQXRoZW5hUmVzdWx0c0J1Y2tldCcsIHtcbiAgICAgIGVuY3J5cHRpb246IHMzLkJ1Y2tldEVuY3J5cHRpb24uUzNfTUFOQUdFRCxcbiAgICAgIGJsb2NrUHVibGljQWNjZXNzOiBzMy5CbG9ja1B1YmxpY0FjY2Vzcy5CTE9DS19BTEwsXG4gICAgICByZW1vdmFsUG9saWN5OiBSZW1vdmFsUG9saWN5LlJFVEFJTixcbiAgICAgIGVuZm9yY2VTU0w6IHRydWUsXG4gICAgICBsaWZlY3ljbGVSdWxlczogW1xuICAgICAgICB7XG4gICAgICAgICAgaWQ6ICdEZWxldGVPbGRRdWVyeVJlc3VsdHMnLFxuICAgICAgICAgIGVuYWJsZWQ6IHRydWUsXG4gICAgICAgICAgZXhwaXJhdGlvbjogRHVyYXRpb24uZGF5cyhwcm9wcy5hdGhlbmFRdWVyeVJlc3VsdHNSZXRlbnRpb25EYXlzKSxcbiAgICAgICAgfSxcbiAgICAgIF0sXG4gICAgfSk7XG5cbiAgICAvLy8vLy8vLyBBV1MgR0xVRSBEQVRBQkFTRSBBTkQgVEFCTEVTIC8vLy8vLy8vXG4gICAgXG4gICAgdGhpcy5nbHVlRGF0YWJhc2UgPSBuZXcgZ2x1ZS5DZm5EYXRhYmFzZSh0aGlzLCAnU2VzRXZlbnREYXRhYmFzZScsIHtcbiAgICAgIGNhdGFsb2dJZDogY2RrLlN0YWNrLm9mKHRoaXMpLmFjY291bnQsXG4gICAgICBkYXRhYmFzZUlucHV0OiB7XG4gICAgICAgIG5hbWU6IGBzZXNfYW5hbHl0aWNzX2RiXyR7Y2RrLlN0YWNrLm9mKHRoaXMpLmFjY291bnR9YCxcbiAgICAgICAgZGVzY3JpcHRpb246ICdEYXRhYmFzZSBmb3IgU0VTIGNhbXBhaWduIGFuYWx5dGljcyBldmVudHMgYW5kIG1hdGVyaWFsaXplZCB2aWV3cycsXG4gICAgICB9LFxuICAgIH0pO1xuXG4gICAgLy8gQ3JlYXRlIEF0aGVuYSBOYW1lZCBRdWVyeSBmb3IgdGFibGUgY3JlYXRpb24gKGZvbGxvd2luZyBDbG91ZEZvcm1hdGlvbiBwYXR0ZXJuKVxuICAgIGNvbnN0IGNyZWF0ZVRhYmxlUXVlcnkgPSBuZXcgYXRoZW5hLkNmbk5hbWVkUXVlcnkodGhpcywgJ0NyZWF0ZVNlc0V2ZW50c1RhYmxlJywge1xuICAgICAgZGF0YWJhc2U6IHRoaXMuZ2x1ZURhdGFiYXNlLnJlZixcbiAgICAgIGRlc2NyaXB0aW9uOiAnQ3JlYXRlIHRhYmxlIGZvciBTRVMgZXZlbnRzJyxcbiAgICAgIG5hbWU6ICdjcmVhdGVfc2VzX2V2ZW50c190YWJsZScsXG4gICAgICBxdWVyeVN0cmluZzogYENSRUFURSBFWFRFUk5BTCBUQUJMRSBJRiBOT1QgRVhJU1RTIHNlc19ldmVudHNfcmF3IChcbiAgZXZlbnRUeXBlIHN0cmluZyxcbiAgbWFpbCBzdHJ1Y3Q8XG4gICAgdGltZXN0YW1wOiBzdHJpbmcsXG4gICAgc291cmNlOiBzdHJpbmcsXG4gICAgc291cmNlQXJuOiBzdHJpbmcsXG4gICAgc2VuZGluZ0FjY291bnRJZDogc3RyaW5nLFxuICAgIG1lc3NhZ2VJZDogc3RyaW5nLFxuICAgIGRlc3RpbmF0aW9uOiBhcnJheTxzdHJpbmc+LFxuICAgIGhlYWRlcnNUcnVuY2F0ZWQ6IGJvb2xlYW4sXG4gICAgaGVhZGVyczogYXJyYXk8c3RydWN0PG5hbWU6IHN0cmluZywgdmFsdWU6IHN0cmluZz4+LFxuICAgIGNvbW1vbkhlYWRlcnM6IHN0cnVjdDxcXGBmcm9tXFxgOiBhcnJheTxzdHJpbmc+LCBcXGB0b1xcYDogYXJyYXk8c3RyaW5nPiwgbWVzc2FnZUlkOiBzdHJpbmcsIHN1YmplY3Q6IHN0cmluZz4sXG4gICAgdGFnczogc3RydWN0PFxuICAgICAgY2FtcGFpZ25faWQ6IGFycmF5PHN0cmluZz4sXG4gICAgICBjYW1wYWlnbl9uYW1lOiBhcnJheTxzdHJpbmc+XG4gICAgPlxuICA+LFxuICBzZW5kIG1hcDxzdHJpbmcsc3RyaW5nPixcbiAgZGVsaXZlcnkgc3RydWN0PFxuICAgIHRpbWVzdGFtcDogc3RyaW5nLFxuICAgIHByb2Nlc3NpbmdUaW1lTWlsbGlzOiBiaWdpbnQsXG4gICAgcmVjaXBpZW50czogYXJyYXk8c3RyaW5nPixcbiAgICBzbXRwUmVzcG9uc2U6IHN0cmluZyxcbiAgICByZXBvcnRpbmdNVEE6IHN0cmluZ1xuICA+LFxuICBvcGVuIHN0cnVjdDxcbiAgICBpcEFkZHJlc3M6IHN0cmluZyxcbiAgICB0aW1lc3RhbXA6IHN0cmluZyxcbiAgICB1c2VyQWdlbnQ6IHN0cmluZ1xuICA+LFxuICBjbGljayBzdHJ1Y3Q8XG4gICAgaXBBZGRyZXNzOiBzdHJpbmcsXG4gICAgbGluazogc3RyaW5nLFxuICAgIGxpbmtUYWdzOiBtYXA8c3RyaW5nLGFycmF5PHN0cmluZz4+LFxuICAgIHRpbWVzdGFtcDogc3RyaW5nLFxuICAgIHVzZXJBZ2VudDogc3RyaW5nXG4gID4sXG4gIGJvdW5jZSBzdHJ1Y3Q8XG4gICAgYm91bmNlVHlwZTogc3RyaW5nLFxuICAgIGJvdW5jZVN1YlR5cGU6IHN0cmluZyxcbiAgICBib3VuY2VkUmVjaXBpZW50czogYXJyYXk8c3RydWN0PFxuICAgICAgZW1haWxBZGRyZXNzOiBzdHJpbmcsXG4gICAgICBhY3Rpb246IHN0cmluZyxcbiAgICAgIHN0YXR1czogc3RyaW5nLFxuICAgICAgZGlhZ25vc3RpY0NvZGU6IHN0cmluZ1xuICAgID4+LFxuICAgIHRpbWVzdGFtcDogc3RyaW5nLFxuICAgIGZlZWRiYWNrSWQ6IHN0cmluZyxcbiAgICByZXBvcnRpbmdNVEE6IHN0cmluZ1xuICA+LFxuICBjb21wbGFpbnQgc3RydWN0PFxuICAgIGNvbXBsYWluZWRSZWNpcGllbnRzOiBhcnJheTxzdHJ1Y3Q8XG4gICAgICBlbWFpbEFkZHJlc3M6IHN0cmluZ1xuICAgID4+LFxuICAgIHRpbWVzdGFtcDogc3RyaW5nLFxuICAgIGZlZWRiYWNrSWQ6IHN0cmluZyxcbiAgICB1c2VyQWdlbnQ6IHN0cmluZyxcbiAgICBjb21wbGFpbnRGZWVkYmFja1R5cGU6IHN0cmluZyxcbiAgICBhcnJpdmFsRGF0ZTogc3RyaW5nXG4gID4sXG4gIHJlamVjdCBzdHJ1Y3Q8XG4gICAgcmVhc29uOiBzdHJpbmdcbiAgPixcbiAgcmVuZGVyaW5nRmFpbHVyZSBzdHJ1Y3Q8XG4gICAgZXJyb3JNZXNzYWdlOiBzdHJpbmcsXG4gICAgdGVtcGxhdGVOYW1lOiBzdHJpbmdcbiAgPlxuKVxuUEFSVElUSU9ORUQgQlkgKGluZ2VzdF90aW1lc3RhbXAgdGltZXN0YW1wKVxuU1RPUkVEIEFTIHBhcnF1ZXRcbkxPQ0FUSU9OIFwiczM6Ly8ke3RoaXMucmF3RGF0YUJ1Y2tldC5idWNrZXROYW1lfS9ldmVudHNcIlxuVEJMUFJPUEVSVElFUyAoXG4gIFwicGFycXVldC5jb21wcmVzc2lvblwiPVwiU05BUFBZXCIsXG4gIFwicHJvamVjdGlvbi5lbmFibGVkXCI9XCJmYWxzZVwiXG4pYCxcbiAgICB9KTtcblxuICAgIC8vIExhbWJkYSB0byBleGVjdXRlIHRoZSBuYW1lZCBxdWVyeSBhbmQgY3JlYXRlIHRoZSB0YWJsZVxuICAgIGNvbnN0IGNyZWF0ZVRhYmxlTGFtYmRhID0gbmV3IGxhbWJkYS5GdW5jdGlvbih0aGlzLCAnQ3JlYXRlVGFibGVMYW1iZGEnLCB7XG4gICAgICBydW50aW1lOiBsYW1iZGEuUnVudGltZS5QWVRIT05fM18xMixcbiAgICAgIGhhbmRsZXI6ICdjcmVhdGVUYWJsZS5oYW5kbGVyJyxcbiAgICAgIGNvZGU6IGxhbWJkYS5Db2RlLmZyb21Bc3NldChwYXRoLmpvaW4oX19kaXJuYW1lLCAnLi4vbGFtYmRhJykpLFxuICAgICAgdGltZW91dDogRHVyYXRpb24ubWludXRlcygxMCksIC8vIEluY3JlYXNlZCBmcm9tIDUgdG8gMTAgbWludXRlcyBmb3IgQXRoZW5hIHF1ZXJ5IGNvbXBsZXRpb25cbiAgICAgIGVudmlyb25tZW50OiB7XG4gICAgICAgIE5BTUVEX1FVRVJZX0lEOiBjcmVhdGVUYWJsZVF1ZXJ5LmF0dHJOYW1lZFF1ZXJ5SWQsXG4gICAgICAgIERBVEFCQVNFX05BTUU6IHRoaXMuZ2x1ZURhdGFiYXNlLnJlZixcbiAgICAgICAgT1VUUFVUX0xPQ0FUSU9OOiBgczM6Ly8ke3RoaXMuYXRoZW5hUmVzdWx0c0J1Y2tldC5idWNrZXROYW1lfS90YWJsZS1jcmVhdGlvbi9gLFxuICAgICAgfSxcbiAgICB9KTtcblxuICAgIHRoaXMuYXRoZW5hUmVzdWx0c0J1Y2tldC5ncmFudFJlYWRXcml0ZShjcmVhdGVUYWJsZUxhbWJkYSk7XG4gICAgXG4gICAgY3JlYXRlVGFibGVMYW1iZGEuYWRkVG9Sb2xlUG9saWN5KFxuICAgICAgbmV3IGlhbS5Qb2xpY3lTdGF0ZW1lbnQoe1xuICAgICAgICBhY3Rpb25zOiBbXG4gICAgICAgICAgJ2F0aGVuYTpHZXROYW1lZFF1ZXJ5JyxcbiAgICAgICAgICAnYXRoZW5hOlN0YXJ0UXVlcnlFeGVjdXRpb24nLFxuICAgICAgICAgICdhdGhlbmE6R2V0UXVlcnlFeGVjdXRpb24nLFxuICAgICAgICBdLFxuICAgICAgICByZXNvdXJjZXM6IFsnKiddLFxuICAgICAgfSksXG4gICAgKTtcblxuICAgIGNyZWF0ZVRhYmxlTGFtYmRhLmFkZFRvUm9sZVBvbGljeShcbiAgICAgIG5ldyBpYW0uUG9saWN5U3RhdGVtZW50KHtcbiAgICAgICAgYWN0aW9uczogW1xuICAgICAgICAgICdnbHVlOkdldERhdGFiYXNlJyxcbiAgICAgICAgICAnZ2x1ZTpHZXRUYWJsZScsXG4gICAgICAgICAgJ2dsdWU6Q3JlYXRlVGFibGUnLFxuICAgICAgICBdLFxuICAgICAgICByZXNvdXJjZXM6IFtcbiAgICAgICAgICBgYXJuOmF3czpnbHVlOiR7Y2RrLlN0YWNrLm9mKHRoaXMpLnJlZ2lvbn06JHtjZGsuU3RhY2sub2YodGhpcykuYWNjb3VudH06Y2F0YWxvZ2AsXG4gICAgICAgICAgYGFybjphd3M6Z2x1ZToke2Nkay5TdGFjay5vZih0aGlzKS5yZWdpb259OiR7Y2RrLlN0YWNrLm9mKHRoaXMpLmFjY291bnR9OmRhdGFiYXNlLyR7dGhpcy5nbHVlRGF0YWJhc2UucmVmfWAsXG4gICAgICAgICAgYGFybjphd3M6Z2x1ZToke2Nkay5TdGFjay5vZih0aGlzKS5yZWdpb259OiR7Y2RrLlN0YWNrLm9mKHRoaXMpLmFjY291bnR9OnRhYmxlLyR7dGhpcy5nbHVlRGF0YWJhc2UucmVmfS8qYCxcbiAgICAgICAgXSxcbiAgICAgIH0pLFxuICAgICk7XG5cbiAgICAvLyBTdXBwcmVzcyBDREsgTmFnIHdhcm5pbmcgZm9yIEdsdWUgdGFibGUgd2lsZGNhcmQgcGVybWlzc2lvbnNcbiAgICBOYWdTdXBwcmVzc2lvbnMuYWRkUmVzb3VyY2VTdXBwcmVzc2lvbnMoXG4gICAgICBjcmVhdGVUYWJsZUxhbWJkYSxcbiAgICAgIFtcbiAgICAgICAge1xuICAgICAgICAgIGlkOiAnQXdzU29sdXRpb25zLUlBTTUnLFxuICAgICAgICAgIHJlYXNvbjogJ0xhbWJkYSBuZWVkcyB3aWxkY2FyZCBwZXJtaXNzaW9ucyB0byBhY2Nlc3MgR2x1ZSB0YWJsZSBtZXRhZGF0YSBmb3IgQXRoZW5hIHF1ZXJ5IGV4ZWN1dGlvbiB0aGF0IGNyZWF0ZXMgdGFibGVzLicsXG4gICAgICAgIH0sXG4gICAgICBdLFxuICAgICAgdHJ1ZSwgLy8gYXBwbHlUb0NoaWxkcmVuIC0gYXBwbGllcyB0byB0aGUgTGFtYmRhJ3Mgcm9sZSBhbmQgcG9saWNpZXNcbiAgICApO1xuXG4gICAgLy8gQ3VzdG9tIHJlc291cmNlIHRvIHRyaWdnZXIgdGFibGUgY3JlYXRpb25cbiAgICBjb25zdCB0YWJsZUNyZWF0aW9uVHJpZ2dlciA9IG5ldyBjZGsuQ3VzdG9tUmVzb3VyY2UodGhpcywgJ1RhYmxlQ3JlYXRpb25UcmlnZ2VyJywge1xuICAgICAgc2VydmljZVRva2VuOiBjcmVhdGVUYWJsZUxhbWJkYS5mdW5jdGlvbkFybixcbiAgICB9KTtcblxuICAgIHRhYmxlQ3JlYXRpb25UcmlnZ2VyLm5vZGUuYWRkRGVwZW5kZW5jeSh0aGlzLmdsdWVEYXRhYmFzZSk7XG4gICAgdGFibGVDcmVhdGlvblRyaWdnZXIubm9kZS5hZGREZXBlbmRlbmN5KGNyZWF0ZVRhYmxlUXVlcnkpO1xuXG4gICAgLy8gUmVmZXJlbmNlIGZvciBGaXJlaG9zZSAtIHVzZSB0aGUgdGFibGUgbmFtZSBkaXJlY3RseVxuICAgIGNvbnN0IHJhd0V2ZW50c1RhYmxlTmFtZSA9ICdzZXNfZXZlbnRzX3Jhdyc7XG5cbiAgICAvLyBNYXRlcmlhbGl6ZWQgdmlldyB0YWJsZSBmb3IgY2FtcGFpZ24gbWV0cmljc1xuICAgIGNvbnN0IGNhbXBhaWduTWV0cmljc1RhYmxlID0gbmV3IGdsdWUuQ2ZuVGFibGUodGhpcywgJ0NhbXBhaWduTWV0cmljc1RhYmxlJywge1xuICAgICAgY2F0YWxvZ0lkOiBjZGsuU3RhY2sub2YodGhpcykuYWNjb3VudCxcbiAgICAgIGRhdGFiYXNlTmFtZTogdGhpcy5nbHVlRGF0YWJhc2UucmVmLFxuICAgICAgdGFibGVJbnB1dDoge1xuICAgICAgICBuYW1lOiAnY2FtcGFpZ25fbWV0cmljc19kYWlseScsXG4gICAgICAgIGRlc2NyaXB0aW9uOiAnTWF0ZXJpYWxpemVkIHZpZXcgb2YgZGFpbHkgY2FtcGFpZ24gbWV0cmljcycsXG4gICAgICAgIHRhYmxlVHlwZTogJ0VYVEVSTkFMX1RBQkxFJyxcbiAgICAgICAgcGFyYW1ldGVyczoge1xuICAgICAgICAgICdwcm9qZWN0aW9uLmVuYWJsZWQnOiAndHJ1ZScsXG4gICAgICAgICAgJ3Byb2plY3Rpb24uZGF0ZS50eXBlJzogJ2RhdGUnLFxuICAgICAgICAgICdwcm9qZWN0aW9uLmRhdGUucmFuZ2UnOiAnMjAyNC0wMS0wMSxOT1cnLFxuICAgICAgICAgICdwcm9qZWN0aW9uLmRhdGUuZm9ybWF0JzogJ3l5eXktTU0tZGQnLFxuICAgICAgICAgICdzdG9yYWdlLmxvY2F0aW9uLnRlbXBsYXRlJzogYHMzOi8vJHt0aGlzLnByb2Nlc3NlZERhdGFCdWNrZXQuYnVja2V0TmFtZX0vbWF0ZXJpYWxpemVkLXZpZXdzL2NhbXBhaWduX21ldHJpY3NfZGFpbHkvZGF0ZT1cXCR7ZGF0ZX1gLFxuICAgICAgICB9LFxuICAgICAgICBwYXJ0aXRpb25LZXlzOiBbXG4gICAgICAgICAgeyBuYW1lOiAnZGF0ZScsIHR5cGU6ICdzdHJpbmcnIH0sXG4gICAgICAgIF0sXG4gICAgICAgIHN0b3JhZ2VEZXNjcmlwdG9yOiB7XG4gICAgICAgICAgY29sdW1uczogW1xuICAgICAgICAgICAgeyBuYW1lOiAnY2FtcGFpZ25faWQnLCB0eXBlOiAnc3RyaW5nJyB9LFxuICAgICAgICAgICAgeyBuYW1lOiAnY2FtcGFpZ25fbmFtZScsIHR5cGU6ICdzdHJpbmcnIH0sXG4gICAgICAgICAgICB7IG5hbWU6ICdlbWFpbHNfc2VudCcsIHR5cGU6ICdiaWdpbnQnIH0sXG4gICAgICAgICAgICB7IG5hbWU6ICdlbWFpbHNfZGVsaXZlcmVkJywgdHlwZTogJ2JpZ2ludCcgfSxcbiAgICAgICAgICAgIHsgbmFtZTogJ2VtYWlsc19vcGVuZWQnLCB0eXBlOiAnYmlnaW50JyB9LFxuICAgICAgICAgICAgeyBuYW1lOiAnZW1haWxzX2NsaWNrZWQnLCB0eXBlOiAnYmlnaW50JyB9LFxuICAgICAgICAgICAgeyBuYW1lOiAnaGFyZF9ib3VuY2VzJywgdHlwZTogJ2JpZ2ludCcgfSxcbiAgICAgICAgICAgIHsgbmFtZTogJ3NvZnRfYm91bmNlcycsIHR5cGU6ICdiaWdpbnQnIH0sXG4gICAgICAgICAgICB7IG5hbWU6ICdjb21wbGFpbnRzJywgdHlwZTogJ2JpZ2ludCcgfSxcbiAgICAgICAgICAgIHsgbmFtZTogJ3JlamVjdHMnLCB0eXBlOiAnYmlnaW50JyB9LFxuICAgICAgICAgICAgeyBuYW1lOiAncmVuZGVyaW5nX2ZhaWx1cmVzJywgdHlwZTogJ2JpZ2ludCcgfSxcbiAgICAgICAgICAgIHsgbmFtZTogJ2RlbGl2ZXJ5X3JhdGUnLCB0eXBlOiAnZG91YmxlJyB9LFxuICAgICAgICAgICAgeyBuYW1lOiAnb3Blbl9yYXRlJywgdHlwZTogJ2RvdWJsZScgfSxcbiAgICAgICAgICAgIHsgbmFtZTogJ2NsaWNrX3JhdGUnLCB0eXBlOiAnZG91YmxlJyB9LFxuICAgICAgICAgICAgeyBuYW1lOiAnaGFyZF9ib3VuY2VfcmF0ZScsIHR5cGU6ICdkb3VibGUnIH0sXG4gICAgICAgICAgICB7IG5hbWU6ICdjb21wbGFpbnRfcmF0ZScsIHR5cGU6ICdkb3VibGUnIH0sXG4gICAgICAgICAgICB7IG5hbWU6ICdyZW5kZXJpbmdfZmFpbHVyZV9yYXRlJywgdHlwZTogJ2RvdWJsZScgfSxcbiAgICAgICAgICAgIHsgbmFtZTogJ3VuaXF1ZV9yZWNpcGllbnRzJywgdHlwZTogJ2JpZ2ludCcgfSxcbiAgICAgICAgICAgIHsgbmFtZTogJ2F2Z19kZWxpdmVyeV90aW1lX21zJywgdHlwZTogJ2RvdWJsZScgfSxcbiAgICAgICAgICAgIHsgbmFtZTogJ2Zyb21fYWRkcmVzcycsIHR5cGU6ICdzdHJpbmcnIH0sXG4gICAgICAgICAgICB7IG5hbWU6ICdzYW1wbGVfc3ViamVjdCcsIHR5cGU6ICdzdHJpbmcnIH0sXG4gICAgICAgICAgXSxcbiAgICAgICAgICBsb2NhdGlvbjogYHMzOi8vJHt0aGlzLnByb2Nlc3NlZERhdGFCdWNrZXQuYnVja2V0TmFtZX0vbWF0ZXJpYWxpemVkLXZpZXdzL2NhbXBhaWduX21ldHJpY3NfZGFpbHkvYCxcbiAgICAgICAgICBpbnB1dEZvcm1hdDogJ29yZy5hcGFjaGUuaGFkb29wLmhpdmUucWwuaW8ucGFycXVldC5NYXByZWRQYXJxdWV0SW5wdXRGb3JtYXQnLFxuICAgICAgICAgIG91dHB1dEZvcm1hdDogJ29yZy5hcGFjaGUuaGFkb29wLmhpdmUucWwuaW8ucGFycXVldC5NYXByZWRQYXJxdWV0T3V0cHV0Rm9ybWF0JyxcbiAgICAgICAgICBzZXJkZUluZm86IHtcbiAgICAgICAgICAgIHNlcmlhbGl6YXRpb25MaWJyYXJ5OiAnb3JnLmFwYWNoZS5oYWRvb3AuaGl2ZS5xbC5pby5wYXJxdWV0LnNlcmRlLlBhcnF1ZXRIaXZlU2VyRGUnLFxuICAgICAgICAgICAgcGFyYW1ldGVyczoge1xuICAgICAgICAgICAgICAnc2VyaWFsaXphdGlvbi5mb3JtYXQnOiAnMScsXG4gICAgICAgICAgICB9LFxuICAgICAgICAgIH0sXG4gICAgICAgIH0sXG4gICAgICB9LFxuICAgIH0pO1xuXG4gICAgLy8vLy8vLy8gQVRIRU5BIFdPUktHUk9VUCAvLy8vLy8vL1xuICAgIFxuICAgIHRoaXMud29ya0dyb3VwID0gbmV3IGF0aGVuYS5DZm5Xb3JrR3JvdXAodGhpcywgJ1Nlc0FuYWx5dGljc1dvcmtHcm91cCcsIHtcbiAgICAgIG5hbWU6IGBzZXMtYW5hbHl0aWNzLXdnLSR7Y2RrLlN0YWNrLm9mKHRoaXMpLmFjY291bnR9YCxcbiAgICAgIGRlc2NyaXB0aW9uOiAnV29ya2dyb3VwIGZvciBTRVMgY2FtcGFpZ24gYW5hbHl0aWNzIHF1ZXJpZXMnLFxuICAgICAgcmVjdXJzaXZlRGVsZXRlT3B0aW9uOiB0cnVlLCAvLyBFbmFibGUgcmVjdXJzaXZlIGRlbGV0aW9uIHRvIGNsZWFuIHVwIHF1ZXJpZXNcbiAgICAgIHdvcmtHcm91cENvbmZpZ3VyYXRpb246IHtcbiAgICAgICAgcmVzdWx0Q29uZmlndXJhdGlvbjoge1xuICAgICAgICAgIG91dHB1dExvY2F0aW9uOiBgczM6Ly8ke3RoaXMuYXRoZW5hUmVzdWx0c0J1Y2tldC5idWNrZXROYW1lfS9xdWVyeS1yZXN1bHRzL2AsXG4gICAgICAgICAgZW5jcnlwdGlvbkNvbmZpZ3VyYXRpb246IHtcbiAgICAgICAgICAgIGVuY3J5cHRpb25PcHRpb246ICdTU0VfUzMnLFxuICAgICAgICAgIH0sXG4gICAgICAgIH0sXG4gICAgICAgIGVuZm9yY2VXb3JrR3JvdXBDb25maWd1cmF0aW9uOiB0cnVlLFxuICAgICAgICBwdWJsaXNoQ2xvdWRXYXRjaE1ldHJpY3NFbmFibGVkOiB0cnVlLFxuICAgICAgICBieXRlc1NjYW5uZWRDdXRvZmZQZXJRdWVyeTogcHJvcHMuYXRoZW5hUXVlcnlTY2FuTGltaXRHQiAqIDEwMjQgKiAxMDI0ICogMTAyNCxcbiAgICAgICAgZW5naW5lVmVyc2lvbjoge1xuICAgICAgICAgIHNlbGVjdGVkRW5naW5lVmVyc2lvbjogJ0F0aGVuYSBlbmdpbmUgdmVyc2lvbiAzJyxcbiAgICAgICAgfSxcbiAgICAgIH0sXG4gICAgfSk7XG4gICAgXG4gICAgLy8gQXBwbHkgcmVtb3ZhbCBwb2xpY3kgdG8gYWxsb3cgZGVsZXRpb25cbiAgICB0aGlzLndvcmtHcm91cC5hcHBseVJlbW92YWxQb2xpY3koUmVtb3ZhbFBvbGljeS5ERVNUUk9ZKTtcblxuICAgIC8vLy8vLy8vIElBTSBST0xFUyBGT1IgS0lORVNJUyBGSVJFSE9TRSAvLy8vLy8vL1xuICAgIFxuICAgIC8vIFJvbGUgZm9yIEZpcmVob3NlIHNlcnZpY2UgaXRzZWxmXG4gICAgY29uc3QgZmlyZWhvc2VSb2xlID0gbmV3IGlhbS5Sb2xlKHRoaXMsICdGaXJlaG9zZVJvbGUnLCB7XG4gICAgICBhc3N1bWVkQnk6IG5ldyBpYW0uU2VydmljZVByaW5jaXBhbCgnZmlyZWhvc2UuYW1hem9uYXdzLmNvbScpLFxuICAgICAgZGVzY3JpcHRpb246ICdSb2xlIGZvciBLaW5lc2lzIEZpcmVob3NlIHRvIHdyaXRlIFNFUyBldmVudHMgdG8gUzMgYW5kIGNvbnZlcnQgdG8gUGFycXVldCcsXG4gICAgfSk7XG5cbiAgICB0aGlzLnJhd0RhdGFCdWNrZXQuZ3JhbnRXcml0ZShmaXJlaG9zZVJvbGUpO1xuICAgIFxuICAgIC8vIFNlcGFyYXRlIHJvbGUgZm9yIFNFUyB0byBwdXQgcmVjb3JkcyBpbnRvIEZpcmVob3NlXG4gICAgY29uc3Qgc2VzRmlyZWhvc2VSb2xlID0gbmV3IGlhbS5Sb2xlKHRoaXMsICdTZXNGaXJlaG9zZVJvbGUnLCB7XG4gICAgICBhc3N1bWVkQnk6IG5ldyBpYW0uU2VydmljZVByaW5jaXBhbCgnc2VzLmFtYXpvbmF3cy5jb20nKSxcbiAgICAgIGRlc2NyaXB0aW9uOiAnUm9sZSBmb3IgU0VTIHRvIHB1dCByZWNvcmRzIGludG8gS2luZXNpcyBGaXJlaG9zZScsXG4gICAgfSk7XG5cbiAgICAvLyBDbG91ZFdhdGNoIExvZ3MgZm9yIEZpcmVob3NlXG4gICAgY29uc3QgZmlyZWhvc2VMb2dHcm91cCA9IG5ldyBsb2dzLkxvZ0dyb3VwKHRoaXMsICdGaXJlaG9zZUxvZ0dyb3VwJywge1xuICAgICAgbG9nR3JvdXBOYW1lOiBgL2F3cy9raW5lc2lzZmlyZWhvc2Uvc2VzLWFuYWx5dGljcy0ke2Nkay5TdGFjay5vZih0aGlzKS5hY2NvdW50fWAsXG4gICAgICByZXRlbnRpb246IGxvZ3MuUmV0ZW50aW9uRGF5cy5PTkVfV0VFSyxcbiAgICAgIHJlbW92YWxQb2xpY3k6IFJlbW92YWxQb2xpY3kuREVTVFJPWSxcbiAgICB9KTtcblxuICAgIGNvbnN0IGZpcmVob3NlTG9nU3RyZWFtID0gbmV3IGxvZ3MuTG9nU3RyZWFtKHRoaXMsICdGaXJlaG9zZUxvZ1N0cmVhbScsIHtcbiAgICAgIGxvZ0dyb3VwOiBmaXJlaG9zZUxvZ0dyb3VwLFxuICAgICAgbG9nU3RyZWFtTmFtZTogJ1MzRGVsaXZlcnknLFxuICAgIH0pO1xuXG4gICAgZmlyZWhvc2VMb2dHcm91cC5ncmFudFdyaXRlKGZpcmVob3NlUm9sZSk7XG5cbiAgICAvLy8vLy8vLyBLSU5FU0lTIERBVEEgRklSRUhPU0UgREVMSVZFUlkgU1RSRUFNIC8vLy8vLy8vXG4gICAgXG4gICAgY29uc3QgZGVsaXZlcnlTdHJlYW0gPSBuZXcgZmlyZWhvc2UuQ2ZuRGVsaXZlcnlTdHJlYW0odGhpcywgJ1Nlc0V2ZW50c0RlbGl2ZXJ5U3RyZWFtJywge1xuICAgICAgZGVsaXZlcnlTdHJlYW1OYW1lOiBgc2VzLWFuYWx5dGljcy1zdHJlYW0tJHtjZGsuU3RhY2sub2YodGhpcykuYWNjb3VudH1gLFxuICAgICAgZGVsaXZlcnlTdHJlYW1UeXBlOiAnRGlyZWN0UHV0JyxcbiAgICAgIGV4dGVuZGVkUzNEZXN0aW5hdGlvbkNvbmZpZ3VyYXRpb246IHtcbiAgICAgICAgYnVja2V0QXJuOiB0aGlzLnJhd0RhdGFCdWNrZXQuYnVja2V0QXJuLFxuICAgICAgICByb2xlQXJuOiBmaXJlaG9zZVJvbGUucm9sZUFybixcbiAgICAgICAgcHJlZml4OiAnZXZlbnRzL3llYXI9IXt0aW1lc3RhbXA6eXl5eX0vbW9udGg9IXt0aW1lc3RhbXA6TU19L2RheT0he3RpbWVzdGFtcDpkZH0vaG91cj0he3RpbWVzdGFtcDpISH0vJyxcbiAgICAgICAgZXJyb3JPdXRwdXRQcmVmaXg6ICdlcnJvcnMvIXtmaXJlaG9zZTplcnJvci1vdXRwdXQtdHlwZX0veWVhcj0he3RpbWVzdGFtcDp5eXl5fS9tb250aD0he3RpbWVzdGFtcDpNTX0vZGF5PSF7dGltZXN0YW1wOmRkfS8nLFxuICAgICAgICBidWZmZXJpbmdIaW50czoge1xuICAgICAgICAgIHNpemVJbk1CczogTWF0aC5tYXgocHJvcHMuZmlyZWhvc2VCdWZmZXJTaXplTUIsIDY0KSwgLy8gTWluaW11bSA2NCBNQiB3aGVuIGRhdGEgZm9ybWF0IGNvbnZlcnNpb24gaXMgZW5hYmxlZFxuICAgICAgICAgIGludGVydmFsSW5TZWNvbmRzOiBwcm9wcy5maXJlaG9zZUJ1ZmZlckludGVydmFsU2Vjb25kcyxcbiAgICAgICAgfSxcbiAgICAgICAgY29tcHJlc3Npb25Gb3JtYXQ6ICdVTkNPTVBSRVNTRUQnLFxuICAgICAgICBkYXRhRm9ybWF0Q29udmVyc2lvbkNvbmZpZ3VyYXRpb246IHtcbiAgICAgICAgICBlbmFibGVkOiB0cnVlLFxuICAgICAgICAgIHNjaGVtYUNvbmZpZ3VyYXRpb246IHtcbiAgICAgICAgICAgIGRhdGFiYXNlTmFtZTogdGhpcy5nbHVlRGF0YWJhc2UucmVmLFxuICAgICAgICAgICAgdGFibGVOYW1lOiByYXdFdmVudHNUYWJsZU5hbWUsXG4gICAgICAgICAgICByZWdpb246IGNkay5TdGFjay5vZih0aGlzKS5yZWdpb24sXG4gICAgICAgICAgICByb2xlQXJuOiBmaXJlaG9zZVJvbGUucm9sZUFybixcbiAgICAgICAgICB9LFxuICAgICAgICAgIGlucHV0Rm9ybWF0Q29uZmlndXJhdGlvbjoge1xuICAgICAgICAgICAgZGVzZXJpYWxpemVyOiB7XG4gICAgICAgICAgICAgIG9wZW5YSnNvblNlckRlOiB7fSxcbiAgICAgICAgICAgIH0sXG4gICAgICAgICAgfSxcbiAgICAgICAgICBvdXRwdXRGb3JtYXRDb25maWd1cmF0aW9uOiB7XG4gICAgICAgICAgICBzZXJpYWxpemVyOiB7XG4gICAgICAgICAgICAgIHBhcnF1ZXRTZXJEZToge1xuICAgICAgICAgICAgICAgIGNvbXByZXNzaW9uOiAnU05BUFBZJyxcbiAgICAgICAgICAgICAgfSxcbiAgICAgICAgICAgIH0sXG4gICAgICAgICAgfSxcbiAgICAgICAgfSxcbiAgICAgICAgY2xvdWRXYXRjaExvZ2dpbmdPcHRpb25zOiB7XG4gICAgICAgICAgZW5hYmxlZDogdHJ1ZSxcbiAgICAgICAgICBsb2dHcm91cE5hbWU6IGZpcmVob3NlTG9nR3JvdXAubG9nR3JvdXBOYW1lLFxuICAgICAgICAgIGxvZ1N0cmVhbU5hbWU6IGZpcmVob3NlTG9nU3RyZWFtLmxvZ1N0cmVhbU5hbWUsXG4gICAgICAgIH0sXG4gICAgICB9LFxuICAgIH0pO1xuXG4gICAgLy8gR3JhbnQgR2x1ZSBwZXJtaXNzaW9ucyB0byBGaXJlaG9zZSByb2xlXG4gICAgY29uc3QgZ2x1ZVBvbGljeSA9IG5ldyBpYW0uUG9saWN5KHRoaXMsICdGaXJlaG9zZUdsdWVQb2xpY3knLCB7XG4gICAgICBzdGF0ZW1lbnRzOiBbXG4gICAgICAgIG5ldyBpYW0uUG9saWN5U3RhdGVtZW50KHtcbiAgICAgICAgICBhY3Rpb25zOiBbXG4gICAgICAgICAgICAnZ2x1ZTpHZXRUYWJsZScsXG4gICAgICAgICAgICAnZ2x1ZTpHZXRUYWJsZVZlcnNpb24nLFxuICAgICAgICAgICAgJ2dsdWU6R2V0VGFibGVWZXJzaW9ucycsXG4gICAgICAgICAgXSxcbiAgICAgICAgICByZXNvdXJjZXM6IFtcbiAgICAgICAgICAgIGBhcm46YXdzOmdsdWU6JHtjZGsuU3RhY2sub2YodGhpcykucmVnaW9ufToke2Nkay5TdGFjay5vZih0aGlzKS5hY2NvdW50fTpjYXRhbG9nYCxcbiAgICAgICAgICAgIGBhcm46YXdzOmdsdWU6JHtjZGsuU3RhY2sub2YodGhpcykucmVnaW9ufToke2Nkay5TdGFjay5vZih0aGlzKS5hY2NvdW50fTpkYXRhYmFzZS8ke3RoaXMuZ2x1ZURhdGFiYXNlLnJlZn1gLFxuICAgICAgICAgICAgYGFybjphd3M6Z2x1ZToke2Nkay5TdGFjay5vZih0aGlzKS5yZWdpb259OiR7Y2RrLlN0YWNrLm9mKHRoaXMpLmFjY291bnR9OnRhYmxlLyR7dGhpcy5nbHVlRGF0YWJhc2UucmVmfS8qYCxcbiAgICAgICAgICBdLFxuICAgICAgICB9KSxcbiAgICAgIF0sXG4gICAgfSk7XG4gICAgXG4gICAgZ2x1ZVBvbGljeS5hdHRhY2hUb1JvbGUoZmlyZWhvc2VSb2xlKTtcbiAgICBcbiAgICAvLyBFbnN1cmUgZGVsaXZlcnkgc3RyZWFtIGlzIGNyZWF0ZWQgYWZ0ZXIgdGhlIHBvbGljeSBpcyBhdHRhY2hlZCBhbmQgdGFibGUgaXMgY3JlYXRlZFxuICAgIGRlbGl2ZXJ5U3RyZWFtLm5vZGUuYWRkRGVwZW5kZW5jeShnbHVlUG9saWN5KTtcbiAgICBkZWxpdmVyeVN0cmVhbS5ub2RlLmFkZERlcGVuZGVuY3kodGFibGVDcmVhdGlvblRyaWdnZXIpO1xuICAgIFxuICAgIC8vIEdyYW50IFNFUyByb2xlIHBlcm1pc3Npb24gdG8gcHV0IHJlY29yZHMgdG8gRmlyZWhvc2VcbiAgICBzZXNGaXJlaG9zZVJvbGUuYWRkVG9Qb2xpY3koXG4gICAgICBuZXcgaWFtLlBvbGljeVN0YXRlbWVudCh7XG4gICAgICAgIGFjdGlvbnM6IFsnZmlyZWhvc2U6UHV0UmVjb3JkJywgJ2ZpcmVob3NlOlB1dFJlY29yZEJhdGNoJywgJ2ZpcmVob3NlOkRlc2NyaWJlRGVsaXZlcnlTdHJlYW0nXSxcbiAgICAgICAgcmVzb3VyY2VzOiBbZGVsaXZlcnlTdHJlYW0uYXR0ckFybl0sXG4gICAgICB9KSxcbiAgICApO1xuICAgIFxuICAgIC8vIFN1cHByZXNzIENESyBOYWcgd2FybmluZ3MgZm9yIEZpcmVob3NlIHJvbGUgR2x1ZSBwZXJtaXNzaW9uc1xuICAgIE5hZ1N1cHByZXNzaW9ucy5hZGRSZXNvdXJjZVN1cHByZXNzaW9ucyhcbiAgICAgIGZpcmVob3NlUm9sZSxcbiAgICAgIFtcbiAgICAgICAge1xuICAgICAgICAgIGlkOiAnQXdzU29sdXRpb25zLUlBTTUnLFxuICAgICAgICAgIHJlYXNvbjogJ0dsdWUgY2F0YWxvZyBvcGVyYXRpb25zIHJlcXVpcmUgd2lsZGNhcmQgcGVybWlzc2lvbnMgZm9yIHRhYmxlIGFjY2VzcyB3aXRoaW4gdGhlIGRhdGFiYXNlLiBUaGlzIGFsbG93cyBGaXJlaG9zZSB0byBhY2Nlc3MgYWxsIHRhYmxlcyBmb3Igc2NoZW1hIGNvbnZlcnNpb24gZHVyaW5nIFBhcnF1ZXQgdHJhbnNmb3JtYXRpb24uJyxcbiAgICAgICAgICBhcHBsaWVzVG86IFtcbiAgICAgICAgICAgIGBSZXNvdXJjZTo6YXJuOmF3czpnbHVlOiR7Y2RrLlN0YWNrLm9mKHRoaXMpLnJlZ2lvbn06JHtjZGsuU3RhY2sub2YodGhpcykuYWNjb3VudH06dGFibGUvPFNlc0V2ZW50RGF0YWJhc2U+LypgXG4gICAgICAgICAgXVxuICAgICAgICB9XG4gICAgICBdLFxuICAgICAgdHJ1ZSAvLyBhcHBseVRvQ2hpbGRyZW5cbiAgICApO1xuXG4gICAgLy8vLy8vLy8gU0VTIEVWRU5UIERFU1RJTkFUSU9OIC8vLy8vLy8vXG4gICAgXG4gICAgY29uc3Qgc2VzRXZlbnREZXN0aW5hdGlvbiA9IG5ldyBzZXMuQ2ZuQ29uZmlndXJhdGlvblNldEV2ZW50RGVzdGluYXRpb24odGhpcywgJ1Nlc0V2ZW50RGVzdGluYXRpb24nLCB7XG4gICAgICBjb25maWd1cmF0aW9uU2V0TmFtZTogY29uZmlnU2V0TmFtZSxcbiAgICAgIGV2ZW50RGVzdGluYXRpb246IHtcbiAgICAgICAgbmFtZTogJ2ZpcmVob3NlLWRlc3RpbmF0aW9uJyxcbiAgICAgICAgZW5hYmxlZDogdHJ1ZSxcbiAgICAgICAgbWF0Y2hpbmdFdmVudFR5cGVzOiBbJ3NlbmQnLCAncmVqZWN0JywgJ2JvdW5jZScsICdjb21wbGFpbnQnLCAnZGVsaXZlcnknLCAnb3BlbicsICdjbGljaycsICdyZW5kZXJpbmdGYWlsdXJlJ10sXG4gICAgICAgIGtpbmVzaXNGaXJlaG9zZURlc3RpbmF0aW9uOiB7XG4gICAgICAgICAgZGVsaXZlcnlTdHJlYW1Bcm46IGRlbGl2ZXJ5U3RyZWFtLmF0dHJBcm4sXG4gICAgICAgICAgaWFtUm9sZUFybjogc2VzRmlyZWhvc2VSb2xlLnJvbGVBcm4sXG4gICAgICAgIH0sXG4gICAgICB9LFxuICAgIH0pO1xuICAgIFxuICAgIC8vIEVuc3VyZSBTRVMgZXZlbnQgZGVzdGluYXRpb24gaXMgY3JlYXRlZCBhZnRlciBhbGwgcG9saWNpZXMgYXJlIGF0dGFjaGVkXG4gICAgc2VzRXZlbnREZXN0aW5hdGlvbi5ub2RlLmFkZERlcGVuZGVuY3koZGVsaXZlcnlTdHJlYW0pO1xuICAgIHNlc0V2ZW50RGVzdGluYXRpb24ubm9kZS5hZGREZXBlbmRlbmN5KHNlc0ZpcmVob3NlUm9sZSk7XG5cbiAgICAvLy8vLy8vLyBTTlMgVE9QSUMgRk9SIE5PVElGSUNBVElPTlMgLy8vLy8vLy9cbiAgICBcbiAgICBsZXQgbm90aWZpY2F0aW9uVG9waWM6IHNucy5Ub3BpYyB8IHVuZGVmaW5lZDtcbiAgICBcbiAgICBpZiAocHJvcHMuZW5hYmxlTm90aWZpY2F0aW9ucykge1xuICAgICAgbm90aWZpY2F0aW9uVG9waWMgPSBuZXcgc25zLlRvcGljKHRoaXMsICdBbmFseXRpY3NOb3RpZmljYXRpb25Ub3BpYycsIHtcbiAgICAgICAgZGlzcGxheU5hbWU6ICdTRVMgQ2FtcGFpZ24gQW5hbHl0aWNzIE5vdGlmaWNhdGlvbnMnLFxuICAgICAgfSk7XG5cbiAgICAgIC8vIEVuZm9yY2UgU1NMXG4gICAgICBub3RpZmljYXRpb25Ub3BpYy5hZGRUb1Jlc291cmNlUG9saWN5KFxuICAgICAgICBuZXcgaWFtLlBvbGljeVN0YXRlbWVudCh7XG4gICAgICAgICAgZWZmZWN0OiBpYW0uRWZmZWN0LkRFTlksXG4gICAgICAgICAgYWN0aW9uczogWydzbnM6UHVibGlzaCddLFxuICAgICAgICAgIHJlc291cmNlczogW25vdGlmaWNhdGlvblRvcGljLnRvcGljQXJuXSxcbiAgICAgICAgICBwcmluY2lwYWxzOiBbbmV3IGlhbS5BbnlQcmluY2lwYWwoKV0sXG4gICAgICAgICAgY29uZGl0aW9uczogeyBCb29sOiB7ICdhd3M6U2VjdXJlVHJhbnNwb3J0JzogJ2ZhbHNlJyB9IH0sXG4gICAgICAgIH0pLFxuICAgICAgKTtcblxuICAgICAgaWYgKHByb3BzLm5vdGlmaWNhdGlvbkVtYWlsKSB7XG4gICAgICAgIG5vdGlmaWNhdGlvblRvcGljLmFkZFN1YnNjcmlwdGlvbihcbiAgICAgICAgICBuZXcgc3Vic2NyaXB0aW9ucy5FbWFpbFN1YnNjcmlwdGlvbihwcm9wcy5ub3RpZmljYXRpb25FbWFpbCksXG4gICAgICAgICk7XG4gICAgICB9XG4gICAgfVxuXG4gICAgLy8vLy8vLy8gTEFNQkRBIEZPUiBNQVRFUklBTElaRUQgVklFVyBSRUZSRVNIIC8vLy8vLy8vXG4gICAgXG4gICAgY29uc3QgcmVmcmVzaExhbWJkYSA9IG5ldyBsYW1iZGEuRnVuY3Rpb24odGhpcywgJ01hdGVyaWFsaXplZFZpZXdSZWZyZXNoJywge1xuICAgICAgcnVudGltZTogbGFtYmRhLlJ1bnRpbWUuUFlUSE9OXzNfMTIsXG4gICAgICBoYW5kbGVyOiAnbWF0ZXJpYWxpemVkVmlld1JlZnJlc2guaGFuZGxlcicsXG4gICAgICBjb2RlOiBsYW1iZGEuQ29kZS5mcm9tQXNzZXQocGF0aC5qb2luKF9fZGlybmFtZSwgJy4uL2xhbWJkYScpKSxcbiAgICAgIHRpbWVvdXQ6IER1cmF0aW9uLm1pbnV0ZXMocHJvcHMubGFtYmRhVGltZW91dE1pbnV0ZXMpLFxuICAgICAgbWVtb3J5U2l6ZTogcHJvcHMubGFtYmRhTWVtb3J5TUIsXG4gICAgICBsb2dSZXRlbnRpb246IGxvZ3MuUmV0ZW50aW9uRGF5cy5PTkVfV0VFSyxcbiAgICAgIGVudmlyb25tZW50OiB7XG4gICAgICAgIERBVEFCQVNFX05BTUU6IHRoaXMuZ2x1ZURhdGFiYXNlLnJlZixcbiAgICAgICAgUkFXX0VWRU5UU19UQUJMRTogcmF3RXZlbnRzVGFibGVOYW1lLFxuICAgICAgICBDQU1QQUlHTl9NRVRSSUNTX1RBQkxFOiBjYW1wYWlnbk1ldHJpY3NUYWJsZS5yZWYsXG4gICAgICAgIFBST0NFU1NFRF9CVUNLRVQ6IHRoaXMucHJvY2Vzc2VkRGF0YUJ1Y2tldC5idWNrZXROYW1lLFxuICAgICAgICBBVEhFTkFfT1VUUFVUX0xPQ0FUSU9OOiBgczM6Ly8ke3RoaXMuYXRoZW5hUmVzdWx0c0J1Y2tldC5idWNrZXROYW1lfS9yZWZyZXNoLXJlc3VsdHMvYCxcbiAgICAgICAgV09SS0dST1VQX05BTUU6IHRoaXMud29ya0dyb3VwLnJlZixcbiAgICAgICAgTk9USUZJQ0FUSU9OX1RPUElDX0FSTjogbm90aWZpY2F0aW9uVG9waWM/LnRvcGljQXJuIHx8ICcnLFxuICAgICAgfSxcbiAgICB9KTtcblxuICAgIC8vIEdyYW50IHBlcm1pc3Npb25zIHRvIHJlZnJlc2ggTGFtYmRhXG4gICAgdGhpcy5wcm9jZXNzZWREYXRhQnVja2V0LmdyYW50UmVhZFdyaXRlKHJlZnJlc2hMYW1iZGEpO1xuICAgIHRoaXMucmF3RGF0YUJ1Y2tldC5ncmFudFJlYWQocmVmcmVzaExhbWJkYSk7XG4gICAgdGhpcy5hdGhlbmFSZXN1bHRzQnVja2V0LmdyYW50UmVhZFdyaXRlKHJlZnJlc2hMYW1iZGEpO1xuXG4gICAgcmVmcmVzaExhbWJkYS5hZGRUb1JvbGVQb2xpY3koXG4gICAgICBuZXcgaWFtLlBvbGljeVN0YXRlbWVudCh7XG4gICAgICAgIGFjdGlvbnM6IFtcbiAgICAgICAgICAnYXRoZW5hOlN0YXJ0UXVlcnlFeGVjdXRpb24nLFxuICAgICAgICAgICdhdGhlbmE6R2V0UXVlcnlFeGVjdXRpb24nLFxuICAgICAgICAgICdhdGhlbmE6R2V0UXVlcnlSZXN1bHRzJyxcbiAgICAgICAgICAnYXRoZW5hOlN0b3BRdWVyeUV4ZWN1dGlvbicsXG4gICAgICAgIF0sXG4gICAgICAgIHJlc291cmNlczogW1xuICAgICAgICAgIGBhcm46YXdzOmF0aGVuYToke2Nkay5TdGFjay5vZih0aGlzKS5yZWdpb259OiR7Y2RrLlN0YWNrLm9mKHRoaXMpLmFjY291bnR9Ondvcmtncm91cC8ke3RoaXMud29ya0dyb3VwLnJlZn1gLFxuICAgICAgICBdLFxuICAgICAgfSksXG4gICAgKTtcblxuICAgIHJlZnJlc2hMYW1iZGEuYWRkVG9Sb2xlUG9saWN5KFxuICAgICAgbmV3IGlhbS5Qb2xpY3lTdGF0ZW1lbnQoe1xuICAgICAgICBhY3Rpb25zOiBbXG4gICAgICAgICAgJ2dsdWU6R2V0RGF0YWJhc2UnLFxuICAgICAgICAgICdnbHVlOkdldFRhYmxlJyxcbiAgICAgICAgICAnZ2x1ZTpHZXRQYXJ0aXRpb25zJyxcbiAgICAgICAgICAnZ2x1ZTpDcmVhdGVQYXJ0aXRpb24nLFxuICAgICAgICAgICdnbHVlOlVwZGF0ZVBhcnRpdGlvbicsXG4gICAgICAgICAgJ2dsdWU6RGVsZXRlUGFydGl0aW9uJyxcbiAgICAgICAgICAnZ2x1ZTpCYXRjaENyZWF0ZVBhcnRpdGlvbicsXG4gICAgICAgICAgJ2dsdWU6QmF0Y2hEZWxldGVQYXJ0aXRpb24nLFxuICAgICAgICBdLFxuICAgICAgICByZXNvdXJjZXM6IFtcbiAgICAgICAgICBgYXJuOmF3czpnbHVlOiR7Y2RrLlN0YWNrLm9mKHRoaXMpLnJlZ2lvbn06JHtjZGsuU3RhY2sub2YodGhpcykuYWNjb3VudH06Y2F0YWxvZ2AsXG4gICAgICAgICAgYGFybjphd3M6Z2x1ZToke2Nkay5TdGFjay5vZih0aGlzKS5yZWdpb259OiR7Y2RrLlN0YWNrLm9mKHRoaXMpLmFjY291bnR9OmRhdGFiYXNlLyR7dGhpcy5nbHVlRGF0YWJhc2UucmVmfWAsXG4gICAgICAgICAgYGFybjphd3M6Z2x1ZToke2Nkay5TdGFjay5vZih0aGlzKS5yZWdpb259OiR7Y2RrLlN0YWNrLm9mKHRoaXMpLmFjY291bnR9OnRhYmxlLyR7dGhpcy5nbHVlRGF0YWJhc2UucmVmfS8qYCxcbiAgICAgICAgXSxcbiAgICAgIH0pLFxuICAgICk7XG5cbiAgICBpZiAobm90aWZpY2F0aW9uVG9waWMpIHtcbiAgICAgIG5vdGlmaWNhdGlvblRvcGljLmdyYW50UHVibGlzaChyZWZyZXNoTGFtYmRhKTtcbiAgICB9XG5cbiAgICAvLy8vLy8vLyBMQU1CREEgRk9SIEFUSEVOQSBQQVJUSVRJT04gTUFOQUdFTUVOVCAvLy8vLy8vL1xuICAgIFxuICAgIC8vIExhbWJkYSB0byBhdXRvbWF0aWNhbGx5IGFkZCBwYXJ0aXRpb25zIHdoZW4gbmV3IGRhdGEgYXJyaXZlcyBpbiBTM1xuICAgIGNvbnN0IGF0aGVuYVBhcnRpdGlvbkxhbWJkYSA9IG5ldyBsYW1iZGEuRnVuY3Rpb24odGhpcywgJ0F0aGVuYVBhcnRpdGlvbkxhbWJkYScsIHtcbiAgICAgIHJ1bnRpbWU6IGxhbWJkYS5SdW50aW1lLlBZVEhPTl8zXzEyLFxuICAgICAgaGFuZGxlcjogJ3BhcnRpdGlvbk1hbmFnZXIuaGFuZGxlcicsXG4gICAgICBjb2RlOiBsYW1iZGEuQ29kZS5mcm9tQXNzZXQocGF0aC5qb2luKF9fZGlybmFtZSwgJy4uL2xhbWJkYScpKSxcbiAgICAgIHRpbWVvdXQ6IER1cmF0aW9uLm1pbnV0ZXMoNSksXG4gICAgICBtZW1vcnlTaXplOiAyNTYsXG4gICAgICBlbnZpcm9ubWVudDoge1xuICAgICAgICBEQVRBQkFTRV9OQU1FOiB0aGlzLmdsdWVEYXRhYmFzZS5yZWYsXG4gICAgICAgIFRBQkxFX05BTUU6IHJhd0V2ZW50c1RhYmxlTmFtZSxcbiAgICAgICAgT1VUUFVUX0xPQ0FUSU9OOiBgczM6Ly8ke3RoaXMuYXRoZW5hUmVzdWx0c0J1Y2tldC5idWNrZXROYW1lfS9wYXJ0aXRpb24tbWdtdC9gLFxuICAgICAgfSxcbiAgICB9KTtcblxuICAgIC8vIEdyYW50IHBlcm1pc3Npb25zIHRvIHBhcnRpdGlvbiBMYW1iZGFcbiAgICB0aGlzLnJhd0RhdGFCdWNrZXQuZ3JhbnRSZWFkKGF0aGVuYVBhcnRpdGlvbkxhbWJkYSk7XG4gICAgdGhpcy5hdGhlbmFSZXN1bHRzQnVja2V0LmdyYW50UmVhZFdyaXRlKGF0aGVuYVBhcnRpdGlvbkxhbWJkYSk7XG5cbiAgICBhdGhlbmFQYXJ0aXRpb25MYW1iZGEuYWRkVG9Sb2xlUG9saWN5KFxuICAgICAgbmV3IGlhbS5Qb2xpY3lTdGF0ZW1lbnQoe1xuICAgICAgICBhY3Rpb25zOiBbJ2F0aGVuYTpTdGFydFF1ZXJ5RXhlY3V0aW9uJ10sXG4gICAgICAgIHJlc291cmNlczogW2Bhcm46YXdzOmF0aGVuYToke2Nkay5TdGFjay5vZih0aGlzKS5yZWdpb259OiR7Y2RrLlN0YWNrLm9mKHRoaXMpLmFjY291bnR9Ondvcmtncm91cC8qYF0sXG4gICAgICB9KSxcbiAgICApO1xuXG4gICAgYXRoZW5hUGFydGl0aW9uTGFtYmRhLmFkZFRvUm9sZVBvbGljeShcbiAgICAgIG5ldyBpYW0uUG9saWN5U3RhdGVtZW50KHtcbiAgICAgICAgYWN0aW9uczogW1xuICAgICAgICAgICdnbHVlOkdldERhdGFiYXNlJyxcbiAgICAgICAgICAnZ2x1ZTpHZXRUYWJsZScsXG4gICAgICAgICAgJ2dsdWU6Q3JlYXRlUGFydGl0aW9uJyxcbiAgICAgICAgICAnZ2x1ZTpCYXRjaENyZWF0ZVBhcnRpdGlvbicsXG4gICAgICAgIF0sXG4gICAgICAgIHJlc291cmNlczogW1xuICAgICAgICAgIGBhcm46YXdzOmdsdWU6JHtjZGsuU3RhY2sub2YodGhpcykucmVnaW9ufToke2Nkay5TdGFjay5vZih0aGlzKS5hY2NvdW50fTpjYXRhbG9nYCxcbiAgICAgICAgICBgYXJuOmF3czpnbHVlOiR7Y2RrLlN0YWNrLm9mKHRoaXMpLnJlZ2lvbn06JHtjZGsuU3RhY2sub2YodGhpcykuYWNjb3VudH06ZGF0YWJhc2UvJHt0aGlzLmdsdWVEYXRhYmFzZS5yZWZ9YCxcbiAgICAgICAgICBgYXJuOmF3czpnbHVlOiR7Y2RrLlN0YWNrLm9mKHRoaXMpLnJlZ2lvbn06JHtjZGsuU3RhY2sub2YodGhpcykuYWNjb3VudH06dGFibGUvJHt0aGlzLmdsdWVEYXRhYmFzZS5yZWZ9LypgLFxuICAgICAgICBdLFxuICAgICAgfSksXG4gICAgKTtcblxuICAgIC8vIEdyYW50IFMzIHBlcm1pc3Npb24gdG8gaW52b2tlIExhbWJkYVxuICAgIGF0aGVuYVBhcnRpdGlvbkxhbWJkYS5hZGRQZXJtaXNzaW9uKCdTM0ludm9rZUxhbWJkYScsIHtcbiAgICAgIHByaW5jaXBhbDogbmV3IGlhbS5TZXJ2aWNlUHJpbmNpcGFsKCdzMy5hbWF6b25hd3MuY29tJyksXG4gICAgICBhY3Rpb246ICdsYW1iZGE6SW52b2tlRnVuY3Rpb24nLFxuICAgICAgc291cmNlQWNjb3VudDogY2RrLlN0YWNrLm9mKHRoaXMpLmFjY291bnQsXG4gICAgICBzb3VyY2VBcm46IHRoaXMucmF3RGF0YUJ1Y2tldC5idWNrZXRBcm4sXG4gICAgfSk7XG5cbiAgICAvLyBBZGQgUzMgbm90aWZpY2F0aW9uIHRvIHRyaWdnZXIgTGFtYmRhIHdoZW4gbmV3IG9iamVjdHMgYXJlIGNyZWF0ZWRcbiAgICB0aGlzLnJhd0RhdGFCdWNrZXQuYWRkRXZlbnROb3RpZmljYXRpb24oXG4gICAgICBzMy5FdmVudFR5cGUuT0JKRUNUX0NSRUFURUQsXG4gICAgICBuZXcgczNOb3RpZmljYXRpb25zLkxhbWJkYURlc3RpbmF0aW9uKGF0aGVuYVBhcnRpdGlvbkxhbWJkYSksXG4gICAgICB7IHByZWZpeDogJ2V2ZW50cy8nIH1cbiAgICApO1xuXG4gICAgLy8vLy8vLy8gRFlOQU1PREIgVEFCTEUgRk9SIENBTVBBSUdOIE1FVEFEQVRBIC8vLy8vLy8vXG4gICAgXG4gICAgY29uc3QgY2FtcGFpZ25NZXRhZGF0YVRhYmxlID0gbmV3IGR5bmFtb2RiLlRhYmxlKHRoaXMsICdDYW1wYWlnbk1ldGFkYXRhVGFibGUnLCB7XG4gICAgICAvLyBMZXQgQ0RLIGdlbmVyYXRlIHVuaXF1ZSB0YWJsZSBuYW1lIHRvIGF2b2lkIGNvbmZsaWN0cyBvbiByZWRlcGxveVxuICAgICAgcGFydGl0aW9uS2V5OiB7IG5hbWU6ICdjYW1wYWlnbl9pZCcsIHR5cGU6IGR5bmFtb2RiLkF0dHJpYnV0ZVR5cGUuU1RSSU5HIH0sXG4gICAgICBiaWxsaW5nTW9kZTogZHluYW1vZGIuQmlsbGluZ01vZGUuUEFZX1BFUl9SRVFVRVNULFxuICAgICAgZW5jcnlwdGlvbjogZHluYW1vZGIuVGFibGVFbmNyeXB0aW9uLkFXU19NQU5BR0VELFxuICAgICAgcG9pbnRJblRpbWVSZWNvdmVyeTogdHJ1ZSxcbiAgICAgIHJlbW92YWxQb2xpY3k6IFJlbW92YWxQb2xpY3kuREVTVFJPWSwgLy8gQ2hhbmdlZCB0byBERVNUUk9ZIGZvciBlYXNpZXIgcmVkZXBsb3ltZW50XG4gICAgICB0aW1lVG9MaXZlQXR0cmlidXRlOiAndHRsJyxcbiAgICB9KTtcblxuICAgIC8vIEdyYW50IExhbWJkYSByZWFkL3dyaXRlIGFjY2VzcyB0byBEeW5hbW9EQiAoZm9yIHBvdGVudGlhbCBmdXR1cmUgYXV0b21hdGlvbilcbiAgICBjYW1wYWlnbk1ldGFkYXRhVGFibGUuZ3JhbnRSZWFkV3JpdGVEYXRhKHJlZnJlc2hMYW1iZGEpO1xuXG4gICAgLy8vLy8vLy8gRVZFTlRCUklER0UgUlVMRSBGT1IgU0NIRURVTEVEIFJFRlJFU0ggLy8vLy8vLy9cbiAgICBcbiAgICBjb25zdCByZWZyZXNoUnVsZSA9IG5ldyBldmVudHMuUnVsZSh0aGlzLCAnRGFpbHlSZWZyZXNoUnVsZScsIHtcbiAgICAgIHNjaGVkdWxlOiBldmVudHMuU2NoZWR1bGUuZXhwcmVzc2lvbihwcm9wcy5yZWZyZXNoU2NoZWR1bGVDcm9uKSxcbiAgICAgIGRlc2NyaXB0aW9uOiAnVHJpZ2dlciBkYWlseSByZWZyZXNoIG9mIFNFUyBjYW1wYWlnbiBtYXRlcmlhbGl6ZWQgdmlld3MnLFxuICAgIH0pO1xuXG4gICAgcmVmcmVzaFJ1bGUuYWRkVGFyZ2V0KG5ldyB0YXJnZXRzLkxhbWJkYUZ1bmN0aW9uKHJlZnJlc2hMYW1iZGEpKTtcblxuICAgIC8vLy8vLy8vIE9VVFBVVFMgLy8vLy8vLy9cbiAgICBcbiAgICBuZXcgQ2ZuT3V0cHV0KHRoaXMsICdTZXNDb25maWd1cmF0aW9uU2V0TmFtZScsIHtcbiAgICAgIHZhbHVlOiBjb25maWdTZXROYW1lLFxuICAgICAgZGVzY3JpcHRpb246ICdTRVMgQ29uZmlndXJhdGlvbiBTZXQgbmFtZSAtIHVzZSB0aGlzIHdoZW4gc2VuZGluZyBlbWFpbHMnLFxuICAgIH0pO1xuXG4gICAgbmV3IENmbk91dHB1dCh0aGlzLCAnUmF3RGF0YUJ1Y2tldE5hbWUnLCB7XG4gICAgICB2YWx1ZTogdGhpcy5yYXdEYXRhQnVja2V0LmJ1Y2tldE5hbWUsXG4gICAgICBkZXNjcmlwdGlvbjogJ1MzIGJ1Y2tldCBmb3IgcmF3IFNFUyBldmVudHMnLFxuICAgIH0pO1xuXG4gICAgbmV3IENmbk91dHB1dCh0aGlzLCAnUHJvY2Vzc2VkRGF0YUJ1Y2tldE5hbWUnLCB7XG4gICAgICB2YWx1ZTogdGhpcy5wcm9jZXNzZWREYXRhQnVja2V0LmJ1Y2tldE5hbWUsXG4gICAgICBkZXNjcmlwdGlvbjogJ1MzIGJ1Y2tldCBmb3IgcHJvY2Vzc2VkIGRhdGEgYW5kIG1hdGVyaWFsaXplZCB2aWV3cycsXG4gICAgfSk7XG5cbiAgICBuZXcgQ2ZuT3V0cHV0KHRoaXMsICdBdGhlbmFSZXN1bHRzQnVja2V0TmFtZScsIHtcbiAgICAgIHZhbHVlOiB0aGlzLmF0aGVuYVJlc3VsdHNCdWNrZXQuYnVja2V0TmFtZSxcbiAgICAgIGRlc2NyaXB0aW9uOiAnUzMgYnVja2V0IGZvciBBdGhlbmEgcXVlcnkgcmVzdWx0cycsXG4gICAgfSk7XG5cbiAgICBuZXcgQ2ZuT3V0cHV0KHRoaXMsICdHbHVlRGF0YWJhc2VOYW1lJywge1xuICAgICAgdmFsdWU6IHRoaXMuZ2x1ZURhdGFiYXNlLnJlZixcbiAgICAgIGRlc2NyaXB0aW9uOiAnR2x1ZSBkYXRhYmFzZSBuYW1lIGZvciBTRVMgYW5hbHl0aWNzJyxcbiAgICB9KTtcblxuICAgIG5ldyBDZm5PdXRwdXQodGhpcywgJ0F0aGVuYVdvcmtHcm91cE5hbWUnLCB7XG4gICAgICB2YWx1ZTogdGhpcy53b3JrR3JvdXAucmVmLFxuICAgICAgZGVzY3JpcHRpb246ICdBdGhlbmEgd29ya2dyb3VwIGZvciBjYW1wYWlnbiBhbmFseXRpY3MgcXVlcmllcycsXG4gICAgfSk7XG5cbiAgICBuZXcgQ2ZuT3V0cHV0KHRoaXMsICdGaXJlaG9zZURlbGl2ZXJ5U3RyZWFtTmFtZScsIHtcbiAgICAgIHZhbHVlOiBkZWxpdmVyeVN0cmVhbS5yZWYsXG4gICAgICBkZXNjcmlwdGlvbjogJ0tpbmVzaXMgRmlyZWhvc2UgZGVsaXZlcnkgc3RyZWFtIG5hbWUnLFxuICAgIH0pO1xuXG4gICAgbmV3IENmbk91dHB1dCh0aGlzLCAnRmlyZWhvc2VEZWxpdmVyeVN0cmVhbUFybicsIHtcbiAgICAgIHZhbHVlOiBkZWxpdmVyeVN0cmVhbS5hdHRyQXJuLFxuICAgICAgZGVzY3JpcHRpb246ICdLaW5lc2lzIEZpcmVob3NlIGRlbGl2ZXJ5IHN0cmVhbSBBUk4nLFxuICAgIH0pO1xuXG4gICAgbmV3IENmbk91dHB1dCh0aGlzLCAnUmVmcmVzaExhbWJkYU5hbWUnLCB7XG4gICAgICB2YWx1ZTogcmVmcmVzaExhbWJkYS5mdW5jdGlvbk5hbWUsXG4gICAgICBkZXNjcmlwdGlvbjogJ0xhbWJkYSBmdW5jdGlvbiBmb3IgbWF0ZXJpYWxpemVkIHZpZXcgcmVmcmVzaCcsXG4gICAgfSk7XG5cbiAgICBuZXcgQ2ZuT3V0cHV0KHRoaXMsICdDYW1wYWlnbk1ldGFkYXRhVGFibGVOYW1lJywge1xuICAgICAgdmFsdWU6IGNhbXBhaWduTWV0YWRhdGFUYWJsZS50YWJsZU5hbWUsXG4gICAgICBkZXNjcmlwdGlvbjogJ0R5bmFtb0RCIHRhYmxlIGZvciBjYW1wYWlnbiBtZXRhZGF0YSBzdG9yYWdlJyxcbiAgICB9KTtcblxuICAgIGlmIChub3RpZmljYXRpb25Ub3BpYykge1xuICAgICAgbmV3IENmbk91dHB1dCh0aGlzLCAnTm90aWZpY2F0aW9uVG9waWNBcm4nLCB7XG4gICAgICAgIHZhbHVlOiBub3RpZmljYXRpb25Ub3BpYy50b3BpY0FybixcbiAgICAgICAgZGVzY3JpcHRpb246ICdTTlMgdG9waWMgZm9yIGFuYWx5dGljcyBub3RpZmljYXRpb25zJyxcbiAgICAgIH0pO1xuICAgIH1cblxuICAgIG5ldyBDZm5PdXRwdXQodGhpcywgJ1F1ZXJ5RXhhbXBsZUNhbXBhaWduU3VtbWFyeScsIHtcbiAgICAgIHZhbHVlOiBgU0VMRUNUICogRlJPTSAke3RoaXMuZ2x1ZURhdGFiYXNlLnJlZn0uY2FtcGFpZ25fbWV0cmljc19kYWlseSBXSEVSRSBkYXRlID49IGN1cnJlbnRfZGF0ZSAtIGludGVydmFsICczMCcgZGF5IE9SREVSIEJZIGRhdGUgREVTQ2AsXG4gICAgICBkZXNjcmlwdGlvbjogJ0V4YW1wbGUgQXRoZW5hIHF1ZXJ5IGZvciBjYW1wYWlnbiBzdW1tYXJ5JyxcbiAgICB9KTtcbiAgfVxufVxuIl19