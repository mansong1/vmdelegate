from jinja2 import FileSystemLoader, Environment
from aws_cdk import (
    CfnOutput,
    Stack,
)
import os
import os.path
import aws_cdk.aws_ec2 as ec2
from constructs import Construct

aws_access_key_id = os.getenv('AWS_ACCESS_KEY_ID')
aws_access_key_secret = os.getenv('AWS_SECRET_ACCESS_KEY')

ec2_type = "t3.medium"
key_name = ""
harness_delegate_name = ""
harness_org_identifier = "default"
harness_project_identifier = ""
harness_account_id = ""
harness_account_secret = ""
linux_instance_type = "t3.medium",
windows_instance_type = "t3.medium",

linux_ami_id = ec2.GenericLinuxImage({
    "us-east-2": "ami-0fb653ca2d3203ac1",
})

windows_ami_id = ec2.GenericWindowsImage({
    "us-east-2": "ami-04d852871ae97b000",
})

delegate_tags = "ubuntu,linux,pool,aws,windows,cdk"

dirname = os.path.dirname(__file__)

with open("assets/init.sh", 'r') as init_script:
    init_script_contents = init_script.read()

templates = Environment(
    autoescape=True,
    loader=FileSystemLoader("templates")
)

env_file = templates.get_template("env.j2").render(
    aws_access_key_id=aws_access_key_id,
    aws_access_key_secret=aws_access_key_secret,
    aws_region=os.getenv('AWS_DEFAULT_REGION'),
    key_name=key_name
)

docker_compose = templates.get_template("docker-compose.yml.j2").render(
    harness_account_id=harness_account_id,
    harness_account_secret=harness_account_secret,
    harness_delegate_name=harness_delegate_name,
    harness_org_identifier=harness_org_identifier,
    harness_project_identifier=harness_project_identifier
)


class VmdelegateStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        vpc = ec2.Vpc(
            self, "harness-vpc",
            cidr="10.0.0.0/16",
            max_azs=2,
            nat_gateways=0,
            enable_dns_hostnames=True,
            enable_dns_support=True,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24
                )
                # ec2.SubnetConfiguration(
                #     name="private",
                #     subnet_type=ec2.SubnetType.PRIVATE_WITH_NAT,
                #     cidr_mask=24
                # )
            ]
        )

        subnet_public = vpc.public_subnets[0]
        security_group = ec2.SecurityGroup(
            self, "SecurityGroup",
            vpc=vpc,
            allow_all_outbound=True,
            description="Allow all outbound traffic",
            security_group_name="VmdelegateSecurityGroup")

        security_group.add_ingress_rule(
            ec2.Peer.ipv4("0.0.0.0/0"),
            connection=ec2.Port.tcp(22), description="Allow SSH")
        security_group.add_ingress_rule(
            ec2.Peer.ipv4("0.0.0.0/0"),
            connection=ec2.Port.tcp(80), description="Allow HTTP")
        security_group.add_ingress_rule(
            ec2.Peer.ipv4("0.0.0.0/0"),
            connection=ec2.Port.tcp(3389), description="Allow RDP")
        security_group.add_ingress_rule(
            ec2.Peer.ipv4("0.0.0.0/0"),
            connection=ec2.Port.tcp(9079), description="Allow port 9079")
        security_group.add_ingress_rule(
            ec2.Peer.ipv4("0.0.0.0/0"),
            connection=ec2.Port.tcp(1), description="Allow ICMP")

        drone_pool = templates.get_template("drone_pool.yml.j2").render(
                aws_region=os.getenv('AWS_DEFAULT_REGION'),
                vpc_id=vpc.vpc_id,
                subnet_id=subnet_public.subnet_id,
                security_group=security_group.security_group_id,
                linux_instance_type=linux_instance_type,
                linux_ami_id=linux_ami_id,
                windows_instance_type=windows_instance_type,
                windows_ami_id=windows_ami_id,
            )

        host = ec2.Instance(
            self, "HarnessDelegate",
            instance_type=ec2.InstanceType(instance_type_identifier=ec2_type),
            instance_name="HarnessDelegate",
            machine_image=linux_ami_id,
            vpc=vpc,
            security_group=security_group,
            key_name=key_name,
            user_data=ec2.UserData.custom(init_script_contents),
            vpc_subnets=subnet_public,
            init=ec2.CloudFormationInit.from_config_sets(
                config_sets={
                    "default": ["aptPreInstall", "config"]
                },
                configs={
                    "apt_preInstall": ec2.InitConfig([
                        ec2.InitPackage.apt("docker.io")]),
                    "config": ec2.InitConfig([
                        ec2.InitGroup.from_name("docker"),

                        ec2.InitFile.from_string("/runner/.env.yml", env_file),
                        ec2.InitFile.from_string("/runner/.drone_pool.yml",drone_pool),
                        ec2.InitFile.from_string("/runner/docker-compose.yml", docker_compose),
                    ])
                }
                ),
            init_options=ec2.ApplyCloudFormationInitOptions(
                config_sets=["default"],
            )
        )

        host.instance.add_property_override("BlockDeviceMappings", [{
            "DeviceName": "/dev/xvda",
            "Ebs": {
                "VolumeSize": "10",
                "VolumeType": "io1",
                "Iops": "150",
                "DeleteOnTermination": "true"
            }
        }, {
            "DeviceName": "/dev/sdb",
            "Ebs": {"VolumeSize": "30"}
        }
        ])  # by default VolumeType is gp2, VolumeSize 8GB

        CfnOutput(self, "Output", value=host.instance_public_ip)
