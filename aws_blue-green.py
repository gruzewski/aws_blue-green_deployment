__author__ = 'jacek gruzewski'

"""
To do: through exceptions rather than calling sys.exit(1)
"""

############################################################
#      IMPORTS
############################################################

# Python's libraries
import time
import sys
import logging
import os

# AWS Boto library
from boto import ec2, route53, exception

# Config file imports
import aws_config

try:
    # Checking if all attributes were set.
    region = getattr(aws_config, "region")
    aws_access_key = getattr(aws_config, "access_key")
    aws_secret_key = getattr(aws_config, "secret_key")
    web_srv_name = getattr(aws_config, "instance_name")
    domain = getattr(aws_config, "domain")
    live_alias = getattr(aws_config, "live_record_name") + "." + domain
    image_id = getattr(aws_config, "ami_id")
    ssh_key = getattr(aws_config, "key_pair")
    sec_group = getattr(aws_config, "security_group")
    subnet_id = getattr(aws_config, "subnet_id")
    instance_size = getattr(aws_config, "instance_size")
    shutdown_behavior = getattr(aws_config, "shutdown_behavior")
    dry_run = getattr(aws_config, "dry_run")
except AttributeError as at_err:
    # Falling back to local variables. Worth to try!
    logging.error('Couldnt read parameters from aws_config.py file. [%s]', at_err)
    region = os.environ['AWS_DEFAULT_REGION']
    aws_access_key = os.environ['AWS_ACCESS_KEY_ID']
    aws_secret_key = os.environ['AWS_SECRET_ACCESS_KEY']

    if region is None or aws_access_key is None or aws_secret_key is None:
        # At least we tried.
        logging.error('Couldnt find AWS credentials in local variables')
        sys.exit(1)
    else:
        logging.info('Got AWS credentials from local variables')


############################################################
#      Static data and configuration
############################################################

# Static AWS Rest service for getting instance details
AWS_METADATA = 'http://169.254.169.254/latest/meta-data/instance-id'

# Log file. Always in /var/log!!
logging.basicConfig(filename='/var/log/blue-green-deploy.log', level=logging.INFO)

# Static variables
blue_alias = 'blue' + '.' + domain
green_alias = 'green' + '.' + domain
old_tag = {'Environment': 'old-app'}

############################################################
#      Functions
############################################################

def connect_to_AWS(region, aws_access_key, aws_secret_key):
    """
    :param:
        region: AWS region
        aws_access_key: AWS Access Key
        aws_secret_key: AWS Secret Key
    :return: map of aws services and connection handles for them.
    """
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

def create_new_instance(ec2_conn, image_id, ssh_key, sec_group, subnet_id, env, instance_name, user_data=None, instance_size='t2.micro', shutdown_behavior='stop', dry_run=False):
    """
    :param
        ec2_conn: connection to AWS EC2 service
        image_id: Amazon Machine Image ID with all your software
        ssh_key: AWS key pair name
        sec_group: Security group ID that should be allocated
        subnet_id: Subnet ID in which your instance should be created
        env: Environment (blue / green / old_app)
        instance_name: Name tag value
        user_data: Cloud-Init script that will run once
        instance_size: String with instance size
        shutdown_behaviour: stop or termination
        dry-run: True or False. If True, it will not make any changes.
    :return: instance ID if created or None
    """
    # Checks (by filtering instances currently running) if there is no other instance running with the same tags.
    instances = ec2_conn.get_only_instances(filters={"tag:Environment" : "{0}".format(''.join(env)),
                                                     "instance-state-name" : "running"})

    if not instances:
        # If list is not empty. Creates new instance.
        try:
            reservations = ec2_conn.run_instances(image_id,
                                           key_name=ssh_key,
                                           user_data=user_data,
                                           instance_type=instance_size,
                                           subnet_id=subnet_id,
                                           security_group_ids=[sec_group],
                                           instance_initiated_shutdown_behavior=shutdown_behavior,
                                           dry_run=dry_run)

            if reservations is not None and not dry_run:
                # When instance was created, we have to assign tags.
                reservations.instances[0].add_tag('Name', instance_name)
                reservations.instances[0].add_tag('Environment', env)
                reservations.instances[0].add_tag('Deployment Date', time.strftime("%d-%m-%Y"))
        except exception.EC2ResponseError:
            print('New instance would be created and this tags should be assigned')
            print('Name: %s' % instance_name)
            print('Environment: %s' % env)
            print('Deployment Date: %s' % time.strftime("%d-%m-%Y"))
            return 'OK'
    else:
        # Looks like there was another instance running with the same tags.
        logging.warn('There is another instance running with %s environment tag (id: %s).' % (env, instances[0]))
        return None

    return reservations.instances

def stop_instance(aws_connection, env, domain, live_alias, dry_run=False):


    result = False

    tag = ''.join(old_tag.values())

    # Gets past live instance.
    instances = aws_connection.get('ec2').get_only_instances(filters={"tag:Environment" : "{0}".format(''.join(env)),
                                                     "instance-state-name" : "running"})

    if check_which_is_live(aws_connection.get('route53'), domain, live_alias) != (env + "." + domain) and instances:
        # Instance is not live
        try:
            instances[0].remove_tag('Environment')
            aws_connection.get('ec2').stop_instances(instance_ids=[instances[0].id], dry_run=dry_run)
            instances[0].add_tag('Environment', '{0}'.format(tag))
        except exception.EC2ResponseError as ex:
            print('Instance %s would be stopped and tagged with Environment:%s' % (instances[0].id, tag))
            print(ex)

        result = True
    else:
        logging.error('Couldnt stop old instance.')
        if dry_run:
            print('Instance %s would be stopped and tagged with Environment:%s' % (instances[0].id, tag))

    return result

def check_which_is_live(route53_conn, domain, live_alias):
    """
    :description: Checks which alias (blue.<domain> or green.<domain>) is live.
    :param
        route53_conn: Connection to AWS Route53 service
        domain: Your Domain
        live_alias: Your external DNS record pointing to live web server.
    :return: fqdn of live sub alias (blue or green)
    """
    live_fqdn = route53_conn.get_zone(domain).get_a(live_alias).alias_dns_name

    return live_fqdn

def swap_live_with_staging(aws_connection, domain, current_live, live_alias, dry_run=False):
    """
    :description: Changes alias (blue.<domain> or green.<domain>) that is behind live url.
    :param
        aws_connection: Connections to AWS Route53 service and EC2
        domain: Your Domain
        current_live: blue.<domain> or green.<domain> depends which is live
        live_alias: Your external DNS record pointing to live web server.
        dry-run: True or False. If True, it will not make any changes.
    :return: Result of the change (AWS respond).
    """
    route53_conn = aws_connection.get('route53')

    zone = route53_conn.get_zone(domain)

    records = route53.record.ResourceRecordSets(connection=route53_conn, hosted_zone_id=zone.id)

    if dry_run:
        if current_live == blue_alias:
            print('DNS record %s would be updated with %s' % (live_alias, green_alias))
            stop_instance(aws_connection, 'blue', domain, live_alias, dry_run=dry_run)
        else:
            print('DNS record %s would be updated with %s' % (live_alias, blue_alias))
            stop_instance(aws_connection, 'green', domain, live_alias, dry_run=dry_run)

        result = 'OK'
    else:
        if current_live == blue_alias:
            # Blue was live so now time for Green.
            change = records.add_change(action='UPSERT', name=live_alias, type='A', alias_dns_name=green_alias, alias_hosted_zone_id=zone.id, alias_evaluate_target_health=False)
            change.add_value(green_alias)
            result = records.commit()

            # Wait TTL and then stop second instance
            time.sleep(300)

            stop_instance(aws_connection, 'blue', domain, live_alias, dry_run=dry_run)
        else:
            # This time Green was live. Blue, are you ready?
            change = records.add_change(action='UPSERT', name=live_alias, type='A', alias_dns_name=blue_alias, alias_hosted_zone_id=zone.id, alias_evaluate_target_health=False)
            change.add_value(blue_alias)
            result = records.commit()

            # Wait TTL and then stop second instance
            time.sleep(600)

            stop_instance(aws_connection, 'green', domain, live_alias, dry_run=dry_run)
    return result

def assign_to_staging(route53_conn, domain, current_live, instance_public_ip, dry_run=False):
    """
    :description: Assigns newly created instance to staging url
    :param
        route53_conn: Connection to AWS Route53 service
        domain: Your Domain
        current_live: blue.<domain> or green.<domain> depends which one was behind your live url.
        instance_public_ip: Public IP of newly created instance that would be assigned to staging url.
        dry-run: True or False. If True, it will not make any changes.
    :return: Result of the change (AWS respond).
    """
    zone = route53_conn.get_zone(domain)

    records = route53.record.ResourceRecordSets(connection=route53_conn, hosted_zone_id=zone.id)

    if dry_run:
        if current_live == green_alias:
            print('Public IP %s would be assigned to %s' % (instance_public_ip, blue_alias))
        else:
            print('Public IP %s would be assigned to %s' % (instance_public_ip, green_alias))

        result = 'OK'
    else:
        if current_live == green_alias:
            # Green was live so we are assigning to Blue.
            change = records.add_change(action='UPSERT', name=blue_alias, type='A', alias_hosted_zone_id=zone.id, alias_evaluate_target_health=False)
            change.add_value(instance_public_ip)
            result = records.commit()

            logging.debug(result)
        else:
            # Blue was live and Green is going to get new instance!
            change = records.add_change(action='UPSERT', name=green_alias, type='A', alias_hosted_zone_id=zone.id, alias_evaluate_target_health=False)
            change.add_value(instance_public_ip)
            result = records.commit()

            logging.debug(result)
    return result

def delete_old_instance(ec2_conn, old_tag, dry_run=False):
    """
    :description: Deletes instance for given tag only if it is stopped
    :param
        ec2_conn: Connection to AWS EC2 service
        old_tag: Dictionary with <tag_name> <tag_value> pair
        dry-run: True or False. If True, it will not make any changes.
    :return: boolean status
    """
    result = False

    # Filters instances with tag Environment = old-app and only in stopped state.
    instances = ec2_conn.get_only_instances(filters={"tag:{0}".format(''.join(old_tag.keys())) : "{0}".format(''.join(old_tag.values())),
                                                     "instance-state-name" : "stopped"})

    if len(instances) is 1:
        # If there is only 1 instance in that state.
        old = instances[0]

        # Double check?
        if old.state == "stopped":
            logging.debug("I am going to delete %s" % old.id)
            try:
                deleted_old = ec2_conn.terminate_instances(instance_ids=[old.id], dry_run=dry_run)

                # Previous line should return instance that was deleted. Worth to check if it was the one we want to delete.
                if deleted_old[0].id == old.id:
                    logging.info('Deleted %s' % deleted_old[0].id)
                    result = True
            except exception.EC2ResponseError as ex:
                print('Instance %s would be deleted.' % old.id)
                print(ex)
        else:
            logging.error('Old instance %s [%s] is not stopped! Reported state was "%s" ' % (old.tags.get('Name'), old.id, old.state))
    else:
        # It could be none or multiple instance in that state. Better notify before someone starts complaining.
        logging.warn('No old instance or more than 1 instance was found. I hope you are aware of that. Continue.')
        result = True  # I am returning true because it shouldn't be a big issue

    return result

def deployment_stage(region, acces_key, secret_key, srv_name, domain, live_url, blue_url, green_url, old_tag, image_id,
                     ssh_key, sec_group, subnet_id, instance_size, shutdown_behavior, dry_run=False):
    """
    :description: Delivers new instance with staging dns (blue / green).
    :param
        region: region to which you want to deploy your instance
        access_key: AWS Access Key
        secret_key: AWS Secret Key
        srv_name: How you want to call your web server
        domain: Your domain
        live_url: DNS record for your live website
        blue_url: Blue Url
        green_url: Green Url
        old_tag: Dictionary with <tag_name> <tag_value> pair
        image_id: Amazon Machine Image ID with all your software
        ssh_key: AWS key pair name
        sec_group: Security group ID that should be allocated
        subnet_id: Subnet ID in which your instance should be created
        instance_size: String with instance size
        shutdown_behaviour: stop or termination
        dry-run: True or False. If True, it will not make any changes.
    :return: string with url and ip address to staging server
    """
    # 1. Connects to AWS
    aws_connections = connect_to_AWS(region, acces_key, secret_key)

    # 2. Delete old instance which should be stopped
    deleted = delete_old_instance(aws_connections.get('ec2'), old_tag, dry_run)

    # 3. Check which environment (blue/green) is live
    live = check_which_is_live(aws_connections.get('route53'), domain, live_url)
    if live == blue_url:
        env = 'green'
    else:
        env = 'blue'

    # 4. If deleted then we can create new instance
    if dry_run:
        # Dry Run
        staging_instance = create_new_instance(aws_connections.get('ec2'), image_id, ssh_key, sec_group, subnet_id, env, srv_name, None, instance_size, shutdown_behavior, dry_run)
        assign_to_staging(aws_connections.get('route53'), domain, live, "127.0.0.1", dry_run)
        swap_live_with_staging(aws_connections, domain, live, live_url, dry_run)
        sys.exit(0)
    elif deleted:
        staging_instance = create_new_instance(aws_connections.get('ec2'), image_id, ssh_key, sec_group, subnet_id, env, srv_name, None, instance_size, shutdown_behavior, dry_run)

    # 5. Assign right dns alias only if we managed to create instance in previous step
    if staging_instance is None:
        # There some problems with creating new instance
        logging.error('Couldnt create new instance.')
        sys.exit(1)
    else:
        if staging_instance[0].ip_address is None:
            # Unfortunately Public IP is not available straight away so we have to wait for it.
            for counter in range(7):
                # We are going to check every 10 seconds for 1 minutes.
                stg_instance = aws_connections.get('ec2').get_only_instances(instance_ids=[staging_instance[0].id])

                if stg_instance[0].ip_address is None:
                    # Still not available so wait 10 seconds/
                    time.sleep(10)
                    if counter is 6:
                        # Timeout.
                        logging.error('Cannot get Public IP from instance %s' % staging_instance[0].id)
                        sys.exit(1)
                else:
                    # We got it!
                    public_ip = stg_instance[0].ip_address
                    break

                counter += 1
        else:
            # Or maybe it is? :)
            public_ip = staging_instance[0].ip_address

        assign_to_staging(aws_connections.get('route53'), domain, live, public_ip, dry_run)

        swap_live_with_staging(aws_connections, domain, live, live_url, dry_run)

    return str(env + "." + domain + ": " + public_ip)

print(deployment_stage(region, aws_access_key, aws_secret_key, web_srv_name, domain, live_alias, blue_alias, green_alias,
                 old_tag, image_id, ssh_key, sec_group, subnet_id, instance_size, shutdown_behavior, dry_run))