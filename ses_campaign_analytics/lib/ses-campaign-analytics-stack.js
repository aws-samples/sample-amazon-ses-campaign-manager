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
const cloudwatch = require("aws-cdk-lib/aws-cloudwatch");
const cloudwatchActions = require("aws-cdk-lib/aws-cloudwatch-actions");
const cdk_nag_1 = require("cdk-nag");
const crypto = require("crypto");
const fs = require("fs");
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
            // No Glacier transition: Athena silently skips Glacier objects, so older events would
            // vanish from queries and refreshes, and per-object fees outweigh savings for small files
            lifecycleRules: [
                {
                    id: 'DeleteOldRawData',
                    enabled: true,
                    expiration: aws_cdk_lib_1.Duration.days(props.dataRetentionDays),
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
        // Views from sql/ (comment lines stripped - Athena DDL must start with the statement)
        const viewFiles = ['campaign_summary_view.sql', 'campaign_realtime_view.sql'];
        const viewSql = viewFiles.map((file) => fs.readFileSync(path.join(__dirname, '../sql', file), 'utf-8')
            .split('\n')
            .filter((line) => !line.trim().startsWith('--'))
            .join('\n')
            .trim());
        const viewQueries = viewSql.map((queryString, index) => new athena.CfnNamedQuery(this, `CreateView${index}`, {
            database: this.glueDatabase.ref,
            description: `Create view from sql/${viewFiles[index]}`,
            name: `create_${viewFiles[index].replace('.sql', '')}`,
            queryString,
        }));
        // Lambda to execute the named queries and create the table and views
        const createTableLambda = new lambda.Function(this, 'CreateTableLambda', {
            runtime: lambda.Runtime.PYTHON_3_12,
            handler: 'createTable.handler',
            code: lambda.Code.fromAsset(path.join(__dirname, '../lambda')),
            timeout: aws_cdk_lib_1.Duration.minutes(10), // Increased from 5 to 10 minutes for Athena query completion
            environment: {
                NAMED_QUERY_ID: createTableQuery.attrNamedQueryId,
                VIEW_QUERY_IDS: cdk.Fn.join(',', viewQueries.map((query) => query.attrNamedQueryId)),
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
                'glue:UpdateTable', // CREATE OR REPLACE VIEW
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
            properties: {
                // Re-run (and re-create the views) whenever the view SQL changes
                ViewsHash: crypto.createHash('sha256').update(viewSql.join('\n')).digest('hex'),
            },
        });
        tableCreationTrigger.node.addDependency(this.glueDatabase);
        tableCreationTrigger.node.addDependency(createTableQuery);
        viewQueries.forEach((query) => tableCreationTrigger.node.addDependency(query));
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
        tableCreationTrigger.node.addDependency(campaignMetricsTable);
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
                REFRESH_LOOKBACK_DAYS: String(props.refreshLookbackDays),
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
            },
        });
        // Grant permissions to partition Lambda (partitions are created through the Glue API)
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
        // Refresh Lambda keeps a lock item in this table so overlapping refreshes don't double-count
        campaignMetadataTable.grantReadWriteData(refreshLambda);
        refreshLambda.addEnvironment('METADATA_TABLE_NAME', campaignMetadataTable.tableName);
        //////// EVENTBRIDGE RULE FOR SCHEDULED REFRESH ////////
        const refreshRule = new events.Rule(this, 'DailyRefreshRule', {
            schedule: events.Schedule.expression(props.refreshScheduleCron),
            description: 'Trigger daily refresh of SES campaign materialized views',
        });
        refreshRule.addTarget(new targets.LambdaFunction(refreshLambda));
        //////// ALARMS ////////
        const firehoseMetric = (metricName, statistic) => new cloudwatch.Metric({
            namespace: 'AWS/Firehose',
            metricName,
            dimensionsMap: { DeliveryStreamName: deliveryStream.ref },
            statistic,
            period: aws_cdk_lib_1.Duration.minutes(5),
        });
        const alarms = [
            new cloudwatch.Alarm(this, 'RefreshErrorsAlarm', {
                alarmDescription: 'Materialized view refresh failed - campaign metrics may be stale',
                metric: refreshLambda.metricErrors({ period: aws_cdk_lib_1.Duration.minutes(5) }),
                threshold: 0,
                comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
                evaluationPeriods: 1,
                treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
            }),
            new cloudwatch.Alarm(this, 'PartitionErrorsAlarm', {
                alarmDescription: 'Raw event partitions could not be registered - events will be missing from analytics',
                metric: athenaPartitionLambda.metricErrors({ period: aws_cdk_lib_1.Duration.minutes(5) }),
                threshold: 0,
                comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
                evaluationPeriods: 1,
                treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
            }),
            new cloudwatch.Alarm(this, 'FirehoseConversionFailuresAlarm', {
                alarmDescription: 'SES events failed Parquet conversion and were written to the errors/ prefix',
                metric: firehoseMetric('FailedConversion.Records', 'Sum'),
                threshold: 0,
                comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
                evaluationPeriods: 1,
                treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
            }),
            new cloudwatch.Alarm(this, 'FirehoseDeliveryFailuresAlarm', {
                alarmDescription: 'Firehose failed to deliver SES events to S3',
                metric: firehoseMetric('DeliveryToS3.Success', 'Minimum'),
                threshold: 1,
                comparisonOperator: cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
                evaluationPeriods: 1,
                treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
            }),
        ];
        if (notificationTopic) {
            // The topic policy replaces SNS's default policy, so CloudWatch needs an explicit allow
            notificationTopic.addToResourcePolicy(new iam.PolicyStatement({
                actions: ['sns:Publish'],
                resources: [notificationTopic.topicArn],
                principals: [new iam.ServicePrincipal('cloudwatch.amazonaws.com')],
                conditions: {
                    ArnLike: { 'aws:SourceArn': `arn:aws:cloudwatch:${cdk.Stack.of(this).region}:${cdk.Stack.of(this).account}:alarm:*` },
                },
            }));
            for (const alarm of alarms) {
                alarm.addAlarmAction(new cloudwatchActions.SnsAction(notificationTopic));
            }
        }
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
//# sourceMappingURL=data:application/json;base64,eyJ2ZXJzaW9uIjozLCJmaWxlIjoic2VzLWNhbXBhaWduLWFuYWx5dGljcy1zdGFjay5qcyIsInNvdXJjZVJvb3QiOiIiLCJzb3VyY2VzIjpbInNlcy1jYW1wYWlnbi1hbmFseXRpY3Mtc3RhY2sudHMiXSwibmFtZXMiOltdLCJtYXBwaW5ncyI6Ijs7O0FBQUEsbUNBQW1DO0FBRW5DLDZDQUF1RTtBQUN2RSx5Q0FBeUM7QUFDekMsb0VBQW9FO0FBQ3BFLDREQUE0RDtBQUM1RCwyQ0FBMkM7QUFDM0MsNkNBQTZDO0FBQzdDLGlEQUFpRDtBQUNqRCxpREFBaUQ7QUFDakQsaURBQWlEO0FBQ2pELDBEQUEwRDtBQUMxRCw2Q0FBNkM7QUFDN0MsMkNBQTJDO0FBQzNDLG1FQUFtRTtBQUNuRSwyQ0FBMkM7QUFDM0MscURBQXFEO0FBQ3JELHlEQUF5RDtBQUN6RCx3RUFBd0U7QUFDeEUscUNBQTBDO0FBQzFDLGlDQUFpQztBQUNqQyx5QkFBeUI7QUFDekIsNkJBQTZCO0FBa0I3QixNQUFhLHlCQUEwQixTQUFRLEdBQUcsQ0FBQyxLQUFLO0lBUXRELFlBQVksS0FBZ0IsRUFBRSxFQUFVLEVBQUUsS0FBcUM7UUFDN0UsS0FBSyxDQUFDLEtBQUssRUFBRSxFQUFFLEVBQUU7WUFDZixHQUFHLEtBQUs7WUFDUixXQUFXLEVBQUUsK0dBQStHO1NBQzdILENBQUMsQ0FBQztRQUVILGlEQUFpRDtRQUNqRCxrQkFBSSxDQUFDLEVBQUUsQ0FBQyxJQUFJLENBQUMsQ0FBQyxHQUFHLENBQUMsU0FBUyxFQUFFLHdCQUF3QixDQUFDLENBQUM7UUFDdkQsa0JBQUksQ0FBQyxFQUFFLENBQUMsSUFBSSxDQUFDLENBQUMsR0FBRyxDQUFDLFdBQVcsRUFBRSxLQUFLLENBQUMsQ0FBQztRQUN0QyxrQkFBSSxDQUFDLEVBQUUsQ0FBQyxJQUFJLENBQUMsQ0FBQyxHQUFHLENBQUMsYUFBYSxFQUFFLFlBQVksQ0FBQyxDQUFDO1FBRS9DLHVDQUF1QztRQUV2Qyx5RUFBeUU7UUFDekUsSUFBSSxhQUFxQixDQUFDO1FBRTFCLElBQUksS0FB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dCAqIGFzIGNkayBmcm9tICdhd3MtY2RrLWxpYic7XG5pbXBvcnQgeyBDb25zdHJ1Y3QgfSBmcm9tICdjb25zdHJ1Y3RzJztcbmltcG9ydCB7IER1cmF0aW9uLCBSZW1vdmFsUG9saWN5LCBDZm5PdXRwdXQsIFRhZ3MgfSBmcm9tICdhd3MtY2RrLWxpYic7XG5pbXBvcnQgKiBhcyBzMyBmcm9tICdhd3MtY2RrLWxpYi9hd3MtczMnO1xuaW1wb3J0ICogYXMgczNOb3RpZmljYXRpb25zIGZyb20gJ2F3cy1jZGstbGliL2F3cy1zMy1ub3RpZmljYXRpb25zJztcbmltcG9ydCAqIGFzIGZpcmVob3NlIGZyb20gJ2F3cy1jZGstbGliL2F3cy1raW5lc2lzZmlyZWhvc2UnO1xuaW1wb3J0ICogYXMgaWFtIGZyb20gJ2F3cy1jZGstbGliL2F3cy1pYW0nO1xuaW1wb3J0ICogYXMgZ2x1ZSBmcm9tICdhd3MtY2RrLWxpYi9hd3MtZ2x1ZSc7XG5pbXBvcnQgKiBhcyBhdGhlbmEgZnJvbSAnYXdzLWNkay1saWIvYXdzLWF0aGVuYSc7XG5pbXBvcnQgKiBhcyBsYW1iZGEgZnJvbSAnYXdzLWNkay1saWIvYXdzLWxhbWJkYSc7XG5pbXBvcnQgKiBhcyBldmVudHMgZnJvbSAnYXdzLWNkay1saWIvYXdzLWV2ZW50cyc7XG5pbXBvcnQgKiBhcyB0YXJnZXRzIGZyb20gJ2F3cy1jZGstbGliL2F3cy1ldmVudHMtdGFyZ2V0cyc7XG5pbXBvcnQgKiBhcyBsb2dzIGZyb20gJ2F3cy1jZGstbGliL2F3cy1sb2dzJztcbmltcG9ydCAqIGFzIHNucyBmcm9tICdhd3MtY2RrLWxpYi9hd3Mtc25zJztcbmltcG9ydCAqIGFzIHN1YnNjcmlwdGlvbnMgZnJvbSAnYXdzLWNkay1saWIvYXdzLXNucy1zdWJzY3JpcHRpb25zJztcbmltcG9ydCAqIGFzIHNlcyBmcm9tICdhd3MtY2RrLWxpYi9hd3Mtc2VzJztcbmltcG9ydCAqIGFzIGR5bmFtb2RiIGZyb20gJ2F3cy1jZGstbGliL2F3cy1keW5hbW9kYic7XG5pbXBvcnQgKiBhcyBjbG91ZHdhdGNoIGZyb20gJ2F3cy1jZGstbGliL2F3cy1jbG91ZHdhdGNoJztcbmltcG9ydCAqIGFzIGNsb3Vkd2F0Y2hBY3Rpb25zIGZyb20gJ2F3cy1jZGstbGliL2F3cy1jbG91ZHdhdGNoLWFjdGlvbnMnO1xuaW1wb3J0IHsgTmFnU3VwcHJlc3Npb25zIH0gZnJvbSAnY2RrLW5hZyc7XG5pbXBvcnQgKiBhcyBjcnlwdG8gZnJvbSAnY3J5cHRvJztcbmltcG9ydCAqIGFzIGZzIGZyb20gJ2ZzJztcbmltcG9ydCAqIGFzIHBhdGggZnJvbSAncGF0aCc7XG5cbmV4cG9ydCBpbnRlcmZhY2UgU2VzQ2FtcGFpZ25BbmFseXRpY3NTdGFja1Byb3BzIGV4dGVuZHMgY2RrLlN0YWNrUHJvcHMge1xuICBleGlzdGluZ0NvbmZpZ3VyYXRpb25TZXROYW1lPzogc3RyaW5nO1xuICByZWZyZXNoU2NoZWR1bGVDcm9uOiBzdHJpbmc7XG4gIHJlZnJlc2hMb29rYmFja0RheXM6IG51bWJlcjtcbiAgZGF0YVJldGVudGlvbkRheXM6IG51bWJlcjtcbiAgZW5hYmxlTm90aWZpY2F0aW9uczogYm9vbGVhbjtcbiAgbm90aWZpY2F0aW9uRW1haWw/OiBzdHJpbmc7XG4gIGZpcmVob3NlQnVmZmVyU2l6ZU1COiBudW1iZXI7XG4gIGZpcmVob3NlQnVmZmVySW50ZXJ2YWxTZWNvbmRzOiBudW1iZXI7XG4gIGF0aGVuYVF1ZXJ5UmVzdWx0c1JldGVudGlvbkRheXM6IG51bWJlcjtcbiAgcHJvY2Vzc2VkRGF0YVRyYW5zaXRpb25Ub0lBRGF5czogbnVtYmVyO1xuICBsYW1iZGFUaW1lb3V0TWludXRlczogbnVtYmVyO1xuICBsYW1iZGFNZW1vcnlNQjogbnVtYmVyO1xuICBhdGhlbmFRdWVyeVNjYW5MaW1pdEdCOiBudW1iZXI7XG59XG5cbmV4cG9ydCBjbGFzcyBTZXNDYW1wYWlnbkFuYWx5dGljc1N0YWNrIGV4dGVuZHMgY2RrLlN0YWNrIHtcbiAgcHVibGljIHJlYWRvbmx5IHJhd0RhdGFCdWNrZXQ6IHMzLkJ1Y2tldDtcbiAgcHVibGljIHJlYWRvbmx5IHByb2Nlc3NlZERhdGFCdWNrZXQ6IHMzLkJ1Y2tldDtcbiAgcHVibGljIHJlYWRvbmx5IGF0aGVuYVJlc3VsdHNCdWNrZXQ6IHMzLkJ1Y2tldDtcbiAgcHVibGljIHJlYWRvbmx5IGdsdWVEYXRhYmFzZTogZ2x1ZS5DZm5EYXRhYmFzZTtcbiAgcHVibGljIHJlYWRvbmx5IHdvcmtHcm91cDogYXRoZW5hLkNmbldvcmtHcm91cDtcbiAgcHVibGljIHJlYWRvbmx5IGNvbmZpZ3VyYXRpb25TZXQ6IHNlcy5DZm5Db25maWd1cmF0aW9uU2V0O1xuXG4gIGNvbnN0cnVjdG9yKHNjb3BlOiBDb25zdHJ1Y3QsIGlkOiBzdHJpbmcsIHByb3BzOiBTZXNDYW1wYWlnbkFuYWx5dGljc1N0YWNrUHJvcHMpIHtcbiAgICBzdXBlcihzY29wZSwgaWQsIHtcbiAgICAgIC4uLnByb3BzLFxuICAgICAgZGVzY3JpcHRpb246ICdTRVMgQ2FtcGFpZ24gQW5hbHl0aWNzIC0gS2luZXNpcyBGaXJlaG9zZSwgQXRoZW5hLCBhbmQgR2x1ZSBmb3IgZGV0YWlsZWQgZW1haWwgY2FtcGFpZ24gbWV0cmljcyBhbmQgcmVwb3J0aW5nJyxcbiAgICB9KTtcblxuICAgIC8vIEFkZCBjb3N0IGFsbG9jYXRpb24gdGFncyBmb3IgdHJhY2tpbmcgZXhwZW5zZXNcbiAgICBUYWdzLm9mKHRoaXMpLmFkZCgnUHJvamVjdCcsICdTRVMtQ2FtcGFpZ24tQW5hbHl0aWNzJyk7XG4gICAgVGFncy5vZih0aGlzKS5hZGQoJ01hbmFnZWRCeScsICdDREsnKTtcbiAgICBUYWdzLm9mKHRoaXMpLmFkZCgnRW52aXJvbm1lbnQnLCAnUHJvZHVjdGlvbicpO1xuXG4gICAgLy8vLy8vLy8gU0VTIENPTkZJR1VSQVRJT04gU0VUIC8vLy8vLy8vXG4gICAgXG4gICAgLy8gVXNlIGV4aXN0aW5nIGNvbmZpZ3VyYXRpb24gc2V0IGlmIHByb3ZpZGVkLCBvdGhlcndpc2UgY3JlYXRlIGEgbmV3IG9uZVxuICAgIGxldCBjb25maWdTZXROYW1lOiBzdHJpbmc7XG4gICAgXG4gICAgaWYgKHByb3BzLmV4aXN0aW5nQ29uZmlndXJhdGlvblNldE5hbWUgJiYgcHJvcHMuZXhpc3RpbmdDb25maWd1cmF0aW9uU2V0TmFtZS50cmltKCkgIT09ICcnKSB7XG4gICAgICAvLyBVc2UgZXhpc3RpbmcgY29uZmlndXJhdGlvbiBzZXRcbiAgICAgIGNvbmZpZ1NldE5hbWUgPSBwcm9wcy5leGlzdGluZ0NvbmZpZ3VyYXRpb25TZXROYW1lO1xuICAgICAgY29uc29sZS5sb2coYFVzaW5nIGV4aXN0aW5nIFNFUyBDb25maWd1cmF0aW9uIFNldDogJHtjb25maWdTZXROYW1lfWApO1xuICAgIH0gZWxzZSB7XG4gICAgICAvLyBDcmVhdGUgbmV3IGNvbmZpZ3VyYXRpb24gc2V0XG4gICAgICB0aGlzLmNvbmZpZ3VyYXRpb25TZXQgPSBuZXcgc2VzLkNmbkNvbmZpZ3VyYXRpb25TZXQodGhpcywgJ1Nlc0NvbmZpZ3VyYXRpb25TZXQnLCB7XG4gICAgICAgIG5hbWU6IGBzZXMtYW5hbHl0aWNzLSR7Y2RrLlN0YWNrLm9mKHRoaXMpLmFjY291bnR9YCxcbiAgICAgIH0pO1xuICAgICAgY29uZmlnU2V0TmFtZSA9IHRoaXMuY29uZmlndXJhdGlvblNldC5uYW1lITtcbiAgICB9XG5cbiAgICAvLy8vLy8vLyBTMyBCVUNLRVRTIEZPUiBEQVRBIFNUT1JBR0UgLy8vLy8vLy9cbiAgICBcbiAgICAvLyBSYXcgU0VTIGV2ZW50cyBmcm9tIEZpcmVob3NlXG4gICAgdGhpcy5yYXdEYXRhQnVja2V0ID0gbmV3IHMzLkJ1Y2tldCh0aGlzLCAnU2VzUmF3RGF0YUJ1Y2tldCcsIHtcbiAgICAgIGVuY3J5cHRpb246IHMzLkJ1Y2tldEVuY3J5cHRpb24uUzNfTUFOQUdFRCxcbiAgICAgIGJsb2NrUHVibGljQWNjZXNzOiBzMy5CbG9ja1B1YmxpY0FjY2Vzcy5CTE9DS19BTEwsXG4gICAgICByZW1vdmFsUG9saWN5OiBSZW1vdmFsUG9saWN5LlJFVEFJTixcbiAgICAgIGVuZm9yY2VTU0w6IHRydWUsXG4gICAgICAvLyBObyBHbGFjaWVyIHRyYW5zaXRpb246IEF0aGVuYSBzaWxlbnRseSBza2lwcyBHbGFjaWVyIG9iamVjdHMsIHNvIG9sZGVyIGV2ZW50cyB3b3VsZFxuICAgICAgLy8gdmFuaXNoIGZyb20gcXVlcmllcyBhbmQgcmVmcmVzaGVzLCBhbmQgcGVyLW9iamVjdCBmZWVzIG91dHdlaWdoIHNhdmluZ3MgZm9yIHNtYWxsIGZpbGVzXG4gICAgICBsaWZlY3ljbGVSdWxlczogW1xuICAgICAgICB7XG4gICAgICAgICAgaWQ6ICdEZWxldGVPbGRSYXdEYXRhJyxcbiAgICAgICAgICBlbmFibGVkOiB0cnVlLFxuICAgICAgICAgIGV4cGlyYXRpb246IER1cmF0aW9uLmRheXMocHJvcHMuZGF0YVJldGVudGlvbkRheXMpLFxuICAgICAgICB9LFxuICAgICAgXSxcbiAgICB9KTtcblxuICAgIC8vIFByb2Nlc3NlZC90cmFuc2Zvcm1lZCBkYXRhXG4gICAgdGhpcy5wcm9jZXNzZWREYXRhQnVja2V0ID0gbmV3IHMzLkJ1Y2tldCh0aGlzLCAnU2VzUHJvY2Vzc2VkRGF0YUJ1Y2tldCcsIHtcbiAgICAgIGVuY3J5cHRpb246IHMzLkJ1Y2tldEVuY3J5cHRpb24uUzNfTUFOQUdFRCxcbiAgICAgIGJsb2NrUHVibGljQWNjZXNzOiBzMy5CbG9ja1B1YmxpY0FjY2Vzcy5CTE9DS19BTEwsXG4gICAgICByZW1vdmFsUG9saWN5OiBSZW1vdmFsUG9saWN5LlJFVEFJTixcbiAgICAgIGVuZm9yY2VTU0w6IHRydWUsXG4gICAgICBsaWZlY3ljbGVSdWxlczogW1xuICAgICAgICB7XG4gICAgICAgICAgaWQ6ICdUcmFuc2l0aW9uVG9JQScsXG4gICAgICAgICAgZW5hYmxlZDogdHJ1ZSxcbiAgICAgICAgICB0cmFuc2l0aW9uczogW1xuICAgICAgICAgICAge1xuICAgICAgICAgICAgICBzdG9yYWdlQ2xhc3M6IHMzLlN0b3JhZ2VDbGFzcy5JTkZSRVFVRU5UX0FDQ0VTUyxcbiAgICAgICAgICAgICAgdHJhbnNpdGlvbkFmdGVyOiBEdXJhdGlvbi5kYXlzKHByb3BzLnByb2Nlc3NlZERhdGFUcmFuc2l0aW9uVG9JQURheXMpLFxuICAgICAgICAgICAgfSxcbiAgICAgICAgICBdLFxuICAgICAgICB9LFxuICAgICAgXSxcbiAgICB9KTtcblxuICAgIC8vIEF0aGVuYSBxdWVyeSByZXN1bHRzXG4gICAgdGhpcy5hdGhlbmFSZXN1bHRzQnVja2V0ID0gbmV3IHMzLkJ1Y2tldCh0aGlzLCAnQXRoZW5hUmVzdWx0c0J1Y2tldCcsIHtcbiAgICAgIGVuY3J5cHRpb246IHMzLkJ1Y2tldEVuY3J5cHRpb24uUzNfTUFOQUdFRCxcbiAgICAgIGJsb2NrUHVibGljQWNjZXNzOiBzMy5CbG9ja1B1YmxpY0FjY2Vzcy5CTE9DS19BTEwsXG4gICAgICByZW1vdmFsUG9saWN5OiBSZW1vdmFsUG9saWN5LlJFVEFJTixcbiAgICAgIGVuZm9yY2VTU0w6IHRydWUsXG4gICAgICBsaWZlY3ljbGVSdWxlczogW1xuICAgICAgICB7XG4gICAgICAgICAgaWQ6ICdEZWxldGVPbGRRdWVyeVJlc3VsdHMnLFxuICAgICAgICAgIGVuYWJsZWQ6IHRydWUsXG4gICAgICAgICAgZXhwaXJhdGlvbjogRHVyYXRpb24uZGF5cyhwcm9wcy5hdGhlbmFRdWVyeVJlc3VsdHNSZXRlbnRpb25EYXlzKSxcbiAgICAgICAgfSxcbiAgICAgIF0sXG4gICAgfSk7XG5cbiAgICAvLy8vLy8vLyBBV1MgR0xVRSBEQVRBQkFTRSBBTkQgVEFCTEVTIC8vLy8vLy8vXG4gICAgXG4gICAgdGhpcy5nbHVlRGF0YWJhc2UgPSBuZXcgZ2x1ZS5DZm5EYXRhYmFzZSh0aGlzLCAnU2VzRXZlbnREYXRhYmFzZScsIHtcbiAgICAgIGNhdGFsb2dJZDogY2RrLlN0YWNrLm9mKHRoaXMpLmFjY291bnQsXG4gICAgICBkYXRhYmFzZUlucHV0OiB7XG4gICAgICAgIG5hbWU6IGBzZXNfYW5hbHl0aWNzX2RiXyR7Y2RrLlN0YWNrLm9mKHRoaXMpLmFjY291bnR9YCxcbiAgICAgICAgZGVzY3JpcHRpb246ICdEYXRhYmFzZSBmb3IgU0VTIGNhbXBhaWduIGFuYWx5dGljcyBldmVudHMgYW5kIG1hdGVyaWFsaXplZCB2aWV3cycsXG4gICAgICB9LFxuICAgIH0pO1xuXG4gICAgLy8gQ3JlYXRlIEF0aGVuYSBOYW1lZCBRdWVyeSBmb3IgdGFibGUgY3JlYXRpb24gKGZvbGxvd2luZyBDbG91ZEZvcm1hdGlvbiBwYXR0ZXJuKVxuICAgIGNvbnN0IGNyZWF0ZVRhYmxlUXVlcnkgPSBuZXcgYXRoZW5hLkNmbk5hbWVkUXVlcnkodGhpcywgJ0NyZWF0ZVNlc0V2ZW50c1RhYmxlJywge1xuICAgICAgZGF0YWJhc2U6IHRoaXMuZ2x1ZURhdGFiYXNlLnJlZixcbiAgICAgIGRlc2NyaXB0aW9uOiAnQ3JlYXRlIHRhYmxlIGZvciBTRVMgZXZlbnRzJyxcbiAgICAgIG5hbWU6ICdjcmVhdGVfc2VzX2V2ZW50c190YWJsZScsXG4gICAgICBxdWVyeVN0cmluZzogYENSRUFURSBFWFRFUk5BTCBUQUJMRSBJRiBOT1QgRVhJU1RTIHNlc19ldmVudHNfcmF3IChcbiAgZXZlbnRUeXBlIHN0cmluZyxcbiAgbWFpbCBzdHJ1Y3Q8XG4gICAgdGltZXN0YW1wOiBzdHJpbmcsXG4gICAgc291cmNlOiBzdHJpbmcsXG4gICAgc291cmNlQXJuOiBzdHJpbmcsXG4gICAgc2VuZGluZ0FjY291bnRJZDogc3RyaW5nLFxuICAgIG1lc3NhZ2VJZDogc3RyaW5nLFxuICAgIGRlc3RpbmF0aW9uOiBhcnJheTxzdHJpbmc+LFxuICAgIGhlYWRlcnNUcnVuY2F0ZWQ6IGJvb2xlYW4sXG4gICAgaGVhZGVyczogYXJyYXk8c3RydWN0PG5hbWU6IHN0cmluZywgdmFsdWU6IHN0cmluZz4+LFxuICAgIGNvbW1vbkhlYWRlcnM6IHN0cnVjdDxcXGBmcm9tXFxgOiBhcnJheTxzdHJpbmc+LCBcXGB0b1xcYDogYXJyYXk8c3RyaW5nPiwgbWVzc2FnZUlkOiBzdHJpbmcsIHN1YmplY3Q6IHN0cmluZz4sXG4gICAgdGFnczogc3RydWN0PFxuICAgICAgY2FtcGFpZ25faWQ6IGFycmF5PHN0cmluZz4sXG4gICAgICBjYW1wYWlnbl9uYW1lOiBhcnJheTxzdHJpbmc+XG4gICAgPlxuICA+LFxuICBzZW5kIG1hcDxzdHJpbmcsc3RyaW5nPixcbiAgZGVsaXZlcnkgc3RydWN0PFxuICAgIHRpbWVzdGFtcDogc3RyaW5nLFxuICAgIHByb2Nlc3NpbmdUaW1lTWlsbGlzOiBiaWdpbnQsXG4gICAgcmVjaXBpZW50czogYXJyYXk8c3RyaW5nPixcbiAgICBzbXRwUmVzcG9uc2U6IHN0cmluZyxcbiAgICByZXBvcnRpbmdNVEE6IHN0cmluZ1xuICA+LFxuICBvcGVuIHN0cnVjdDxcbiAgICBpcEFkZHJlc3M6IHN0cmluZyxcbiAgICB0aW1lc3RhbXA6IHN0cmluZyxcbiAgICB1c2VyQWdlbnQ6IHN0cmluZ1xuICA+LFxuICBjbGljayBzdHJ1Y3Q8XG4gICAgaXBBZGRyZXNzOiBzdHJpbmcsXG4gICAgbGluazogc3RyaW5nLFxuICAgIGxpbmtUYWdzOiBtYXA8c3RyaW5nLGFycmF5PHN0cmluZz4+LFxuICAgIHRpbWVzdGFtcDogc3RyaW5nLFxuICAgIHVzZXJBZ2VudDogc3RyaW5nXG4gID4sXG4gIGJvdW5jZSBzdHJ1Y3Q8XG4gICAgYm91bmNlVHlwZTogc3RyaW5nLFxuICAgIGJvdW5jZVN1YlR5cGU6IHN0cmluZyxcbiAgICBib3VuY2VkUmVjaXBpZW50czogYXJyYXk8c3RydWN0PFxuICAgICAgZW1haWxBZGRyZXNzOiBzdHJpbmcsXG4gICAgICBhY3Rpb246IHN0cmluZyxcbiAgICAgIHN0YXR1czogc3RyaW5nLFxuICAgICAgZGlhZ25vc3RpY0NvZGU6IHN0cmluZ1xuICAgID4+LFxuICAgIHRpbWVzdGFtcDogc3RyaW5nLFxuICAgIGZlZWRiYWNrSWQ6IHN0cmluZyxcbiAgICByZXBvcnRpbmdNVEE6IHN0cmluZ1xuICA+LFxuICBjb21wbGFpbnQgc3RydWN0PFxuICAgIGNvbXBsYWluZWRSZWNpcGllbnRzOiBhcnJheTxzdHJ1Y3Q8XG4gICAgICBlbWFpbEFkZHJlc3M6IHN0cmluZ1xuICAgID4+LFxuICAgIHRpbWVzdGFtcDogc3RyaW5nLFxuICAgIGZlZWRiYWNrSWQ6IHN0cmluZyxcbiAgICB1c2VyQWdlbnQ6IHN0cmluZyxcbiAgICBjb21wbGFpbnRGZWVkYmFja1R5cGU6IHN0cmluZyxcbiAgICBhcnJpdmFsRGF0ZTogc3RyaW5nXG4gID4sXG4gIHJlamVjdCBzdHJ1Y3Q8XG4gICAgcmVhc29uOiBzdHJpbmdcbiAgPixcbiAgcmVuZGVyaW5nRmFpbHVyZSBzdHJ1Y3Q8XG4gICAgZXJyb3JNZXNzYWdlOiBzdHJpbmcsXG4gICAgdGVtcGxhdGVOYW1lOiBzdHJpbmdcbiAgPlxuKVxuUEFSVElUSU9ORUQgQlkgKGluZ2VzdF90aW1lc3RhbXAgdGltZXN0YW1wKVxuU1RPUkVEIEFTIHBhcnF1ZXRcbkxPQ0FUSU9OIFwiczM6Ly8ke3RoaXMucmF3RGF0YUJ1Y2tldC5idWNrZXROYW1lfS9ldmVudHNcIlxuVEJMUFJPUEVSVElFUyAoXG4gIFwicGFycXVldC5jb21wcmVzc2lvblwiPVwiU05BUFBZXCIsXG4gIFwicHJvamVjdGlvbi5lbmFibGVkXCI9XCJmYWxzZVwiXG4pYCxcbiAgICB9KTtcblxuICAgIC8vIFZpZXdzIGZyb20gc3FsLyAoY29tbWVudCBsaW5lcyBzdHJpcHBlZCAtIEF0aGVuYSBEREwgbXVzdCBzdGFydCB3aXRoIHRoZSBzdGF0ZW1lbnQpXG4gICAgY29uc3Qgdmlld0ZpbGVzID0gWydjYW1wYWlnbl9zdW1tYXJ5X3ZpZXcuc3FsJywgJ2NhbXBhaWduX3JlYWx0aW1lX3ZpZXcuc3FsJ107XG4gICAgY29uc3Qgdmlld1NxbCA9IHZpZXdGaWxlcy5tYXAoKGZpbGUpID0+XG4gICAgICBmcy5yZWFkRmlsZVN5bmMocGF0aC5qb2luKF9fZGlybmFtZSwgJy4uL3NxbCcsIGZpbGUpLCAndXRmLTgnKVxuICAgICAgICAuc3BsaXQoJ1xcbicpXG4gICAgICAgIC5maWx0ZXIoKGxpbmUpID0+ICFsaW5lLnRyaW0oKS5zdGFydHNXaXRoKCctLScpKVxuICAgICAgICAuam9pbignXFxuJylcbiAgICAgICAgLnRyaW0oKSxcbiAgICApO1xuICAgIGNvbnN0IHZpZXdRdWVyaWVzID0gdmlld1NxbC5tYXAoKHF1ZXJ5U3RyaW5nLCBpbmRleCkgPT5cbiAgICAgIG5ldyBhdGhlbmEuQ2ZuTmFtZWRRdWVyeSh0aGlzLCBgQ3JlYXRlVmlldyR7aW5kZXh9YCwge1xuICAgICAgICBkYXRhYmFzZTogdGhpcy5nbHVlRGF0YWJhc2UucmVmLFxuICAgICAgICBkZXNjcmlwdGlvbjogYENyZWF0ZSB2aWV3IGZyb20gc3FsLyR7dmlld0ZpbGVzW2luZGV4XX1gLFxuICAgICAgICBuYW1lOiBgY3JlYXRlXyR7dmlld0ZpbGVzW2luZGV4XS5yZXBsYWNlKCcuc3FsJywgJycpfWAsXG4gICAgICAgIHF1ZXJ5U3RyaW5nLFxuICAgICAgfSksXG4gICAgKTtcblxuICAgIC8vIExhbWJkYSB0byBleGVjdXRlIHRoZSBuYW1lZCBxdWVyaWVzIGFuZCBjcmVhdGUgdGhlIHRhYmxlIGFuZCB2aWV3c1xuICAgIGNvbnN0IGNyZWF0ZVRhYmxlTGFtYmRhID0gbmV3IGxhbWJkYS5GdW5jdGlvbih0aGlzLCAnQ3JlYXRlVGFibGVMYW1iZGEnLCB7XG4gICAgICBydW50aW1lOiBsYW1iZGEuUnVudGltZS5QWVRIT05fM18xMixcbiAgICAgIGhhbmRsZXI6ICdjcmVhdGVUYWJsZS5oYW5kbGVyJyxcbiAgICAgIGNvZGU6IGxhbWJkYS5Db2RlLmZyb21Bc3NldChwYXRoLmpvaW4oX19kaXJuYW1lLCAnLi4vbGFtYmRhJykpLFxuICAgICAgdGltZW91dDogRHVyYXRpb24ubWludXRlcygxMCksIC8vIEluY3JlYXNlZCBmcm9tIDUgdG8gMTAgbWludXRlcyBmb3IgQXRoZW5hIHF1ZXJ5IGNvbXBsZXRpb25cbiAgICAgIGVudmlyb25tZW50OiB7XG4gICAgICAgIE5BTUVEX1FVRVJZX0lEOiBjcmVhdGVUYWJsZVF1ZXJ5LmF0dHJOYW1lZFF1ZXJ5SWQsXG4gICAgICAgIFZJRVdfUVVFUllfSURTOiBjZGsuRm4uam9pbignLCcsIHZpZXdRdWVyaWVzLm1hcCgocXVlcnkpID0+IHF1ZXJ5LmF0dHJOYW1lZFF1ZXJ5SWQpKSxcbiAgICAgICAgREFUQUJBU0VfTkFNRTogdGhpcy5nbHVlRGF0YWJhc2UucmVmLFxuICAgICAgICBPVVRQVVRfTE9DQVRJT046IGBzMzovLyR7dGhpcy5hdGhlbmFSZXN1bHRzQnVja2V0LmJ1Y2tldE5hbWV9L3RhYmxlLWNyZWF0aW9uL2AsXG4gICAgICB9LFxuICAgIH0pO1xuXG4gICAgdGhpcy5hdGhlbmFSZXN1bHRzQnVja2V0LmdyYW50UmVhZFdyaXRlKGNyZWF0ZVRhYmxlTGFtYmRhKTtcbiAgICBcbiAgICBjcmVhdGVUYWJsZUxhbWJkYS5hZGRUb1JvbGVQb2xpY3koXG4gICAgICBuZXcgaWFtLlBvbGljeVN0YXRlbWVudCh7XG4gICAgICAgIGFjdGlvbnM6IFtcbiAgICAgICAgICAnYXRoZW5hOkdldE5hbWVkUXVlcnknLFxuICAgICAgICAgICdhdGhlbmE6U3RhcnRRdWVyeUV4ZWN1dGlvbicsXG4gICAgICAgICAgJ2F0aGVuYTpHZXRRdWVyeUV4ZWN1dGlvbicsXG4gICAgICAgIF0sXG4gICAgICAgIHJlc291cmNlczogWycqJ10sXG4gICAgICB9KSxcbiAgICApO1xuXG4gICAgY3JlYXRlVGFibGVMYW1iZGEuYWRkVG9Sb2xlUG9saWN5KFxuICAgICAgbmV3IGlhbS5Qb2xpY3lTdGF0ZW1lbnQoe1xuICAgICAgICBhY3Rpb25zOiBbXG4gICAgICAgICAgJ2dsdWU6R2V0RGF0YWJhc2UnLFxuICAgICAgICAgICdnbHVlOkdldFRhYmxlJyxcbiAgICAgICAgICAnZ2x1ZTpDcmVhdGVUYWJsZScsXG4gICAgICAgICAgJ2dsdWU6VXBkYXRlVGFibGUnLCAvLyBDUkVBVEUgT1IgUkVQTEFDRSBWSUVXXG4gICAgICAgIF0sXG4gICAgICAgIHJlc291cmNlczogW1xuICAgICAgICAgIGBhcm46YXdzOmdsdWU6JHtjZGsuU3RhY2sub2YodGhpcykucmVnaW9ufToke2Nkay5TdGFjay5vZih0aGlzKS5hY2NvdW50fTpjYXRhbG9nYCxcbiAgICAgICAgICBgYXJuOmF3czpnbHVlOiR7Y2RrLlN0YWNrLm9mKHRoaXMpLnJlZ2lvbn06JHtjZGsuU3RhY2sub2YodGhpcykuYWNjb3VudH06ZGF0YWJhc2UvJHt0aGlzLmdsdWVEYXRhYmFzZS5yZWZ9YCxcbiAgICAgICAgICBgYXJuOmF3czpnbHVlOiR7Y2RrLlN0YWNrLm9mKHRoaXMpLnJlZ2lvbn06JHtjZGsuU3RhY2sub2YodGhpcykuYWNjb3VudH06dGFibGUvJHt0aGlzLmdsdWVEYXRhYmFzZS5yZWZ9LypgLFxuICAgICAgICBdLFxuICAgICAgfSksXG4gICAgKTtcblxuICAgIC8vIFN1cHByZXNzIENESyBOYWcgd2FybmluZyBmb3IgR2x1ZSB0YWJsZSB3aWxkY2FyZCBwZXJtaXNzaW9uc1xuICAgIE5hZ1N1cHByZXNzaW9ucy5hZGRSZXNvdXJjZVN1cHByZXNzaW9ucyhcbiAgICAgIGNyZWF0ZVRhYmxlTGFtYmRhLFxuICAgICAgW1xuICAgICAgICB7XG4gICAgICAgICAgaWQ6ICdBd3NTb2x1dGlvbnMtSUFNNScsXG4gICAgICAgICAgcmVhc29uOiAnTGFtYmRhIG5lZWRzIHdpbGRjYXJkIHBlcm1pc3Npb25zIHRvIGFjY2VzcyBHbHVlIHRhYmxlIG1ldGFkYXRhIGZvciBBdGhlbmEgcXVlcnkgZXhlY3V0aW9uIHRoYXQgY3JlYXRlcyB0YWJsZXMuJyxcbiAgICAgICAgfSxcbiAgICAgIF0sXG4gICAgICB0cnVlLCAvLyBhcHBseVRvQ2hpbGRyZW4gLSBhcHBsaWVzIHRvIHRoZSBMYW1iZGEncyByb2xlIGFuZCBwb2xpY2llc1xuICAgICk7XG5cbiAgICAvLyBDdXN0b20gcmVzb3VyY2UgdG8gdHJpZ2dlciB0YWJsZSBjcmVhdGlvblxuICAgIGNvbnN0IHRhYmxlQ3JlYXRpb25UcmlnZ2VyID0gbmV3IGNkay5DdXN0b21SZXNvdXJjZSh0aGlzLCAnVGFibGVDcmVhdGlvblRyaWdnZXInLCB7XG4gICAgICBzZXJ2aWNlVG9rZW46IGNyZWF0ZVRhYmxlTGFtYmRhLmZ1bmN0aW9uQXJuLFxuICAgICAgcHJvcGVydGllczoge1xuICAgICAgICAvLyBSZS1ydW4gKGFuZCByZS1jcmVhdGUgdGhlIHZpZXdzKSB3aGVuZXZlciB0aGUgdmlldyBTUUwgY2hhbmdlc1xuICAgICAgICBWaWV3c0hhc2g6IGNyeXB0by5jcmVhdGVIYXNoKCdzaGEyNTYnKS51cGRhdGUodmlld1NxbC5qb2luKCdcXG4nKSkuZGlnZXN0KCdoZXgnKSxcbiAgICAgIH0sXG4gICAgfSk7XG5cbiAgICB0YWJsZUNyZWF0aW9uVHJpZ2dlci5ub2RlLmFkZERlcGVuZGVuY3kodGhpcy5nbHVlRGF0YWJhc2UpO1xuICAgIHRhYmxlQ3JlYXRpb25UcmlnZ2VyLm5vZGUuYWRkRGVwZW5kZW5jeShjcmVhdGVUYWJsZVF1ZXJ5KTtcbiAgICB2aWV3UXVlcmllcy5mb3JFYWNoKChxdWVyeSkgPT4gdGFibGVDcmVhdGlvblRyaWdnZXIubm9kZS5hZGREZXBlbmRlbmN5KHF1ZXJ5KSk7XG5cbiAgICAvLyBSZWZlcmVuY2UgZm9yIEZpcmVob3NlIC0gdXNlIHRoZSB0YWJsZSBuYW1lIGRpcmVjdGx5XG4gICAgY29uc3QgcmF3RXZlbnRzVGFibGVOYW1lID0gJ3Nlc19ldmVudHNfcmF3JztcblxuICAgIC8vIE1hdGVyaWFsaXplZCB2aWV3IHRhYmxlIGZvciBjYW1wYWlnbiBtZXRyaWNzXG4gICAgY29uc3QgY2FtcGFpZ25NZXRyaWNzVGFibGUgPSBuZXcgZ2x1ZS5DZm5UYWJsZSh0aGlzLCAnQ2FtcGFpZ25NZXRyaWNzVGFibGUnLCB7XG4gICAgICBjYXRhbG9nSWQ6IGNkay5TdGFjay5vZih0aGlzKS5hY2NvdW50LFxuICAgICAgZGF0YWJhc2VOYW1lOiB0aGlzLmdsdWVEYXRhYmFzZS5yZWYsXG4gICAgICB0YWJsZUlucHV0OiB7XG4gICAgICAgIG5hbWU6ICdjYW1wYWlnbl9tZXRyaWNzX2RhaWx5JyxcbiAgICAgICAgZGVzY3JpcHRpb246ICdNYXRlcmlhbGl6ZWQgdmlldyBvZiBkYWlseSBjYW1wYWlnbiBtZXRyaWNzJyxcbiAgICAgICAgdGFibGVUeXBlOiAnRVhURVJOQUxfVEFCTEUnLFxuICAgICAgICBwYXJhbWV0ZXJzOiB7XG4gICAgICAgICAgJ3Byb2plY3Rpb24uZW5hYmxlZCc6ICd0cnVlJyxcbiAgICAgICAgICAncHJvamVjdGlvbi5kYXRlLnR5cGUnOiAnZGF0ZScsXG4gICAgICAgICAgJ3Byb2plY3Rpb24uZGF0ZS5yYW5nZSc6ICcyMDI0LTAxLTAxLE5PVycsXG4gICAgICAgICAgJ3Byb2plY3Rpb24uZGF0ZS5mb3JtYXQnOiAneXl5eS1NTS1kZCcsXG4gICAgICAgICAgJ3N0b3JhZ2UubG9jYXRpb24udGVtcGxhdGUnOiBgczM6Ly8ke3RoaXMucHJvY2Vzc2VkRGF0YUJ1Y2tldC5idWNrZXROYW1lfS9tYXRlcmlhbGl6ZWQtdmlld3MvY2FtcGFpZ25fbWV0cmljc19kYWlseS9kYXRlPVxcJHtkYXRlfWAsXG4gICAgICAgIH0sXG4gICAgICAgIHBhcnRpdGlvbktleXM6IFtcbiAgICAgICAgICB7IG5hbWU6ICdkYXRlJywgdHlwZTogJ3N0cmluZycgfSxcbiAgICAgICAgXSxcbiAgICAgICAgc3RvcmFnZURlc2NyaXB0b3I6IHtcbiAgICAgICAgICBjb2x1bW5zOiBbXG4gICAgICAgICAgICB7IG5hbWU6ICdjYW1wYWlnbl9pZCcsIHR5cGU6ICdzdHJpbmcnIH0sXG4gICAgICAgICAgICB7IG5hbWU6ICdjYW1wYWlnbl9uYW1lJywgdHlwZTogJ3N0cmluZycgfSxcbiAgICAgICAgICAgIHsgbmFtZTogJ2VtYWlsc19zZW50JywgdHlwZTogJ2JpZ2ludCcgfSxcbiAgICAgICAgICAgIHsgbmFtZTogJ2VtYWlsc19kZWxpdmVyZWQnLCB0eXBlOiAnYmlnaW50JyB9LFxuICAgICAgICAgICAgeyBuYW1lOiAnZW1haWxzX29wZW5lZCcsIHR5cGU6ICdiaWdpbnQnIH0sXG4gICAgICAgICAgICB7IG5hbWU6ICdlbWFpbHNfY2xpY2tlZCcsIHR5cGU6ICdiaWdpbnQnIH0sXG4gICAgICAgICAgICB7IG5hbWU6ICdoYXJkX2JvdW5jZXMnLCB0eXBlOiAnYmlnaW50JyB9LFxuICAgICAgICAgICAgeyBuYW1lOiAnc29mdF9ib3VuY2VzJywgdHlwZTogJ2JpZ2ludCcgfSxcbiAgICAgICAgICAgIHsgbmFtZTogJ2NvbXBsYWludHMnLCB0eXBlOiAnYmlnaW50JyB9LFxuICAgICAgICAgICAgeyBuYW1lOiAncmVqZWN0cycsIHR5cGU6ICdiaWdpbnQnIH0sXG4gICAgICAgICAgICB7IG5hbWU6ICdyZW5kZXJpbmdfZmFpbHVyZXMnLCB0eXBlOiAnYmlnaW50JyB9LFxuICAgICAgICAgICAgeyBuYW1lOiAnZGVsaXZlcnlfcmF0ZScsIHR5cGU6ICdkb3VibGUnIH0sXG4gICAgICAgICAgICB7IG5hbWU6ICdvcGVuX3JhdGUnLCB0eXBlOiAnZG91YmxlJyB9LFxuICAgICAgICAgICAgeyBuYW1lOiAnY2xpY2tfcmF0ZScsIHR5cGU6ICdkb3VibGUnIH0sXG4gICAgICAgICAgICB7IG5hbWU6ICdoYXJkX2JvdW5jZV9yYXRlJywgdHlwZTogJ2RvdWJsZScgfSxcbiAgICAgICAgICAgIHsgbmFtZTogJ2NvbXBsYWludF9yYXRlJywgdHlwZTogJ2RvdWJsZScgfSxcbiAgICAgICAgICAgIHsgbmFtZTogJ3JlbmRlcmluZ19mYWlsdXJlX3JhdGUnLCB0eXBlOiAnZG91YmxlJyB9LFxuICAgICAgICAgICAgeyBuYW1lOiAndW5pcXVlX3JlY2lwaWVudHMnLCB0eXBlOiAnYmlnaW50JyB9LFxuICAgICAgICAgICAgeyBuYW1lOiAnYXZnX2RlbGl2ZXJ5X3RpbWVfbXMnLCB0eXBlOiAnZG91YmxlJyB9LFxuICAgICAgICAgICAgeyBuYW1lOiAnZnJvbV9hZGRyZXNzJywgdHlwZTogJ3N0cmluZycgfSxcbiAgICAgICAgICAgIHsgbmFtZTogJ3NhbXBsZV9zdWJqZWN0JywgdHlwZTogJ3N0cmluZycgfSxcbiAgICAgICAgICBdLFxuICAgICAgICAgIGxvY2F0aW9uOiBgczM6Ly8ke3RoaXMucHJvY2Vzc2VkRGF0YUJ1Y2tldC5idWNrZXROYW1lfS9tYXRlcmlhbGl6ZWQtdmlld3MvY2FtcGFpZ25fbWV0cmljc19kYWlseS9gLFxuICAgICAgICAgIGlucHV0Rm9ybWF0OiAnb3JnLmFwYWNoZS5oYWRvb3AuaGl2ZS5xbC5pby5wYXJxdWV0Lk1hcHJlZFBhcnF1ZXRJbnB1dEZvcm1hdCcsXG4gICAgICAgICAgb3V0cHV0Rm9ybWF0OiAnb3JnLmFwYWNoZS5oYWRvb3AuaGl2ZS5xbC5pby5wYXJxdWV0Lk1hcHJlZFBhcnF1ZXRPdXRwdXRGb3JtYXQnLFxuICAgICAgICAgIHNlcmRlSW5mbzoge1xuICAgICAgICAgICAgc2VyaWFsaXphdGlvbkxpYnJhcnk6ICdvcmcuYXBhY2hlLmhhZG9vcC5oaXZlLnFsLmlvLnBhcnF1ZXQuc2VyZGUuUGFycXVldEhpdmVTZXJEZScsXG4gICAgICAgICAgICBwYXJhbWV0ZXJzOiB7XG4gICAgICAgICAgICAgICdzZXJpYWxpemF0aW9uLmZvcm1hdCc6ICcxJyxcbiAgICAgICAgICAgIH0sXG4gICAgICAgICAgfSxcbiAgICAgICAgfSxcbiAgICAgIH0sXG4gICAgfSk7XG5cbiAgICB0YWJsZUNyZWF0aW9uVHJpZ2dlci5ub2RlLmFkZERlcGVuZGVuY3koY2FtcGFpZ25NZXRyaWNzVGFibGUpO1xuXG4gICAgLy8vLy8vLy8gQVRIRU5BIFdPUktHUk9VUCAvLy8vLy8vL1xuICAgIFxuICAgIHRoaXMud29ya0dyb3VwID0gbmV3IGF0aGVuYS5DZm5Xb3JrR3JvdXAodGhpcywgJ1Nlc0FuYWx5dGljc1dvcmtHcm91cCcsIHtcbiAgICAgIG5hbWU6IGBzZXMtYW5hbHl0aWNzLXdnLSR7Y2RrLlN0YWNrLm9mKHRoaXMpLmFjY291bnR9YCxcbiAgICAgIGRlc2NyaXB0aW9uOiAnV29ya2dyb3VwIGZvciBTRVMgY2FtcGFpZ24gYW5hbHl0aWNzIHF1ZXJpZXMnLFxuICAgICAgcmVjdXJzaXZlRGVsZXRlT3B0aW9uOiB0cnVlLCAvLyBFbmFibGUgcmVjdXJzaXZlIGRlbGV0aW9uIHRvIGNsZWFuIHVwIHF1ZXJpZXNcbiAgICAgIHdvcmtHcm91cENvbmZpZ3VyYXRpb246IHtcbiAgICAgICAgcmVzdWx0Q29uZmlndXJhdGlvbjoge1xuICAgICAgICAgIG91dHB1dExvY2F0aW9uOiBgczM6Ly8ke3RoaXMuYXRoZW5hUmVzdWx0c0J1Y2tldC5idWNrZXROYW1lfS9xdWVyeS1yZXN1bHRzL2AsXG4gICAgICAgICAgZW5jcnlwdGlvbkNvbmZpZ3VyYXRpb246IHtcbiAgICAgICAgICAgIGVuY3J5cHRpb25PcHRpb246ICdTU0VfUzMnLFxuICAgICAgICAgIH0sXG4gICAgICAgIH0sXG4gICAgICAgIGVuZm9yY2VXb3JrR3JvdXBDb25maWd1cmF0aW9uOiB0cnVlLFxuICAgICAgICBwdWJsaXNoQ2xvdWRXYXRjaE1ldHJpY3NFbmFibGVkOiB0cnVlLFxuICAgICAgICBieXRlc1NjYW5uZWRDdXRvZmZQZXJRdWVyeTogcHJvcHMuYXRoZW5hUXVlcnlTY2FuTGltaXRHQiAqIDEwMjQgKiAxMDI0ICogMTAyNCxcbiAgICAgICAgZW5naW5lVmVyc2lvbjoge1xuICAgICAgICAgIHNlbGVjdGVkRW5naW5lVmVyc2lvbjogJ0F0aGVuYSBlbmdpbmUgdmVyc2lvbiAzJyxcbiAgICAgICAgfSxcbiAgICAgIH0sXG4gICAgfSk7XG4gICAgXG4gICAgLy8gQXBwbHkgcmVtb3ZhbCBwb2xpY3kgdG8gYWxsb3cgZGVsZXRpb25cbiAgICB0aGlzLndvcmtHcm91cC5hcHBseVJlbW92YWxQb2xpY3koUmVtb3ZhbFBvbGljeS5ERVNUUk9ZKTtcblxuICAgIC8vLy8vLy8vIElBTSBST0xFUyBGT1IgS0lORVNJUyBGSVJFSE9TRSAvLy8vLy8vL1xuICAgIFxuICAgIC8vIFJvbGUgZm9yIEZpcmVob3NlIHNlcnZpY2UgaXRzZWxmXG4gICAgY29uc3QgZmlyZWhvc2VSb2xlID0gbmV3IGlhbS5Sb2xlKHRoaXMsICdGaXJlaG9zZVJvbGUnLCB7XG4gICAgICBhc3N1bWVkQnk6IG5ldyBpYW0uU2VydmljZVByaW5jaXBhbCgnZmlyZWhvc2UuYW1hem9uYXdzLmNvbScpLFxuICAgICAgZGVzY3JpcHRpb246ICdSb2xlIGZvciBLaW5lc2lzIEZpcmVob3NlIHRvIHdyaXRlIFNFUyBldmVudHMgdG8gUzMgYW5kIGNvbnZlcnQgdG8gUGFycXVldCcsXG4gICAgfSk7XG5cbiAgICB0aGlzLnJhd0RhdGFCdWNrZXQuZ3JhbnRXcml0ZShmaXJlaG9zZVJvbGUpO1xuICAgIFxuICAgIC8vIFNlcGFyYXRlIHJvbGUgZm9yIFNFUyB0byBwdXQgcmVjb3JkcyBpbnRvIEZpcmVob3NlXG4gICAgY29uc3Qgc2VzRmlyZWhvc2VSb2xlID0gbmV3IGlhbS5Sb2xlKHRoaXMsICdTZXNGaXJlaG9zZVJvbGUnLCB7XG4gICAgICBhc3N1bWVkQnk6IG5ldyBpYW0uU2VydmljZVByaW5jaXBhbCgnc2VzLmFtYXpvbmF3cy5jb20nKSxcbiAgICAgIGRlc2NyaXB0aW9uOiAnUm9sZSBmb3IgU0VTIHRvIHB1dCByZWNvcmRzIGludG8gS2luZXNpcyBGaXJlaG9zZScsXG4gICAgfSk7XG5cbiAgICAvLyBDbG91ZFdhdGNoIExvZ3MgZm9yIEZpcmVob3NlXG4gICAgY29uc3QgZmlyZWhvc2VMb2dHcm91cCA9IG5ldyBsb2dzLkxvZ0dyb3VwKHRoaXMsICdGaXJlaG9zZUxvZ0dyb3VwJywge1xuICAgICAgbG9nR3JvdXBOYW1lOiBgL2F3cy9raW5lc2lzZmlyZWhvc2Uvc2VzLWFuYWx5dGljcy0ke2Nkay5TdGFjay5vZih0aGlzKS5hY2NvdW50fWAsXG4gICAgICByZXRlbnRpb246IGxvZ3MuUmV0ZW50aW9uRGF5cy5PTkVfV0VFSyxcbiAgICAgIHJlbW92YWxQb2xpY3k6IFJlbW92YWxQb2xpY3kuREVTVFJPWSxcbiAgICB9KTtcblxuICAgIGNvbnN0IGZpcmVob3NlTG9nU3RyZWFtID0gbmV3IGxvZ3MuTG9nU3RyZWFtKHRoaXMsICdGaXJlaG9zZUxvZ1N0cmVhbScsIHtcbiAgICAgIGxvZ0dyb3VwOiBmaXJlaG9zZUxvZ0dyb3VwLFxuICAgICAgbG9nU3RyZWFtTmFtZTogJ1MzRGVsaXZlcnknLFxuICAgIH0pO1xuXG4gICAgZmlyZWhvc2VMb2dHcm91cC5ncmFudFdyaXRlKGZpcmVob3NlUm9sZSk7XG5cbiAgICAvLy8vLy8vLyBLSU5FU0lTIERBVEEgRklSRUhPU0UgREVMSVZFUlkgU1RSRUFNIC8vLy8vLy8vXG4gICAgXG4gICAgY29uc3QgZGVsaXZlcnlTdHJlYW0gPSBuZXcgZmlyZWhvc2UuQ2ZuRGVsaXZlcnlTdHJlYW0odGhpcywgJ1Nlc0V2ZW50c0RlbGl2ZXJ5U3RyZWFtJywge1xuICAgICAgZGVsaXZlcnlTdHJlYW1OYW1lOiBgc2VzLWFuYWx5dGljcy1zdHJlYW0tJHtjZGsuU3RhY2sub2YodGhpcykuYWNjb3VudH1gLFxuICAgICAgZGVsaXZlcnlTdHJlYW1UeXBlOiAnRGlyZWN0UHV0JyxcbiAgICAgIGV4dGVuZGVkUzNEZXN0aW5hdGlvbkNvbmZpZ3VyYXRpb246IHtcbiAgICAgICAgYnVja2V0QXJuOiB0aGlzLnJhd0RhdGFCdWNrZXQuYnVja2V0QXJuLFxuICAgICAgICByb2xlQXJuOiBmaXJlaG9zZVJvbGUucm9sZUFybixcbiAgICAgICAgcHJlZml4OiAnZXZlbnRzL3llYXI9IXt0aW1lc3RhbXA6eXl5eX0vbW9udGg9IXt0aW1lc3RhbXA6TU19L2RheT0he3RpbWVzdGFtcDpkZH0vaG91cj0he3RpbWVzdGFtcDpISH0vJyxcbiAgICAgICAgZXJyb3JPdXRwdXRQcmVmaXg6ICdlcnJvcnMvIXtmaXJlaG9zZTplcnJvci1vdXRwdXQtdHlwZX0veWVhcj0he3RpbWVzdGFtcDp5eXl5fS9tb250aD0he3RpbWVzdGFtcDpNTX0vZGF5PSF7dGltZXN0YW1wOmRkfS8nLFxuICAgICAgICBidWZmZXJpbmdIaW50czoge1xuICAgICAgICAgIHNpemVJbk1CczogTWF0aC5tYXgocHJvcHMuZmlyZWhvc2VCdWZmZXJTaXplTUIsIDY0KSwgLy8gTWluaW11bSA2NCBNQiB3aGVuIGRhdGEgZm9ybWF0IGNvbnZlcnNpb24gaXMgZW5hYmxlZFxuICAgICAgICAgIGludGVydmFsSW5TZWNvbmRzOiBwcm9wcy5maXJlaG9zZUJ1ZmZlckludGVydmFsU2Vjb25kcyxcbiAgICAgICAgfSxcbiAgICAgICAgY29tcHJlc3Npb25Gb3JtYXQ6ICdVTkNPTVBSRVNTRUQnLFxuICAgICAgICBkYXRhRm9ybWF0Q29udmVyc2lvbkNvbmZpZ3VyYXRpb246IHtcbiAgICAgICAgICBlbmFibGVkOiB0cnVlLFxuICAgICAgICAgIHNjaGVtYUNvbmZpZ3VyYXRpb246IHtcbiAgICAgICAgICAgIGRhdGFiYXNlTmFtZTogdGhpcy5nbHVlRGF0YWJhc2UucmVmLFxuICAgICAgICAgICAgdGFibGVOYW1lOiByYXdFdmVudHNUYWJsZU5hbWUsXG4gICAgICAgICAgICByZWdpb246IGNkay5TdGFjay5vZih0aGlzKS5yZWdpb24sXG4gICAgICAgICAgICByb2xlQXJuOiBmaXJlaG9zZVJvbGUucm9sZUFybixcbiAgICAgICAgICB9LFxuICAgICAgICAgIGlucHV0Rm9ybWF0Q29uZmlndXJhdGlvbjoge1xuICAgICAgICAgICAgZGVzZXJpYWxpemVyOiB7XG4gICAgICAgICAgICAgIG9wZW5YSnNvblNlckRlOiB7fSxcbiAgICAgICAgICAgIH0sXG4gICAgICAgICAgfSxcbiAgICAgICAgICBvdXRwdXRGb3JtYXRDb25maWd1cmF0aW9uOiB7XG4gICAgICAgICAgICBzZXJpYWxpemVyOiB7XG4gICAgICAgICAgICAgIHBhcnF1ZXRTZXJEZToge1xuICAgICAgICAgICAgICAgIGNvbXByZXNzaW9uOiAnU05BUFBZJyxcbiAgICAgICAgICAgICAgfSxcbiAgICAgICAgICAgIH0sXG4gICAgICAgICAgfSxcbiAgICAgICAgfSxcbiAgICAgICAgY2xvdWRXYXRjaExvZ2dpbmdPcHRpb25zOiB7XG4gICAgICAgICAgZW5hYmxlZDogdHJ1ZSxcbiAgICAgICAgICBsb2dHcm91cE5hbWU6IGZpcmVob3NlTG9nR3JvdXAubG9nR3JvdXBOYW1lLFxuICAgICAgICAgIGxvZ1N0cmVhbU5hbWU6IGZpcmVob3NlTG9nU3RyZWFtLmxvZ1N0cmVhbU5hbWUsXG4gICAgICAgIH0sXG4gICAgICB9LFxuICAgIH0pO1xuXG4gICAgLy8gR3JhbnQgR2x1ZSBwZXJtaXNzaW9ucyB0byBGaXJlaG9zZSByb2xlXG4gICAgY29uc3QgZ2x1ZVBvbGljeSA9IG5ldyBpYW0uUG9saWN5KHRoaXMsICdGaXJlaG9zZUdsdWVQb2xpY3knLCB7XG4gICAgICBzdGF0ZW1lbnRzOiBbXG4gICAgICAgIG5ldyBpYW0uUG9saWN5U3RhdGVtZW50KHtcbiAgICAgICAgICBhY3Rpb25zOiBbXG4gICAgICAgICAgICAnZ2x1ZTpHZXRUYWJsZScsXG4gICAgICAgICAgICAnZ2x1ZTpHZXRUYWJsZVZlcnNpb24nLFxuICAgICAgICAgICAgJ2dsdWU6R2V0VGFibGVWZXJzaW9ucycsXG4gICAgICAgICAgXSxcbiAgICAgICAgICByZXNvdXJjZXM6IFtcbiAgICAgICAgICAgIGBhcm46YXdzOmdsdWU6JHtjZGsuU3RhY2sub2YodGhpcykucmVnaW9ufToke2Nkay5TdGFjay5vZih0aGlzKS5hY2NvdW50fTpjYXRhbG9nYCxcbiAgICAgICAgICAgIGBhcm46YXdzOmdsdWU6JHtjZGsuU3RhY2sub2YodGhpcykucmVnaW9ufToke2Nkay5TdGFjay5vZih0aGlzKS5hY2NvdW50fTpkYXRhYmFzZS8ke3RoaXMuZ2x1ZURhdGFiYXNlLnJlZn1gLFxuICAgICAgICAgICAgYGFybjphd3M6Z2x1ZToke2Nkay5TdGFjay5vZih0aGlzKS5yZWdpb259OiR7Y2RrLlN0YWNrLm9mKHRoaXMpLmFjY291bnR9OnRhYmxlLyR7dGhpcy5nbHVlRGF0YWJhc2UucmVmfS8qYCxcbiAgICAgICAgICBdLFxuICAgICAgICB9KSxcbiAgICAgIF0sXG4gICAgfSk7XG4gICAgXG4gICAgZ2x1ZVBvbGljeS5hdHRhY2hUb1JvbGUoZmlyZWhvc2VSb2xlKTtcbiAgICBcbiAgICAvLyBFbnN1cmUgZGVsaXZlcnkgc3RyZWFtIGlzIGNyZWF0ZWQgYWZ0ZXIgdGhlIHBvbGljeSBpcyBhdHRhY2hlZCBhbmQgdGFibGUgaXMgY3JlYXRlZFxuICAgIGRlbGl2ZXJ5U3RyZWFtLm5vZGUuYWRkRGVwZW5kZW5jeShnbHVlUG9saWN5KTtcbiAgICBkZWxpdmVyeVN0cmVhbS5ub2RlLmFkZERlcGVuZGVuY3kodGFibGVDcmVhdGlvblRyaWdnZXIpO1xuICAgIFxuICAgIC8vIEdyYW50IFNFUyByb2xlIHBlcm1pc3Npb24gdG8gcHV0IHJlY29yZHMgdG8gRmlyZWhvc2VcbiAgICBzZXNGaXJlaG9zZVJvbGUuYWRkVG9Qb2xpY3koXG4gICAgICBuZXcgaWFtLlBvbGljeVN0YXRlbWVudCh7XG4gICAgICAgIGFjdGlvbnM6IFsnZmlyZWhvc2U6UHV0UmVjb3JkJywgJ2ZpcmVob3NlOlB1dFJlY29yZEJhdGNoJywgJ2ZpcmVob3NlOkRlc2NyaWJlRGVsaXZlcnlTdHJlYW0nXSxcbiAgICAgICAgcmVzb3VyY2VzOiBbZGVsaXZlcnlTdHJlYW0uYXR0ckFybl0sXG4gICAgICB9KSxcbiAgICApO1xuICAgIFxuICAgIC8vIFN1cHByZXNzIENESyBOYWcgd2FybmluZ3MgZm9yIEZpcmVob3NlIHJvbGUgR2x1ZSBwZXJtaXNzaW9uc1xuICAgIE5hZ1N1cHByZXNzaW9ucy5hZGRSZXNvdXJjZVN1cHByZXNzaW9ucyhcbiAgICAgIGZpcmVob3NlUm9sZSxcbiAgICAgIFtcbiAgICAgICAge1xuICAgICAgICAgIGlkOiAnQXdzU29sdXRpb25zLUlBTTUnLFxuICAgICAgICAgIHJlYXNvbjogJ0dsdWUgY2F0YWxvZyBvcGVyYXRpb25zIHJlcXVpcmUgd2lsZGNhcmQgcGVybWlzc2lvbnMgZm9yIHRhYmxlIGFjY2VzcyB3aXRoaW4gdGhlIGRhdGFiYXNlLiBUaGlzIGFsbG93cyBGaXJlaG9zZSB0byBhY2Nlc3MgYWxsIHRhYmxlcyBmb3Igc2NoZW1hIGNvbnZlcnNpb24gZHVyaW5nIFBhcnF1ZXQgdHJhbnNmb3JtYXRpb24uJyxcbiAgICAgICAgICBhcHBsaWVzVG86IFtcbiAgICAgICAgICAgIGBSZXNvdXJjZTo6YXJuOmF3czpnbHVlOiR7Y2RrLlN0YWNrLm9mKHRoaXMpLnJlZ2lvbn06JHtjZGsuU3RhY2sub2YodGhpcykuYWNjb3VudH06dGFibGUvPFNlc0V2ZW50RGF0YWJhc2U+LypgXG4gICAgICAgICAgXVxuICAgICAgICB9XG4gICAgICBdLFxuICAgICAgdHJ1ZSAvLyBhcHBseVRvQ2hpbGRyZW5cbiAgICApO1xuXG4gICAgLy8vLy8vLy8gU0VTIEVWRU5UIERFU1RJTkFUSU9OIC8vLy8vLy8vXG4gICAgXG4gICAgY29uc3Qgc2VzRXZlbnREZXN0aW5hdGlvbiA9IG5ldyBzZXMuQ2ZuQ29uZmlndXJhdGlvblNldEV2ZW50RGVzdGluYXRpb24odGhpcywgJ1Nlc0V2ZW50RGVzdGluYXRpb24nLCB7XG4gICAgICBjb25maWd1cmF0aW9uU2V0TmFtZTogY29uZmlnU2V0TmFtZSxcbiAgICAgIGV2ZW50RGVzdGluYXRpb246IHtcbiAgICAgICAgbmFtZTogJ2ZpcmVob3NlLWRlc3RpbmF0aW9uJyxcbiAgICAgICAgZW5hYmxlZDogdHJ1ZSxcbiAgICAgICAgbWF0Y2hpbmdFdmVudFR5cGVzOiBbJ3NlbmQnLCAncmVqZWN0JywgJ2JvdW5jZScsICdjb21wbGFpbnQnLCAnZGVsaXZlcnknLCAnb3BlbicsICdjbGljaycsICdyZW5kZXJpbmdGYWlsdXJlJ10sXG4gICAgICAgIGtpbmVzaXNGaXJlaG9zZURlc3RpbmF0aW9uOiB7XG4gICAgICAgICAgZGVsaXZlcnlTdHJlYW1Bcm46IGRlbGl2ZXJ5U3RyZWFtLmF0dHJBcm4sXG4gICAgICAgICAgaWFtUm9sZUFybjogc2VzRmlyZWhvc2VSb2xlLnJvbGVBcm4sXG4gICAgICAgIH0sXG4gICAgICB9LFxuICAgIH0pO1xuICAgIFxuICAgIC8vIEVuc3VyZSBTRVMgZXZlbnQgZGVzdGluYXRpb24gaXMgY3JlYXRlZCBhZnRlciBhbGwgcG9saWNpZXMgYXJlIGF0dGFjaGVkXG4gICAgc2VzRXZlbnREZXN0aW5hdGlvbi5ub2RlLmFkZERlcGVuZGVuY3koZGVsaXZlcnlTdHJlYW0pO1xuICAgIHNlc0V2ZW50RGVzdGluYXRpb24ubm9kZS5hZGREZXBlbmRlbmN5KHNlc0ZpcmVob3NlUm9sZSk7XG5cbiAgICAvLy8vLy8vLyBTTlMgVE9QSUMgRk9SIE5PVElGSUNBVElPTlMgLy8vLy8vLy9cbiAgICBcbiAgICBsZXQgbm90aWZpY2F0aW9uVG9waWM6IHNucy5Ub3BpYyB8IHVuZGVmaW5lZDtcbiAgICBcbiAgICBpZiAocHJvcHMuZW5hYmxlTm90aWZpY2F0aW9ucykge1xuICAgICAgbm90aWZpY2F0aW9uVG9waWMgPSBuZXcgc25zLlRvcGljKHRoaXMsICdBbmFseXRpY3NOb3RpZmljYXRpb25Ub3BpYycsIHtcbiAgICAgICAgZGlzcGxheU5hbWU6ICdTRVMgQ2FtcGFpZ24gQW5hbHl0aWNzIE5vdGlmaWNhdGlvbnMnLFxuICAgICAgfSk7XG5cbiAgICAgIC8vIEVuZm9yY2UgU1NMXG4gICAgICBub3RpZmljYXRpb25Ub3BpYy5hZGRUb1Jlc291cmNlUG9saWN5KFxuICAgICAgICBuZXcgaWFtLlBvbGljeVN0YXRlbWVudCh7XG4gICAgICAgICAgZWZmZWN0OiBpYW0uRWZmZWN0LkRFTlksXG4gICAgICAgICAgYWN0aW9uczogWydzbnM6UHVibGlzaCddLFxuICAgICAgICAgIHJlc291cmNlczogW25vdGlmaWNhdGlvblRvcGljLnRvcGljQXJuXSxcbiAgICAgICAgICBwcmluY2lwYWxzOiBbbmV3IGlhbS5BbnlQcmluY2lwYWwoKV0sXG4gICAgICAgICAgY29uZGl0aW9uczogeyBCb29sOiB7ICdhd3M6U2VjdXJlVHJhbnNwb3J0JzogJ2ZhbHNlJyB9IH0sXG4gICAgICAgIH0pLFxuICAgICAgKTtcblxuICAgICAgaWYgKHByb3BzLm5vdGlmaWNhdGlvbkVtYWlsKSB7XG4gICAgICAgIG5vdGlmaWNhdGlvblRvcGljLmFkZFN1YnNjcmlwdGlvbihcbiAgICAgICAgICBuZXcgc3Vic2NyaXB0aW9ucy5FbWFpbFN1YnNjcmlwdGlvbihwcm9wcy5ub3RpZmljYXRpb25FbWFpbCksXG4gICAgICAgICk7XG4gICAgICB9XG4gICAgfVxuXG4gICAgLy8vLy8vLy8gTEFNQkRBIEZPUiBNQVRFUklBTElaRUQgVklFVyBSRUZSRVNIIC8vLy8vLy8vXG4gICAgXG4gICAgY29uc3QgcmVmcmVzaExhbWJkYSA9IG5ldyBsYW1iZGEuRnVuY3Rpb24odGhpcywgJ01hdGVyaWFsaXplZFZpZXdSZWZyZXNoJywge1xuICAgICAgcnVudGltZTogbGFtYmRhLlJ1bnRpbWUuUFlUSE9OXzNfMTIsXG4gICAgICBoYW5kbGVyOiAnbWF0ZXJpYWxpemVkVmlld1JlZnJlc2guaGFuZGxlcicsXG4gICAgICBjb2RlOiBsYW1iZGEuQ29kZS5mcm9tQXNzZXQocGF0aC5qb2luKF9fZGlybmFtZSwgJy4uL2xhbWJkYScpKSxcbiAgICAgIHRpbWVvdXQ6IER1cmF0aW9uLm1pbnV0ZXMocHJvcHMubGFtYmRhVGltZW91dE1pbnV0ZXMpLFxuICAgICAgbWVtb3J5U2l6ZTogcHJvcHMubGFtYmRhTWVtb3J5TUIsXG4gICAgICBsb2dSZXRlbnRpb246IGxvZ3MuUmV0ZW50aW9uRGF5cy5PTkVfV0VFSyxcbiAgICAgIGVudmlyb25tZW50OiB7XG4gICAgICAgIERBVEFCQVNFX05BTUU6IHRoaXMuZ2x1ZURhdGFiYXNlLnJlZixcbiAgICAgICAgUkFXX0VWRU5UU19UQUJMRTogcmF3RXZlbnRzVGFibGVOYW1lLFxuICAgICAgICBDQU1QQUlHTl9NRVRSSUNTX1RBQkxFOiBjYW1wYWlnbk1ldHJpY3NUYWJsZS5yZWYsXG4gICAgICAgIFBST0NFU1NFRF9CVUNLRVQ6IHRoaXMucHJvY2Vzc2VkRGF0YUJ1Y2tldC5idWNrZXROYW1lLFxuICAgICAgICBBVEhFTkFfT1VUUFVUX0xPQ0FUSU9OOiBgczM6Ly8ke3RoaXMuYXRoZW5hUmVzdWx0c0J1Y2tldC5idWNrZXROYW1lfS9yZWZyZXNoLXJlc3VsdHMvYCxcbiAgICAgICAgV09SS0dST1VQX05BTUU6IHRoaXMud29ya0dyb3VwLnJlZixcbiAgICAgICAgTk9USUZJQ0FUSU9OX1RPUElDX0FSTjogbm90aWZpY2F0aW9uVG9waWM/LnRvcGljQXJuIHx8ICcnLFxuICAgICAgICBSRUZSRVNIX0xPT0tCQUNLX0RBWVM6IFN0cmluZyhwcm9wcy5yZWZyZXNoTG9va2JhY2tEYXlzKSxcbiAgICAgIH0sXG4gICAgfSk7XG5cbiAgICAvLyBHcmFudCBwZXJtaXNzaW9ucyB0byByZWZyZXNoIExhbWJkYVxuICAgIHRoaXMucHJvY2Vzc2VkRGF0YUJ1Y2tldC5ncmFudFJlYWRXcml0ZShyZWZyZXNoTGFtYmRhKTtcbiAgICB0aGlzLnJhd0RhdGFCdWNrZXQuZ3JhbnRSZWFkKHJlZnJlc2hMYW1iZGEpO1xuICAgIHRoaXMuYXRoZW5hUmVzdWx0c0J1Y2tldC5ncmFudFJlYWRXcml0ZShyZWZyZXNoTGFtYmRhKTtcblxuICAgIHJlZnJlc2hMYW1iZGEuYWRkVG9Sb2xlUG9saWN5KFxuICAgICAgbmV3IGlhbS5Qb2xpY3lTdGF0ZW1lbnQoe1xuICAgICAgICBhY3Rpb25zOiBbXG4gICAgICAgICAgJ2F0aGVuYTpTdGFydFF1ZXJ5RXhlY3V0aW9uJyxcbiAgICAgICAgICAnYXRoZW5hOkdldFF1ZXJ5RXhlY3V0aW9uJyxcbiAgICAgICAgICAnYXRoZW5hOkdldFF1ZXJ5UmVzdWx0cycsXG4gICAgICAgICAgJ2F0aGVuYTpTdG9wUXVlcnlFeGVjdXRpb24nLFxuICAgICAgICBdLFxuICAgICAgICByZXNvdXJjZXM6IFtcbiAgICAgICAgICBgYXJuOmF3czphdGhlbmE6JHtjZGsuU3RhY2sub2YodGhpcykucmVnaW9ufToke2Nkay5TdGFjay5vZih0aGlzKS5hY2NvdW50fTp3b3JrZ3JvdXAvJHt0aGlzLndvcmtHcm91cC5yZWZ9YCxcbiAgICAgICAgXSxcbiAgICAgIH0pLFxuICAgICk7XG5cbiAgICByZWZyZXNoTGFtYmRhLmFkZFRvUm9sZVBvbGljeShcbiAgICAgIG5ldyBpYW0uUG9saWN5U3RhdGVtZW50KHtcbiAgICAgICAgYWN0aW9uczogW1xuICAgICAgICAgICdnbHVlOkdldERhdGFiYXNlJyxcbiAgICAgICAgICAnZ2x1ZTpHZXRUYWJsZScsXG4gICAgICAgICAgJ2dsdWU6R2V0UGFydGl0aW9ucycsXG4gICAgICAgICAgJ2dsdWU6Q3JlYXRlUGFydGl0aW9uJyxcbiAgICAgICAgICAnZ2x1ZTpVcGRhdGVQYXJ0aXRpb24nLFxuICAgICAgICAgICdnbHVlOkRlbGV0ZVBhcnRpdGlvbicsXG4gICAgICAgICAgJ2dsdWU6QmF0Y2hDcmVhdGVQYXJ0aXRpb24nLFxuICAgICAgICAgICdnbHVlOkJhdGNoRGVsZXRlUGFydGl0aW9uJyxcbiAgICAgICAgXSxcbiAgICAgICAgcmVzb3VyY2VzOiBbXG4gICAgICAgICAgYGFybjphd3M6Z2x1ZToke2Nkay5TdGFjay5vZih0aGlzKS5yZWdpb259OiR7Y2RrLlN0YWNrLm9mKHRoaXMpLmFjY291bnR9OmNhdGFsb2dgLFxuICAgICAgICAgIGBhcm46YXdzOmdsdWU6JHtjZGsuU3RhY2sub2YodGhpcykucmVnaW9ufToke2Nkay5TdGFjay5vZih0aGlzKS5hY2NvdW50fTpkYXRhYmFzZS8ke3RoaXMuZ2x1ZURhdGFiYXNlLnJlZn1gLFxuICAgICAgICAgIGBhcm46YXdzOmdsdWU6JHtjZGsuU3RhY2sub2YodGhpcykucmVnaW9ufToke2Nkay5TdGFjay5vZih0aGlzKS5hY2NvdW50fTp0YWJsZS8ke3RoaXMuZ2x1ZURhdGFiYXNlLnJlZn0vKmAsXG4gICAgICAgIF0sXG4gICAgICB9KSxcbiAgICApO1xuXG4gICAgaWYgKG5vdGlmaWNhdGlvblRvcGljKSB7XG4gICAgICBub3RpZmljYXRpb25Ub3BpYy5ncmFudFB1Ymxpc2gocmVmcmVzaExhbWJkYSk7XG4gICAgfVxuXG4gICAgLy8vLy8vLy8gTEFNQkRBIEZPUiBBVEhFTkEgUEFSVElUSU9OIE1BTkFHRU1FTlQgLy8vLy8vLy9cbiAgICBcbiAgICAvLyBMYW1iZGEgdG8gYXV0b21hdGljYWxseSBhZGQgcGFydGl0aW9ucyB3aGVuIG5ldyBkYXRhIGFycml2ZXMgaW4gUzNcbiAgICBjb25zdCBhdGhlbmFQYXJ0aXRpb25MYW1iZGEgPSBuZXcgbGFtYmRhLkZ1bmN0aW9uKHRoaXMsICdBdGhlbmFQYXJ0aXRpb25MYW1iZGEnLCB7XG4gICAgICBydW50aW1lOiBsYW1iZGEuUnVudGltZS5QWVRIT05fM18xMixcbiAgICAgIGhhbmRsZXI6ICdwYXJ0aXRpb25NYW5hZ2VyLmhhbmRsZXInLFxuICAgICAgY29kZTogbGFtYmRhLkNvZGUuZnJvbUFzc2V0KHBhdGguam9pbihfX2Rpcm5hbWUsICcuLi9sYW1iZGEnKSksXG4gICAgICB0aW1lb3V0OiBEdXJhdGlvbi5taW51dGVzKDUpLFxuICAgICAgbWVtb3J5U2l6ZTogMjU2LFxuICAgICAgZW52aXJvbm1lbnQ6IHtcbiAgICAgICAgREFUQUJBU0VfTkFNRTogdGhpcy5nbHVlRGF0YWJhc2UucmVmLFxuICAgICAgICBUQUJMRV9OQU1FOiByYXdFdmVudHNUYWJsZU5hbWUsXG4gICAgICB9LFxuICAgIH0pO1xuXG4gICAgLy8gR3JhbnQgcGVybWlzc2lvbnMgdG8gcGFydGl0aW9uIExhbWJkYSAocGFydGl0aW9ucyBhcmUgY3JlYXRlZCB0aHJvdWdoIHRoZSBHbHVlIEFQSSlcbiAgICBhdGhlbmFQYXJ0aXRpb25MYW1iZGEuYWRkVG9Sb2xlUG9saWN5KFxuICAgICAgbmV3IGlhbS5Qb2xpY3lTdGF0ZW1lbnQoe1xuICAgICAgICBhY3Rpb25zOiBbXG4gICAgICAgICAgJ2dsdWU6R2V0RGF0YWJhc2UnLFxuICAgICAgICAgICdnbHVlOkdldFRhYmxlJyxcbiAgICAgICAgICAnZ2x1ZTpDcmVhdGVQYXJ0aXRpb24nLFxuICAgICAgICAgICdnbHVlOkJhdGNoQ3JlYXRlUGFydGl0aW9uJyxcbiAgICAgICAgXSxcbiAgICAgICAgcmVzb3VyY2VzOiBbXG4gICAgICAgICAgYGFybjphd3M6Z2x1ZToke2Nkay5TdGFjay5vZih0aGlzKS5yZWdpb259OiR7Y2RrLlN0YWNrLm9mKHRoaXMpLmFjY291bnR9OmNhdGFsb2dgLFxuICAgICAgICAgIGBhcm46YXdzOmdsdWU6JHtjZGsuU3RhY2sub2YodGhpcykucmVnaW9ufToke2Nkay5TdGFjay5vZih0aGlzKS5hY2NvdW50fTpkYXRhYmFzZS8ke3RoaXMuZ2x1ZURhdGFiYXNlLnJlZn1gLFxuICAgICAgICAgIGBhcm46YXdzOmdsdWU6JHtjZGsuU3RhY2sub2YodGhpcykucmVnaW9ufToke2Nkay5TdGFjay5vZih0aGlzKS5hY2NvdW50fTp0YWJsZS8ke3RoaXMuZ2x1ZURhdGFiYXNlLnJlZn0vKmAsXG4gICAgICAgIF0sXG4gICAgICB9KSxcbiAgICApO1xuXG4gICAgLy8gR3JhbnQgUzMgcGVybWlzc2lvbiB0byBpbnZva2UgTGFtYmRhXG4gICAgYXRoZW5hUGFydGl0aW9uTGFtYmRhLmFkZFBlcm1pc3Npb24oJ1MzSW52b2tlTGFtYmRhJywge1xuICAgICAgcHJpbmNpcGFsOiBuZXcgaWFtLlNlcnZpY2VQcmluY2lwYWwoJ3MzLmFtYXpvbmF3cy5jb20nKSxcbiAgICAgIGFjdGlvbjogJ2xhbWJkYTpJbnZva2VGdW5jdGlvbicsXG4gICAgICBzb3VyY2VBY2NvdW50OiBjZGsuU3RhY2sub2YodGhpcykuYWNjb3VudCxcbiAgICAgIHNvdXJjZUFybjogdGhpcy5yYXdEYXRhQnVja2V0LmJ1Y2tldEFybixcbiAgICB9KTtcblxuICAgIC8vIEFkZCBTMyBub3RpZmljYXRpb24gdG8gdHJpZ2dlciBMYW1iZGEgd2hlbiBuZXcgb2JqZWN0cyBhcmUgY3JlYXRlZFxuICAgIHRoaXMucmF3RGF0YUJ1Y2tldC5hZGRFdmVudE5vdGlmaWNhdGlvbihcbiAgICAgIHMzLkV2ZW50VHlwZS5PQkpFQ1RfQ1JFQVRFRCxcbiAgICAgIG5ldyBzM05vdGlmaWNhdGlvbnMuTGFtYmRhRGVzdGluYXRpb24oYXRoZW5hUGFydGl0aW9uTGFtYmRhKSxcbiAgICAgIHsgcHJlZml4OiAnZXZlbnRzLycgfVxuICAgICk7XG5cbiAgICAvLy8vLy8vLyBEWU5BTU9EQiBUQUJMRSBGT1IgQ0FNUEFJR04gTUVUQURBVEEgLy8vLy8vLy9cbiAgICBcbiAgICBjb25zdCBjYW1wYWlnbk1ldGFkYXRhVGFibGUgPSBuZXcgZHluYW1vZGIuVGFibGUodGhpcywgJ0NhbXBhaWduTWV0YWRhdGFUYWJsZScsIHtcbiAgICAgIC8vIExldCBDREsgZ2VuZXJhdGUgdW5pcXVlIHRhYmxlIG5hbWUgdG8gYXZvaWQgY29uZmxpY3RzIG9uIHJlZGVwbG95XG4gICAgICBwYXJ0aXRpb25LZXk6IHsgbmFtZTogJ2NhbXBhaWduX2lkJywgdHlwZTogZHluYW1vZGIuQXR0cmlidXRlVHlwZS5TVFJJTkcgfSxcbiAgICAgIGJpbGxpbmdNb2RlOiBkeW5hbW9kYi5CaWxsaW5nTW9kZS5QQVlfUEVSX1JFUVVFU1QsXG4gICAgICBlbmNyeXB0aW9uOiBkeW5hbW9kYi5UYWJsZUVuY3J5cHRpb24uQVdTX01BTkFHRUQsXG4gICAgICBwb2ludEluVGltZVJlY292ZXJ5OiB0cnVlLFxuICAgICAgcmVtb3ZhbFBvbGljeTogUmVtb3ZhbFBvbGljeS5ERVNUUk9ZLCAvLyBDaGFuZ2VkIHRvIERFU1RST1kgZm9yIGVhc2llciByZWRlcGxveW1lbnRcbiAgICAgIHRpbWVUb0xpdmVBdHRyaWJ1dGU6ICd0dGwnLFxuICAgIH0pO1xuXG4gICAgLy8gUmVmcmVzaCBMYW1iZGEga2VlcHMgYSBsb2NrIGl0ZW0gaW4gdGhpcyB0YWJsZSBzbyBvdmVybGFwcGluZyByZWZyZXNoZXMgZG9uJ3QgZG91YmxlLWNvdW50XG4gICAgY2FtcGFpZ25NZXRhZGF0YVRhYmxlLmdyYW50UmVhZFdyaXRlRGF0YShyZWZyZXNoTGFtYmRhKTtcbiAgICByZWZyZXNoTGFtYmRhLmFkZEVudmlyb25tZW50KCdNRVRBREFUQV9UQUJMRV9OQU1FJywgY2FtcGFpZ25NZXRhZGF0YVRhYmxlLnRhYmxlTmFtZSk7XG5cbiAgICAvLy8vLy8vLyBFVkVOVEJSSURHRSBSVUxFIEZPUiBTQ0hFRFVMRUQgUkVGUkVTSCAvLy8vLy8vL1xuICAgIFxuICAgIGNvbnN0IHJlZnJlc2hSdWxlID0gbmV3IGV2ZW50cy5SdWxlKHRoaXMsICdEYWlseVJlZnJlc2hSdWxlJywge1xuICAgICAgc2NoZWR1bGU6IGV2ZW50cy5TY2hlZHVsZS5leHByZXNzaW9uKHByb3BzLnJlZnJlc2hTY2hlZHVsZUNyb24pLFxuICAgICAgZGVzY3JpcHRpb246ICdUcmlnZ2VyIGRhaWx5IHJlZnJlc2ggb2YgU0VTIGNhbXBhaWduIG1hdGVyaWFsaXplZCB2aWV3cycsXG4gICAgfSk7XG5cbiAgICByZWZyZXNoUnVsZS5hZGRUYXJnZXQobmV3IHRhcmdldHMuTGFtYmRhRnVuY3Rpb24ocmVmcmVzaExhbWJkYSkpO1xuXG4gICAgLy8vLy8vLy8gQUxBUk1TIC8vLy8vLy8vXG5cbiAgICBjb25zdCBmaXJlaG9zZU1ldHJpYyA9IChtZXRyaWNOYW1lOiBzdHJpbmcsIHN0YXRpc3RpYzogc3RyaW5nKSA9PlxuICAgICAgbmV3IGNsb3Vkd2F0Y2guTWV0cmljKHtcbiAgICAgICAgbmFtZXNwYWNlOiAnQVdTL0ZpcmVob3NlJyxcbiAgICAgICAgbWV0cmljTmFtZSxcbiAgICAgICAgZGltZW5zaW9uc01hcDogeyBEZWxpdmVyeVN0cmVhbU5hbWU6IGRlbGl2ZXJ5U3RyZWFtLnJlZiB9LFxuICAgICAgICBzdGF0aXN0aWMsXG4gICAgICAgIHBlcmlvZDogRHVyYXRpb24ubWludXRlcyg1KSxcbiAgICAgIH0pO1xuXG4gICAgY29uc3QgYWxhcm1zID0gW1xuICAgICAgbmV3IGNsb3Vkd2F0Y2guQWxhcm0odGhpcywgJ1JlZnJlc2hFcnJvcnNBbGFybScsIHtcbiAgICAgICAgYWxhcm1EZXNjcmlwdGlvbjogJ01hdGVyaWFsaXplZCB2aWV3IHJlZnJlc2ggZmFpbGVkIC0gY2FtcGFpZ24gbWV0cmljcyBtYXkgYmUgc3RhbGUnLFxuICAgICAgICBtZXRyaWM6IHJlZnJlc2hMYW1iZGEubWV0cmljRXJyb3JzKHsgcGVyaW9kOiBEdXJhdGlvbi5taW51dGVzKDUpIH0pLFxuICAgICAgICB0aHJlc2hvbGQ6IDAsXG4gICAgICAgIGNvbXBhcmlzb25PcGVyYXRvcjogY2xvdWR3YXRjaC5Db21wYXJpc29uT3BlcmF0b3IuR1JFQVRFUl9USEFOX1RIUkVTSE9MRCxcbiAgICAgICAgZXZhbHVhdGlvblBlcmlvZHM6IDEsXG4gICAgICAgIHRyZWF0TWlzc2luZ0RhdGE6IGNsb3Vkd2F0Y2guVHJlYXRNaXNzaW5nRGF0YS5OT1RfQlJFQUNISU5HLFxuICAgICAgfSksXG4gICAgICBuZXcgY2xvdWR3YXRjaC5BbGFybSh0aGlzLCAnUGFydGl0aW9uRXJyb3JzQWxhcm0nLCB7XG4gICAgICAgIGFsYXJtRGVzY3JpcHRpb246ICdSYXcgZXZlbnQgcGFydGl0aW9ucyBjb3VsZCBub3QgYmUgcmVnaXN0ZXJlZCAtIGV2ZW50cyB3aWxsIGJlIG1pc3NpbmcgZnJvbSBhbmFseXRpY3MnLFxuICAgICAgICBtZXRyaWM6IGF0aGVuYVBhcnRpdGlvbkxhbWJkYS5tZXRyaWNFcnJvcnMoeyBwZXJpb2Q6IER1cmF0aW9uLm1pbnV0ZXMoNSkgfSksXG4gICAgICAgIHRocmVzaG9sZDogMCxcbiAgICAgICAgY29tcGFyaXNvbk9wZXJhdG9yOiBjbG91ZHdhdGNoLkNvbXBhcmlzb25PcGVyYXRvci5HUkVBVEVSX1RIQU5fVEhSRVNIT0xELFxuICAgICAgICBldmFsdWF0aW9uUGVyaW9kczogMSxcbiAgICAgICAgdHJlYXRNaXNzaW5nRGF0YTogY2xvdWR3YXRjaC5UcmVhdE1pc3NpbmdEYXRhLk5PVF9CUkVBQ0hJTkcsXG4gICAgICB9KSxcbiAgICAgIG5ldyBjbG91ZHdhdGNoLkFsYXJtKHRoaXMsICdGaXJlaG9zZUNvbnZlcnNpb25GYWlsdXJlc0FsYXJtJywge1xuICAgICAgICBhbGFybURlc2NyaXB0aW9uOiAnU0VTIGV2ZW50cyBmYWlsZWQgUGFycXVldCBjb252ZXJzaW9uIGFuZCB3ZXJlIHdyaXR0ZW4gdG8gdGhlIGVycm9ycy8gcHJlZml4JyxcbiAgICAgICAgbWV0cmljOiBmaXJlaG9zZU1ldHJpYygnRmFpbGVkQ29udmVyc2lvbi5SZWNvcmRzJywgJ1N1bScpLFxuICAgICAgICB0aHJlc2hvbGQ6IDAsXG4gICAgICAgIGNvbXBhcmlzb25PcGVyYXRvcjogY2xvdWR3YXRjaC5Db21wYXJpc29uT3BlcmF0b3IuR1JFQVRFUl9USEFOX1RIUkVTSE9MRCxcbiAgICAgICAgZXZhbHVhdGlvblBlcmlvZHM6IDEsXG4gICAgICAgIHRyZWF0TWlzc2luZ0RhdGE6IGNsb3Vkd2F0Y2guVHJlYXRNaXNzaW5nRGF0YS5OT1RfQlJFQUNISU5HLFxuICAgICAgfSksXG4gICAgICBuZXcgY2xvdWR3YXRjaC5BbGFybSh0aGlzLCAnRmlyZWhvc2VEZWxpdmVyeUZhaWx1cmVzQWxhcm0nLCB7XG4gICAgICAgIGFsYXJtRGVzY3JpcHRpb246ICdGaXJlaG9zZSBmYWlsZWQgdG8gZGVsaXZlciBTRVMgZXZlbnRzIHRvIFMzJyxcbiAgICAgICAgbWV0cmljOiBmaXJlaG9zZU1ldHJpYygnRGVsaXZlcnlUb1MzLlN1Y2Nlc3MnLCAnTWluaW11bScpLFxuICAgICAgICB0aHJlc2hvbGQ6IDEsXG4gICAgICAgIGNvbXBhcmlzb25PcGVyYXRvcjogY2xvdWR3YXRjaC5Db21wYXJpc29uT3BlcmF0b3IuTEVTU19USEFOX1RIUkVTSE9MRCxcbiAgICAgICAgZXZhbHVhdGlvblBlcmlvZHM6IDEsXG4gICAgICAgIHRyZWF0TWlzc2luZ0RhdGE6IGNsb3Vkd2F0Y2guVHJlYXRNaXNzaW5nRGF0YS5OT1RfQlJFQUNISU5HLFxuICAgICAgfSksXG4gICAgXTtcblxuICAgIGlmIChub3RpZmljYXRpb25Ub3BpYykge1xuICAgICAgLy8gVGhlIHRvcGljIHBvbGljeSByZXBsYWNlcyBTTlMncyBkZWZhdWx0IHBvbGljeSwgc28gQ2xvdWRXYXRjaCBuZWVkcyBhbiBleHBsaWNpdCBhbGxvd1xuICAgICAgbm90aWZpY2F0aW9uVG9waWMuYWRkVG9SZXNvdXJjZVBvbGljeShcbiAgICAgICAgbmV3IGlhbS5Qb2xpY3lTdGF0ZW1lbnQoe1xuICAgICAgICAgIGFjdGlvbnM6IFsnc25zOlB1Ymxpc2gnXSxcbiAgICAgICAgICByZXNvdXJjZXM6IFtub3RpZmljYXRpb25Ub3BpYy50b3BpY0Fybl0sXG4gICAgICAgICAgcHJpbmNpcGFsczogW25ldyBpYW0uU2VydmljZVByaW5jaXBhbCgnY2xvdWR3YXRjaC5hbWF6b25hd3MuY29tJyldLFxuICAgICAgICAgIGNvbmRpdGlvbnM6IHtcbiAgICAgICAgICAgIEFybkxpa2U6IHsgJ2F3czpTb3VyY2VBcm4nOiBgYXJuOmF3czpjbG91ZHdhdGNoOiR7Y2RrLlN0YWNrLm9mKHRoaXMpLnJlZ2lvbn06JHtjZGsuU3RhY2sub2YodGhpcykuYWNjb3VudH06YWxhcm06KmAgfSxcbiAgICAgICAgICB9LFxuICAgICAgICB9KSxcbiAgICAgICk7XG5cbiAgICAgIGZvciAoY29uc3QgYWxhcm0gb2YgYWxhcm1zKSB7XG4gICAgICAgIGFsYXJtLmFkZEFsYXJtQWN0aW9uKG5ldyBjbG91ZHdhdGNoQWN0aW9ucy5TbnNBY3Rpb24obm90aWZpY2F0aW9uVG9waWMpKTtcbiAgICAgIH1cbiAgICB9XG5cbiAgICAvLy8vLy8vLyBPVVRQVVRTIC8vLy8vLy8vXG4gICAgXG4gICAgbmV3IENmbk91dHB1dCh0aGlzLCAnU2VzQ29uZmlndXJhdGlvblNldE5hbWUnLCB7XG4gICAgICB2YWx1ZTogY29uZmlnU2V0TmFtZSxcbiAgICAgIGRlc2NyaXB0aW9uOiAnU0VTIENvbmZpZ3VyYXRpb24gU2V0IG5hbWUgLSB1c2UgdGhpcyB3aGVuIHNlbmRpbmcgZW1haWxzJyxcbiAgICB9KTtcblxuICAgIG5ldyBDZm5PdXRwdXQodGhpcywgJ1Jhd0RhdGFCdWNrZXROYW1lJywge1xuICAgICAgdmFsdWU6IHRoaXMucmF3RGF0YUJ1Y2tldC5idWNrZXROYW1lLFxuICAgICAgZGVzY3JpcHRpb246ICdTMyBidWNrZXQgZm9yIHJhdyBTRVMgZXZlbnRzJyxcbiAgICB9KTtcblxuICAgIG5ldyBDZm5PdXRwdXQodGhpcywgJ1Byb2Nlc3NlZERhdGFCdWNrZXROYW1lJywge1xuICAgICAgdmFsdWU6IHRoaXMucHJvY2Vzc2VkRGF0YUJ1Y2tldC5idWNrZXROYW1lLFxuICAgICAgZGVzY3JpcHRpb246ICdTMyBidWNrZXQgZm9yIHByb2Nlc3NlZCBkYXRhIGFuZCBtYXRlcmlhbGl6ZWQgdmlld3MnLFxuICAgIH0pO1xuXG4gICAgbmV3IENmbk91dHB1dCh0aGlzLCAnQXRoZW5hUmVzdWx0c0J1Y2tldE5hbWUnLCB7XG4gICAgICB2YWx1ZTogdGhpcy5hdGhlbmFSZXN1bHRzQnVja2V0LmJ1Y2tldE5hbWUsXG4gICAgICBkZXNjcmlwdGlvbjogJ1MzIGJ1Y2tldCBmb3IgQXRoZW5hIHF1ZXJ5IHJlc3VsdHMnLFxuICAgIH0pO1xuXG4gICAgbmV3IENmbk91dHB1dCh0aGlzLCAnR2x1ZURhdGFiYXNlTmFtZScsIHtcbiAgICAgIHZhbHVlOiB0aGlzLmdsdWVEYXRhYmFzZS5yZWYsXG4gICAgICBkZXNjcmlwdGlvbjogJ0dsdWUgZGF0YWJhc2UgbmFtZSBmb3IgU0VTIGFuYWx5dGljcycsXG4gICAgfSk7XG5cbiAgICBuZXcgQ2ZuT3V0cHV0KHRoaXMsICdBdGhlbmFXb3JrR3JvdXBOYW1lJywge1xuICAgICAgdmFsdWU6IHRoaXMud29ya0dyb3VwLnJlZixcbiAgICAgIGRlc2NyaXB0aW9uOiAnQXRoZW5hIHdvcmtncm91cCBmb3IgY2FtcGFpZ24gYW5hbHl0aWNzIHF1ZXJpZXMnLFxuICAgIH0pO1xuXG4gICAgbmV3IENmbk91dHB1dCh0aGlzLCAnRmlyZWhvc2VEZWxpdmVyeVN0cmVhbU5hbWUnLCB7XG4gICAgICB2YWx1ZTogZGVsaXZlcnlTdHJlYW0ucmVmLFxuICAgICAgZGVzY3JpcHRpb246ICdLaW5lc2lzIEZpcmVob3NlIGRlbGl2ZXJ5IHN0cmVhbSBuYW1lJyxcbiAgICB9KTtcblxuICAgIG5ldyBDZm5PdXRwdXQodGhpcywgJ0ZpcmVob3NlRGVsaXZlcnlTdHJlYW1Bcm4nLCB7XG4gICAgICB2YWx1ZTogZGVsaXZlcnlTdHJlYW0uYXR0ckFybixcbiAgICAgIGRlc2NyaXB0aW9uOiAnS2luZXNpcyBGaXJlaG9zZSBkZWxpdmVyeSBzdHJlYW0gQVJOJyxcbiAgICB9KTtcblxuICAgIG5ldyBDZm5PdXRwdXQodGhpcywgJ1JlZnJlc2hMYW1iZGFOYW1lJywge1xuICAgICAgdmFsdWU6IHJlZnJlc2hMYW1iZGEuZnVuY3Rpb25OYW1lLFxuICAgICAgZGVzY3JpcHRpb246ICdMYW1iZGEgZnVuY3Rpb24gZm9yIG1hdGVyaWFsaXplZCB2aWV3IHJlZnJlc2gnLFxuICAgIH0pO1xuXG4gICAgbmV3IENmbk91dHB1dCh0aGlzLCAnQ2FtcGFpZ25NZXRhZGF0YVRhYmxlTmFtZScsIHtcbiAgICAgIHZhbHVlOiBjYW1wYWlnbk1ldGFkYXRhVGFibGUudGFibGVOYW1lLFxuICAgICAgZGVzY3JpcHRpb246ICdEeW5hbW9EQiB0YWJsZSBmb3IgY2FtcGFpZ24gbWV0YWRhdGEgc3RvcmFnZScsXG4gICAgfSk7XG5cbiAgICBpZiAobm90aWZpY2F0aW9uVG9waWMpIHtcbiAgICAgIG5ldyBDZm5PdXRwdXQodGhpcywgJ05vdGlmaWNhdGlvblRvcGljQXJuJywge1xuICAgICAgICB2YWx1ZTogbm90aWZpY2F0aW9uVG9waWMudG9waWNBcm4sXG4gICAgICAgIGRlc2NyaXB0aW9uOiAnU05TIHRvcGljIGZvciBhbmFseXRpY3Mgbm90aWZpY2F0aW9ucycsXG4gICAgICB9KTtcbiAgICB9XG5cbiAgICBuZXcgQ2ZuT3V0cHV0KHRoaXMsICdRdWVyeUV4YW1wbGVDYW1wYWlnblN1bW1hcnknLCB7XG4gICAgICB2YWx1ZTogYFNFTEVDVCAqIEZST00gJHt0aGlzLmdsdWVEYXRhYmFzZS5yZWZ9LmNhbXBhaWduX21ldHJpY3NfZGFpbHkgV0hFUkUgZGF0ZSA+PSBjdXJyZW50X2RhdGUgLSBpbnRlcnZhbCAnMzAnIGRheSBPUkRFUiBCWSBkYXRlIERFU0NgLFxuICAgICAgZGVzY3JpcHRpb246ICdFeGFtcGxlIEF0aGVuYSBxdWVyeSBmb3IgY2FtcGFpZ24gc3VtbWFyeScsXG4gICAgfSk7XG4gIH1cbn1cbiJdfQ==