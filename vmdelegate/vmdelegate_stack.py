from jinja2 import render_template
from aws_cdk import (
    CfnOutput,
    Stack,
)
import aws_cdk.aws_ec2 as ec2
from constructs import Construct

vpc_id =""
ec2_type=""
key_name=""
linux_ami_id = ec2.GenericLinuxImage({
    "us-east-2": "ami-0fb653ca2d3203ac1",
})

class VmdelegateStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        vpc = ec2.Vpc.from_lookup(self, "VPC", vpc_id=vpc_id)

        security_group = ec2.SecurityGroup(self, "SecurityGroup",
            vpc=vpc,
            allow_all_outbound=True,
            description="Allow all outbound traffic",
            security_group_name="VmdelegateSecurityGroup")

        security_group.add_ingress_rule(aws_ec2.Peer.ipv4("0.0.0.0/0"), connection=ec2.Port.tcp(22), description="Allow SSH")
        security_group.add_ingress_rule(aws_ec2.Peer.ipv4("0.0.0.0/0"), connection=ec2.Port.tcp(80), description="Allow HTTP")
        security_group.add_ingress_rule(aws_ec2.Peer.ipv4("0.0.0.0/0"), connection=ec2.Port.tcp(3389), description="Allow RDP")
        security_group.add_ingress_rule(aws_ec2.Peer.ipv4("0.0.0.0/0"), connection=ec2.Port.tcp(9079), description="Allow port 9079")

        host = ec2.Instance(self, "HarnessDelegate",
            instance_type=ec2.InstanceType(instance_type_identifier=ec2_type),
            instance_name="HarnessDelegate",
            machine_image=linux_ami_id,
            vpc=vpc,
            key_name=key_name,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            init=ec2.CloudFormationInit.from_config_sets(
                config_sets={
                    "default": ["aptPreInstall", "config"]
                },
                configs = {
                    "apt_preInstall": ec2.InitConfig([
                        ec2.InitPackage.apt("docker.io"),]),
                    "config": ec2.InitConfig([

                        ec2.InitFile.from_string("/runner/.env.yml", content),
                        ec2.InitFile.from_string("/runner/.drone_pool.yml", content),
                        ec2.InitFile.from_string("/runner/docker-compose.yml", content),

                        ec2.InitGroup.from_name("docker"),
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
        ]) # by default VolumeType is gp2, VolumeSize 8GB

        CfnOutput(self, "Output", value=host.instance_public_ip)