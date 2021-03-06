# Copy this to aws_config.py and put your details.
access_key = '<AWS access key>'
secret_key = '<AWS secret key>'
region = '<AWS region>'
instance_name = '<Name tag for your instance>'
domain = '<your fqdn domain with trailing dot>'
live_record_name = '<DNS record name without domain>'
ami_id = '<Amazon Machine Image ID with your software>'
key_pair = '<Amazon key pair that will be used to ssh to your instance>'
security_group = '<EC2 security group to which your instance will be assigned>'
subnet_id = '<VPC subnet ID in which your instance will be started>'
instance_size = '<EC2 instance type, default to t2.micro>'
shutdown_behavior = '<stop or termination, default to stop>'
dry_run = <False or True>
