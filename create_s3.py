import boto3
from botocore.exceptions import ClientError
import json
import sys

BUCKET_NAME = "polystudent3-py"
KMS_KEY_ARN = "arn:aws:kms:ca-central-1:671081739219:key/4be20024-83a3-4eb7-afce-7efcb439f488"
REGION = "ca-central-1"

s3 = boto3.client('s3', region_name=REGION)

def create_secure_bucket():
    s3.create_bucket(
        Bucket=BUCKET_NAME,
        CreateBucketConfiguration={'LocationConstraint': REGION}
    )
    print(f"Bucket created: {BUCKET_NAME}")

    s3.put_public_access_block(
        Bucket=BUCKET_NAME,
        PublicAccessBlockConfiguration={
            'BlockPublicAcls': True,
            'IgnorePublicAcls': True,
            'BlockPublicPolicy': True,
            'RestrictPublicBuckets': True
        }
    )
    s3.put_bucket_encryption(
        Bucket=BUCKET_NAME,
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
        Bucket=BUCKET_NAME,
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
                    f"arn:aws:s3:::{BUCKET_NAME}",
                    f"arn:aws:s3:::{BUCKET_NAME}/*"
                ],
                "Condition": {
                    "Bool": {"aws:SecureTransport": "false"}
                }
            }
        ]
    }
    s3.put_bucket_policy(Bucket=BUCKET_NAME, Policy=json.dumps(policy))
    print("S3 BUCKET FULLY CONFIGURED")

if __name__ == "__main__":
    create_secure_bucket()