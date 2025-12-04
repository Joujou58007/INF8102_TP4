import boto3
from botocore.exceptions import ClientError

import json

REGION = "us-east-1"
SOURCE_BUCKET = "polystudent3-py-lab4-try2"
DEST_BUCKET = "polystudent3-py-lab4-try2-back"
REPLICATION_ROLE_ARN = "arn:aws:iam::514390778516:role/s3-replication-role"
KMS_KEY_ARN = "arn:aws:kms:us-east-1:514390778516:key/1ef31c89-9db2-4a42-9942-d7baadb7f099"

s3 = boto3.client('s3', region_name=REGION)

def create_secure_bucket(bucket_name):
    if REGION == "us-east-1":
        s3.create_bucket(Bucket=bucket_name)
    else:
        s3.create_bucket(
            Bucket=bucket_name,
            CreateBucketConfiguration={'LocationConstraint': REGION}
    )

    print(f"Bucket created: {bucket_name}")

    s3.put_public_access_block(
        Bucket=bucket_name,
        PublicAccessBlockConfiguration={
            'BlockPublicAcls': True,
            'IgnorePublicAcls': True,
            'BlockPublicPolicy': True,
            'RestrictPublicBuckets': True
        }
    )
    s3.put_bucket_encryption(
        Bucket=bucket_name,
        ServerSideEncryptionConfiguration={
            'Rules': [
                {
                    'ApplyServerSideEncryptionByDefault': {
                        'SSEAlgorithm': 'aws:kms',
                        'KMSMasterKeyID': KMS_KEY_ARN
                    }
                }
            ]
        }
    )

    s3.put_bucket_versioning(
        Bucket=bucket_name,
        VersioningConfiguration={'Status': 'Enabled'}
    )

    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "DenyUnsecureConnections",
                "Effect": "Deny",
                "Principal": "*",
                "Action": "s3:*",
                "Resource": [
                    f"arn:aws:s3:::{bucket_name}",
                    f"arn:aws:s3:::{bucket_name}/*"
                ],
                "Condition": {
                    "Bool": {"aws:SecureTransport": "false"}
                }
            }
        ]
    }
    s3.put_bucket_policy(Bucket=bucket_name, Policy=json.dumps(policy))
    print("S3 BUCKET FULLY CONFIGURED")

create_secure_bucket(SOURCE_BUCKET)
create_secure_bucket(DEST_BUCKET)



iam = boto3.client('iam')
s3 = boto3.client('s3', region_name=REGION)
ec2 = boto3.client('ec2', region_name=REGION)
cloudtrail = boto3.client('cloudtrail', region_name=REGION)

# trouver role lab2role
try:
    role = iam.get_role(RoleName='lab2role')
    role_arn = role['Role']['Arn']
    print(f"Role found: {role_arn}")
except ClientError:
    print("ERROR: Role 'lab2role' not found")
    exit(1)

# creer flow logs
try:
    ec2.create_flow_logs(
        ResourceType='VPC',
        ResourceIds=['vpc-0332b1503520157e4'],
        TrafficType='REJECT',
        LogDestinationType='s3',
        LogDestination=f"arn:aws:s3:::{SOURCE_BUCKET}/vpc-flow-logs/",
    )
except ClientError as e:
    if 'FlowLogAlreadyExists' in str(e):
        print("Flow Logs already exist on this VPC")
    else:
        print(f"Flow Logs error: {e}")
        raise

# bucket versioning
for bucket in [SOURCE_BUCKET, DEST_BUCKET]:
    try:
        s3.put_bucket_versioning(
            Bucket=bucket,
            VersioningConfiguration={"Status": "Enabled"}
        )
        print(f"Versioning enabled on {bucket}")
    except ClientError as e:
        print(f"Error enabling versioning on {bucket}: {e}")
        raise

# replication
replication_config = {
    "Role": REPLICATION_ROLE_ARN,
    "Rules": [
        {
            "ID": "replicate-all",
            "Status": "Enabled",
            "Priority": 1,
            "DeleteMarkerReplication": {"Status": "Enabled"},
            "Filter": {"Prefix": ""},
            "Destination": {
                "Bucket": f"arn:aws:s3:::{DEST_BUCKET}",
                "StorageClass": "STANDARD"
            }
        }
    ]
}
# appliquer replication
try:
    s3.put_bucket_replication(
        Bucket=SOURCE_BUCKET,
        ReplicationConfiguration=replication_config
    )
    print("Replication configured successfully.")
except ClientError as e:
    print(f"Error applying replication: {e}")
    raise

# cloudtrail pour modification 

# étapes: 

# 1. update polices
try:
    # Get existing policy if it exists
    try:
        current_policy = s3.get_bucket_policy(Bucket=SOURCE_BUCKET)['Policy']
        policy = json.loads(current_policy)
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchBucketPolicy':
            policy = {"Version": "2012-10-17", "Statement": []}
        else:
            raise

    # Add CloudTrail statements
    policy['Statement'].extend([
        {
            "Sid": "AWSCloudTrailAclCheck20131101",
            "Effect": "Allow",
            "Principal": {"Service": "cloudtrail.amazonaws.com"},
            "Action": "s3:GetBucketAcl",
            "Resource": f"arn:aws:s3:::{SOURCE_BUCKET}"
        },
        {
            "Sid": "AWSCloudTrailWrite20131101",
            "Effect": "Allow",
            "Principal": {"Service": "cloudtrail.amazonaws.com"},
            "Action": "s3:PutObject",
            "Resource": f"arn:aws:s3:::{SOURCE_BUCKET}/AWSLogs/514390778516/*",
            "Condition": {
                "StringEquals": {"s3:x-amz-acl": "bucket-owner-full-control"}
            }
        }
    ])

    s3.put_bucket_policy(Bucket=SOURCE_BUCKET, Policy=json.dumps(policy))
    print(f"Bucket policy updated for CloudTrail writes on {SOURCE_BUCKET}")
except ClientError as e:
    print(f"Error updating bucket policy: {e}")
    raise

# 2. créer trail
try:
    cloudtrail.create_trail(
        Name='Lab4S3modficationsTrail',
        S3BucketName=SOURCE_BUCKET,
        IsMultiRegionTrail=True,
        IncludeGlobalServiceEvents=True,
        EnableLogFileValidation=True
    )
    print("CloudTrail created successfully.")
except ClientError as e:
    if 'TrailAlreadyExistsException' in str(e):
        print("CloudTrail already exists.")
    else:
        print(f"Error creating CloudTrail: {e}")
        raise

# 3. commencer logging
cloudtrail.start_logging(Name='Lab4S3modficationsTrail')
print(f"Started logging for trail 'Lab4S3modficationsTrail'")

# 4. enable les events de modification et deletion dans le bucket
cloudtrail.put_event_selectors(
    TrailName='Lab4S3modficationsTrail',
    EventSelectors=[
        {
            'ReadWriteType': 'All',
            'IncludeManagementEvents': True,
            'DataResources': [
                {
                    'Type': 'AWS::S3::Object',
                    'Values': [f"arn:aws:s3:::{SOURCE_BUCKET}/"]
                }
            ]
        }
    ]
)