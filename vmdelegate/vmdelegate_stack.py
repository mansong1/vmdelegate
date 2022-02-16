import os
from configparser import ConfigParser
from jinja2 import FileSystemLoader, Environment
from aws_cdk import (
    aws_ec2 as ec2,
    aws_iam as iam,
    CfnOutput,
    Stack,
    Duration
)
from constructs import Construct

cfg = ConfigParser()
cfg.read('config.ini')

aws_config = cfg['AWSINFO']
harness_config = cfg['HARNESSINFO']

linux_ami_id = ec2.GenericLinuxImage({
    "us-east-2": "ami-0fb653ca2d3203ac1",
})

windows_ami_id = ec2.GenericWindowsImage({
    "us-east-2": "ami-04d852871ae97b000",
})

with open("assets/init.sh", 'r') as init_script:
    init_script_contents = init_script.read()

templates = Environment(
    autoescape=True,
    loader=FileSystemLoader("templates")
)

env_file = templates.get_template("env.j2").render(
    key_name=aws_config["key_name"],
    aws_region=os.getenv('AWS_DEFAULT_REGION'),
    aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
    aws_access_key_secret=os.getenv('AWS_SECRET_ACCESS_KEY'),
)

docker_compose = templates.get_template("docker-compose.yml.j2").render(
    delegate_tags=harness_config["harness_delegate_tags"],
    harness_account_id=harness_config["harness_account_id"],
    harness_account_secret=harness_config["harness_account_secret"],
    harness_delegate_name=harness_config["harness_delegate_name"],
    harness_org_identifier=harness_config["harness_org_identifier"],
    harness_project_identifier=harness_config["harness_project_identifier"]
)

class VmdelegateStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # VPC
        vpc = ec2.Vpc(
            self, "VPC",
            nat_gateways=0,
            subnet_configuration=[ec2.SubnetConfiguration(name="public", subnet_type=ec2.SubnetType.PUBLIC)]
        )

        subnet_public = vpc.public_subnets[0]

        # AMI
        amzn_linux = ec2.MachineImage.latest_amazon_linux(
            generation=ec2.AmazonLinuxGeneration.AMAZON_LINUX_2,
            edition=ec2.AmazonLinuxEdition.STANDARD,
            virtualization=ec2.AmazonLinuxVirt.HVM,
            storage=ec2.AmazonLinuxStorage.GENERAL_PURPOSE
        )

        # Instance Role and SSM Managed Policy
        role = iam.Role(self, "InstanceSSM", assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"))

        role.add_managed_policy(iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore"))

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
                linux_ami_id=linux_ami_id,
                windows_ami_id=windows_ami_id,
                linux_pool_instance_type=aws_config["linux_pool_instance_type"],
                windows_pool_instance_type=aws_config["windows_pool_instance_type"],
            )

        instance = ec2.Instance(
            self, "HarnessDelegate",
            instance_type=ec2.InstanceType(instance_type_identifier=harness_config["harness_delegate_instance_type"]),
            instance_name="HarnessDelegate",
            machine_image=amzn_linux,
            vpc=vpc,
            security_group=security_group,
            key_name=aws_config['key_name'],
            user_data=ec2.UserData.custom(init_script_contents),
            init=ec2.CloudFormationInit.from_config_sets(
                config_sets={
                    "default": ["config"]
                },
                configs={
                    "config": ec2.InitConfig([
                        ec2.InitFile.from_string("/runner/.env.yml", env_file, base64_encoded=True),
                        ec2.InitFile.from_string("/runner/.drone_pool.yml", drone_pool, base64_encoded=True),
                        ec2.InitFile.from_string("/runner/docker-compose.yml", docker_compose, base64_encoded=True),
                        ec2.InitGroup('docker'),
                        ec2.InitCommand.shell_command("sudo docker-compose up -d")
                    ])
                }),
            init_options=ec2.ApplyCloudFormationInitOptions(
                config_sets=["default"],
                ignore_failures=False,
                timeout=Duration.minutes(10)
            )
        )

        CfnOutput(self, "Output", value=instance.instance_public_ip)
