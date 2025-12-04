import boto3
from botocore.exceptions import ClientError

ENV_NAME = "polystudent-vpc-py"
REGION = "us-east-1"
ROLE_NAME = "LabRole"
S3_BUCKET_FOR_FLOWLOGS = "polystudent3-py"

ec2 = boto3.client('ec2', region_name=REGION)
iam = boto3.client('iam')

def wait_for_nat(nat_ids):
    ec2.get_waiter('nat_gateway_available').wait(NatGatewayIds=nat_ids)
    print("NAT Gateways ready")


vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")
vpc_id = vpc['Vpc']['VpcId']
ec2.create_tags(Resources=[vpc_id], Tags=[{'Key': 'Name', 'Value': ENV_NAME}])
ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsSupport={'Value': True})
ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={'Value': True})
print(f"VPC created: {vpc_id}")
ec2.get_waiter('vpc_available').wait(VpcIds=[vpc_id])

azs = [az['ZoneName'] for az in ec2.describe_availability_zones()['AvailabilityZones'][:2]]

subnets = {
    'PublicSubnet1':  ec2.create_subnet(VpcId=vpc_id, CidrBlock="10.0.0.0/24",   AvailabilityZone=azs[0]),
    'PublicSubnet2':  ec2.create_subnet(VpcId=vpc_id, CidrBlock="10.0.16.0/24",  AvailabilityZone=azs[1]),
    'PrivateSubnet1': ec2.create_subnet(VpcId=vpc_id, CidrBlock="10.0.128.0/24", AvailabilityZone=azs[0]),
    'PrivateSubnet2': ec2.create_subnet(VpcId=vpc_id, CidrBlock="10.0.144.0/24", AvailabilityZone=azs[1]),
}

for logical, sub in subnets.items():
    sid = sub['Subnet']['SubnetId']
    is_pub = 'Public' in logical
    az_num = 1 if '1' in logical else 2
    ec2.create_tags(Resources=[sid], Tags=[{
        'Key': 'Name',
        'Value': f"{ENV_NAME} {'Public' if is_pub else 'Private'} Subnet (AZ{az_num})"
    }])
    if is_pub:
        ec2.modify_subnet_attribute(SubnetId=sid, MapPublicIpOnLaunch={'Value': True})
    print(f"{logical}: {sid}")

public1_id  = subnets['PublicSubnet1']['Subnet']['SubnetId']
public2_id  = subnets['PublicSubnet2']['Subnet']['SubnetId']
private1_id = subnets['PrivateSubnet1']['Subnet']['SubnetId']
private2_id = subnets['PrivateSubnet2']['Subnet']['SubnetId']

igw = ec2.create_internet_gateway()
igw_id = igw['InternetGateway']['InternetGatewayId']
ec2.create_tags(Resources=[igw_id], Tags=[{'Key': 'Name', 'Value': ENV_NAME}])
ec2.attach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
print(f"Internet Gateway attached: {igw_id}")

public_rt = ec2.create_route_table(VpcId=vpc_id)['RouteTable']
ec2.create_tags(Resources=[public_rt['RouteTableId']], Tags=[{'Key': 'Name', 'Value': f"{ENV_NAME} Public Routes"}])
ec2.create_route(RouteTableId=public_rt['RouteTableId'], DestinationCidrBlock='0.0.0.0/0', GatewayId=igw_id)
for sid in [public1_id, public2_id]:
    ec2.associate_route_table(RouteTableId=public_rt['RouteTableId'], SubnetId=sid)
print("Public route table configured")

nat_gateways = []
eip_alloc_ids = []

eip1 = ec2.allocate_address(Domain='vpc')
eip2 = ec2.allocate_address(Domain='vpc')
eip_alloc_ids = [eip1['AllocationId'], eip2['AllocationId']]
print("2 EIPs allocated successfully")

for i, alloc_id in enumerate(eip_alloc_ids):
    subnet_id = public1_id if i == 0 else public2_id
    nat = ec2.create_nat_gateway(SubnetId=subnet_id, AllocationId=alloc_id)
    nat_id = nat['NatGateway']['NatGatewayId']
    nat_gateways.append(nat_id)
    print(f"NAT Gateway {i+1} creating in {subnet_id}...")

wait_for_nat(nat_gateways)

nat1_id = nat_gateways[0]
nat2_id = nat_gateways[1] if len(nat_gateways) > 1 else nat1_id

rt1 = ec2.create_route_table(VpcId=vpc_id)['RouteTable']
ec2.create_tags(Resources=[rt1['RouteTableId']], Tags=[{'Key': 'Name', 'Value': f"{ENV_NAME} Private Routes (AZ1)"}])
ec2.create_route(RouteTableId=rt1['RouteTableId'], DestinationCidrBlock='0.0.0.0/0', NatGatewayId=nat1_id)
ec2.associate_route_table(RouteTableId=rt1['RouteTableId'], SubnetId=private1_id)

rt2 = ec2.create_route_table(VpcId=vpc_id)['RouteTable']
ec2.create_tags(Resources=[rt2['RouteTableId']], Tags=[{'Key': 'Name', 'Value': f"{ENV_NAME} Private Routes (AZ2)"}])
ec2.create_route(RouteTableId=rt2['RouteTableId'], DestinationCidrBlock='0.0.0.0/0', NatGatewayId=nat2_id)
ec2.associate_route_table(RouteTableId=rt2['RouteTableId'], SubnetId=private2_id)

print("Private route tables configured")

sg = ec2.create_security_group(
    GroupName="polystudent-sg",
    Description="Security group allows SSH, HTTP, HTTPS, MSSQL, etc...",
    VpcId=vpc_id
)
sg_id = sg['GroupId']

rules = [
    {'IpProtocol': 'tcp', 'FromPort': p, 'ToPort': p, 'IpRanges': [{'Description': 'port', 'CidrIp': '0.0.0.0/0'}]}
    for p in [22, 80, 443, 53, 1433, 5432, 3306, 3389]
] + [
    {'IpProtocol': 'udp', 'FromPort': 53, 'ToPort': 53, 'IpRanges': [{'Description': 'port', 'CidrIp': '0.0.0.0/0'}]},
    {'IpProtocol': 'udp', 'FromPort': 1514, 'ToPort': 1514, 'IpRanges': [{'Description': 'port', 'CidrIp': '0.0.0.0/0'}]},
    {'IpProtocol': 'tcp', 'FromPort': 9200, 'ToPort': 9300, 'IpRanges': [{'Description': 'port', 'CidrIp': '0.0.0.0/0'}]},
]

ec2.authorize_security_group_ingress(GroupId=sg_id, IpPermissions=rules)

# Partie de la question 3
# =======================
print(vpc_id)

vpc_id = "vpc-0991753fb9cb56685"

response = ec2.create_flow_logs(
    ResourceType='VPC',
    ResourceIds=[vpc_id],
    TrafficType='REJECT',
    LogDestinationType='s3',
    LogDestination=f"arn:aws:s3:::{S3_BUCKET_FOR_FLOWLOGS}/vpc-flow-logs/"
)

print(response)
# =======================

print("DEPLOYMENT COMPLETE")
