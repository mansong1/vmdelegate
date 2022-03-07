# -*- coding: utf-8 -*-
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

linux_image = ec2.MachineImage.latest_amazon_linux(
                generation=ec2.AmazonLinuxGeneration.AMAZON_LINUX_2,
                edition=ec2.AmazonLinuxEdition.STANDARD,
                virtualization=ec2.AmazonLinuxVirt.HVM,
                storage=ec2.AmazonLinuxStorage.GENERAL_PURPOSE)

windows_image = ec2.MachineImage.latest_windows(ec2.WindowsVersion.WINDOWS_SERVER_2019_ENGLISH_CORE_CONTAINERSLATEST)

templates = Environment(
    autoescape=True,
    loader=FileSystemLoader("templates")
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

        env_file = templates.get_template("env.j2").render(
            aws_region=Stack.of(self).region
        )

        # VPC
        vpc = ec2.Vpc(
            self, "VPC",
            nat_gateways=0,
            subnet_configuration=[ec2.SubnetConfiguration(name="public", subnet_type=ec2.SubnetType.PUBLIC)]
        )

        subnet_public = vpc.public_subnets[0]

        # Instance Role and SSM Managed Policy
        role=iam.Role(
            self,
            'InstanceRole',
            assumed_by=iam.ServicePrincipal('ec2.amazonaws.com'),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name('AmazonSSMManagedInstanceCore'),
                iam.ManagedPolicy.from_aws_managed_policy_name('AmazonEC2FullAccess')  # TODO restrict permissions
            ]
        )

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
                aws_region=Stack.of(self).region,
                vpc_id=vpc.vpc_id,
                subnet_id=subnet_public.subnet_id,
                security_group=security_group.security_group_id,
                linux_ami_id=linux_image.get_image(self).image_id,
                windows_ami_id=windows_image.get_image(self).image_id,
                linux_pool_instance_type=aws_config["linux_pool_instance_type"],
                windows_pool_instance_type=aws_config["windows_pool_instance_type"],
            )
        # https://docs.aws.amazon.com/cdk/api/latest/python/aws_cdk.aws_ec2/ApplyCloudFormationInitOptions.html#applycloudformationinitoptions
        # Note, if CloudFormationInit is specified, with config_sets, then config_sets are implicitly activated via ApplyCloudFormationInitOptions
        # There is no need to explicitly call cfn-init with configset in UserData script because it's done implicitly!
        instance = ec2.Instance(
            self, "HarnessDelegate",
            role=role,
            instance_type=ec2.InstanceType(instance_type_identifier=harness_config["harness_delegate_instance_type"]),
            instance_name=harness_config["harness_delegate_name"],
            machine_image=linux_image,
            vpc=vpc,
            security_group=security_group,
            init=ec2.CloudFormationInit.from_config_sets(
                config_sets={
                    "ConfigSet1": ["config_step1", "config_step2"],
                    "ConfigSet2": ["config_step3", "config_step4"],
                },
                configs={
                    "config_step1": ec2.InitConfig([
                        ec2.InitPackage.yum("docker"),
                        ec2.InitPackage.yum("wget"),
                        ec2.InitCommand.shell_command("sudo yum update -y"),
                    ]),
                    "config_step2": ec2.InitConfig([
                        ec2.InitCommand.shell_command("wget https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)"),
                        ec2.InitCommand.shell_command("sudo mv docker-compose-$(uname -s)-$(uname -m) /usr/local/bin/docker-compose"),
                        ec2.InitCommand.shell_command("sudo chmod +x /usr/local/bin/docker-compose"),
                        ec2.InitGroup('docker'),
                        ec2.InitCommand.shell_command("sudo usermod -aG docker ec2-user"),
                        ec2.InitCommand.shell_command("export PATH=$PATH:/usr/local/bin/docker-compose"),
                        ec2.InitCommand.shell_command("sudo systemctl enable docker.service"),
                        ec2.InitCommand.shell_command("sudo systemctl start docker.service"),
                    ]),
                    "config_step3": ec2.InitConfig([
                        ec2.InitFile.from_string("/runner/.env", env_file),
                        ec2.InitFile.from_string("/runner/.drone_pool.yml", drone_pool),
                        ec2.InitFile.from_string("/runner/docker-compose.yml", docker_compose),
                        ec2.InitCommand.shell_command("sudo ssh-keygen -f /runner/id_rsa -q -P \"\" -C \"harness-delegate\""),
                    ]),
                    "config_step4": ec2.InitConfig([
                        ec2.InitCommand.shell_command("docker-compose -f /runner/docker-compose.yml up -d"),
                    ]),
                }
            ),
            init_options=ec2.ApplyCloudFormationInitOptions(
                config_sets=["ConfigSet1", "ConfigSet2"],
                ignore_failures=True,
                timeout=Duration.minutes(10)
            )
        )

        CfnOutput(self, "ConnectCommand", value=f'aws ssm start-session --target {instance.instance_id}')
