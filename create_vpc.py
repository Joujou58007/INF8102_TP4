import boto3

ENV_NAME = "polystudent-vpc-py2"
VPC_CIDR = "10.0.0.0/16"
REGION = "us-east-1"

ec2 = boto3.client('ec2', region_name=REGION)

print(f"Starting deployment of {ENV_NAME} in {REGION}...")

vpc = ec2.create_vpc(CidrBlock=VPC_CIDR)
vpc_id = vpc['Vpc']['VpcId']
ec2.create_tags(Resources=[vpc_id], Tags=[{'Key': 'Name', 'Value': ENV_NAME}])
ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsSupport={'Value': True})
ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={'Value': True})
print(f"VPC created: {vpc_id}")

waiter = ec2.get_waiter('vpc_available')
waiter.wait(VpcIds=[vpc_id])

azs = [f"{REGION}a", f"{REGION}b"]

public_subnets = []
private_subnets = []

cidrs = {
    'public1': '10.0.0.0/24',
    'public2': '10.0.16.0/24',
    'private1': '10.0.128.0/24',
    'private2': '10.0.144.0/24'
}

for i, (name, cidr) in enumerate(cidrs.items()):
    az = azs[0] if '1' in name else azs[1]
    is_public = 'public' in name
    
    subnet = ec2.create_subnet(
        VpcId=vpc_id,
        CidrBlock=cidr,
        AvailabilityZone=az
    )
    subnet_id = subnet['Subnet']['SubnetId']
    
    tag_name = f"{ENV_NAME} {'Public' if is_public else 'Private'} Subnet (AZ{i%2 + 1})"
    ec2.create_tags(Resources=[subnet_id], Tags=[{'Key': 'Name', 'Value': tag_name}])
    
    if is_public:
        ec2.modify_subnet_attribute(SubnetId=subnet_id, MapPublicIpOnLaunch={'Value': True})
        public_subnets.append(subnet_id)
    else:
        private_subnets.append(subnet_id)
    
    print(f"Subnet {tag_name}: {subnet_id}")

igw = ec2.create_internet_gateway()
igw_id = igw['InternetGateway']['InternetGatewayId']
ec2.create_tags(Resources=[igw_id], Tags=[{'Key': 'Name', 'Value': ENV_NAME}])
ec2.attach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
print(f"Internet Gateway attached: {igw_id}")

public_rt = ec2.create_route_table(VpcId=vpc_id)
public_rt_id = public_rt['RouteTable']['RouteTableId']
ec2.create_tags(Resources=[public_rt_id], Tags=[{'Key': 'Name', 'Value': f"{ENV_NAME} Public Routes"}])

ec2.create_route(
    RouteTableId=public_rt_id,
    DestinationCidrBlock='0.0.0.0/0',
    GatewayId=igw_id
)
print("Default route → Internet Gateway added to public route table")

for subnet_id in public_subnets:
    ec2.associate_route_table(RouteTableId=public_rt_id, SubnetId=subnet_id)

nat_gateways = []
eips = []

for i, pub_subnet_id in enumerate(public_subnets):
    eip = ec2.allocate_address(Domain='vpc')
    eip_alloc_id = eip['AllocationId']
    eips.append(eip_alloc_id)
    
    nat = ec2.create_nat_gateway(
        SubnetId=pub_subnet_id,
        AllocationId=eip_alloc_id
    )
    nat_id = nat['NatGateway']['NatGatewayId']
    nat_gateways.append(nat_id)
    print(f"NAT Gateway {i+1} creating in {pub_subnet_id}...")

waiter = ec2.get_waiter('nat_gateway_available')
waiter.wait(NatGatewayIds=nat_gateways)
print("Both NAT Gateways are available")

for i, (priv_subnet_id, nat_id) in enumerate(zip(private_subnets, nat_gateways)):
    private_rt = ec2.create_route_table(VpcId=vpc_id)
    private_rt_id = private_rt['RouteTable']['RouteTableId']
    ec2.create_tags(Resources=[private_rt_id],
                    Tags=[{'Key': 'Name', 'Value': f"{ENV_NAME} Private Routes (AZ{i+1})"}])
    
    ec2.create_route(
        RouteTableId=private_rt_id,
        DestinationCidrBlock='0.0.0.0/0',
        NatGatewayId=nat_id
    )
    
    ec2.associate_route_table(RouteTableId=private_rt_id, SubnetId=priv_subnet_id)
    print(f"Private subnet {priv_subnet_id} → NAT {nat_id}")

sg = ec2.create_security_group(
    GroupName="polystudent-sg-py",
    Description="Security group allows SSH, HTTP, HTTPS, MSSQL, etc...",
    VpcId=vpc_id
)
sg_id = sg['GroupId']
ec2.create_tags(Resources=[sg_id], Tags=[{'Key': 'Name', 'Value': 'polystudent-sg-py'}])

rules = [
    {'IpProtocol': 'tcp', 'FromPort': 22,   'ToPort': 22,   'CidrIp': '0.0.0.0/0'},   # SSH
    {'IpProtocol': 'tcp', 'FromPort': 80,   'ToPort': 80,   'CidrIp': '0.0.0.0/0'},   # HTTP
    {'IpProtocol': 'tcp', 'FromPort': 443,  'ToPort': 443,  'CidrIp': '0.0.0.0/0'},   # HTTPS
    {'IpProtocol': 'tcp', 'FromPort': 53,   'ToPort': 53,   'CidrIp': '0.0.0.0/0'},   # DNS TCP
    {'IpProtocol': 'udp', 'FromPort': 53,   'ToPort': 53,   'CidrIp': '0.0.0.0/0'},   # DNS UDP
    {'IpProtocol': 'tcp', 'FromPort': 1433, 'ToPort': 1433, 'CidrIp': '0.0.0.0/0'},   # MSSQL
    {'IpProtocol': 'tcp', 'FromPort': 5432, 'ToPort': 5432, 'CidrIp': '0.0.0.0/0'},   # PostgreSQL
    {'IpProtocol': 'tcp', 'FromPort': 3306, 'ToPort': 3306, 'CidrIp': '0.0.0.0/0'},   # MySQL
    {'IpProtocol': 'tcp', 'FromPort': 3389, 'ToPort': 3389, 'CidrIp': '0.0.0.0/0'},   # RDP
    {'IpProtocol': 'udp', 'FromPort': 1514, 'ToPort': 1514, 'CidrIp': '0.0.0.0/0'},   # OSSEC
    {'IpProtocol': 'tcp', 'FromPort': 9200, 'ToPort': 9300, 'CidrIp': '0.0.0.0/0'},   # Elasticsearch
]

ec2.authorize_security_group_ingress(GroupId=sg_id, IpPermissions=rules)
print(f"Security Group created: {sg_id} with all required ports open")

print("\nDeployment complete!")
print(f"VPC: {vpc_id}")
print(f"Public Subnets : {public_subnets}")
print(f"Private Subnets: {private_subnets}")
print(f"Security Group : {sg_id}")
print(f"NAT Gateways   : {nat_gateways}")
print(f"Internet Gateway: {igw_id}")