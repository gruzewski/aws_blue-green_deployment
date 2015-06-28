__author__ = 'jacek gruzewski'

"""
To do: through exceptions rather than calling sys.exit(1)
"""

############################################################
#      IMPORTS
############################################################

# Python's libraries
import requests
import sys
import logging
import os

# AWS Boto library
from boto import ec2, route53

# Config file imports
import aws_config

############################################################
#      Static data and configuration
############################################################

# Static AWS Rest service for getting instance details
AWS_METADATA = 'http://169.254.169.254/latest/meta-data/instance-id'

logging.basicConfig(filename='/var/log/blue-green-deploy.log', level=logging.INFO)

web_srv_name = 'Web-Server TEST'
domain = 'example.com.'
live_alias = 'webserver' + '.' + domain
blue_alias = 'blue' + '.' + domain
green_alias = 'green' + '.' + domain

def connect_to_AWS():
    # Parsing configuration and connecting to AWS
    try:
        region = getattr(aws_config, "region")
        aws_access_key = getattr(aws_config, "access_key")
        aws_secret_key = getattr(aws_config, "secret_key")
        eip_id = getattr(aws_config, "eip_id")

        ec2_conn = ec2.connect_to_region(region_name=region,
                                         aws_access_key_id=aws_access_key,
                                         aws_secret_access_key=aws_secret_key)

        route53_conn = route53.Route53Connection(aws_access_key_id=aws_access_key,
                                                 aws_secret_access_key=aws_secret_key)
    except AttributeError as at_err:
        logging.error('Couldnt read parameters from aws_config.py file. [%s]', at_err)
        region = os.environ['AWS_DEFAULT_REGION']
        aws_access_key = os.environ['AWS_ACCESS_KEY_ID']
        aws_secret_key = os.environ['AWS_SECRET_ACCESS_KEY']

        if region is None or aws_access_key is None or aws_secret_key is None:
            logging.error('Couldnt find AWS credentials in local variables')
            sys.exit(1)
        else:
            logging.info('Got AWS credentials from local variables')

            ec2_conn = ec2.connect_to_region(region_name=region,
                                             aws_access_key_id=aws_access_key,
                                             aws_secret_access_key=aws_secret_key)

            route53_conn = route53.Route53Connection(aws_access_key_id=aws_access_key,
                                                     aws_secret_access_key=aws_secret_key)

    if ec2_conn is None:
        logging.error('Couldnt connect to Ec2 with this parameters: %s, %s, <secret key>', region, aws_access_key)
        sys.exit(1)
    else:
        logging.info('Connected to AWS EC2 [%s]', region)

    if route53_conn is None:
        logging.error('Couldnt connect to Route53 with this parameters: %s, <secret key>', aws_access_key)
        sys.exit(1)
    else:
        logging.info('Connected to AWS Route53')

    return {'ec2': ec2_conn, 'route53': route53_conn}

def create_new_instance(ec2_conn, image_id, ssh_key, sec_group, subnet_id, user_data=None, instance_size='t2.micro', shutdown_behavior='stop'):
    """
    :param
        ec2_conn: connection to AWS EC2 service
        image_id: Amazon Machine Image ID with all your software
        ssh_key: AWS key pair name
        sec_group: Security group ID that should be allocated
        subnet_id: Subnet ID in which your instance should be created
        user_data: Cloud-Init script that will run once
        instance_size: String with instance size
        shutdown_behaviour: stop or termination
    :return: instance ID or None
    """
    reservations = ec2_conn.run_instances(image_id,
                                           key_name=ssh_key,
                                           user_data=user_data,
                                           instance_type=instance_size,
                                           subnet_id=subnet_id,
                                           security_group_ids=sec_group,
                                           instance_initiated_shutdown_behavior=shutdown_behavior)

    if reservations is not None:
        reservations.instances[0].add_tag('Name', web_srv_name)
        reservations.instances[0].add_tag('Environment', 'blue')

    return reservations.instances

def check_which_is_live(route53_conn):
    live_fqdn = route53_conn.get_zone(domain).get_a(live_alias).alias_dns_name

    return live_fqdn#.replace('.' + domain, '')

def assign_to_live(route53_conn, current_live):

    records = route53.record.ResourceRecordSets(connection=route53_conn, hosted_zone_id='Z1O0H3GLQ224C8')

    if current_live == blue_alias:
        change = records.add_change(action='UPSERT', name=live_alias, type='A', alias_dns_name=green_alias, alias_hosted_zone_id='Z1O0H3GLQ224C8', alias_evaluate_target_health=False)
        change.add_value(green_alias)
        result = records.commit()

        print(result)
    else:
        change = records.add_change(action='UPSERT', name=live_alias, type='A', alias_dns_name=blue_alias, alias_hosted_zone_id='Z1O0H3GLQ224C8', alias_evaluate_target_health=False)
        change.add_value(blue_alias)
        result = records.commit()

        print(result)
    return result

route53_connection = connect_to_AWS().get('route53')
live_current = check_which_is_live(route53_connection)

print(live_current)

print(assign_to_live(route53_connection, live_current))

#reservation = create_new_instance(connect_to_AWS().get('ec2'), 'ami-47a23a30', 'frontend-key', ['sg-37784152'], 'subnet-0d389654')

#print(reservation)


"""
try:
    instance_id = requests.get(AWS_METADATA, timeout=0.5).text
except requests.exceptions.Timeout as t_err:
    logging.error('Timeout occured: %s', t_err)
    sys.exit(1)
except requests.exceptions.ConnectionError as con_err:
    logging.error('Connection error occured: %s', con_err)
    sys.exit(1)

logging.info('Got details from AWS [instance id: %s]', instance_id)

eip = ec2_conn.get_all_addresses(allocation_ids=eip_id)

if eip[0].instance_id is None:
    status = ec2_conn.associate_address(instance_id=instance_id,
                                        public_ip=None,
                                        allocation_id=eip_id)
    if status is True:
        logging.info('Elastic IP was allocated.')
    else:
        logging.error('Elastic IP [%s] couldnt be allocated.', eip_id)
else:
    logging('Elastic IP [%s] is allocated to %s', eip_id, instance_id)
    sys.exit(1)
"""