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

log_path = '/var/log/'
file_name = 'blue-green-deploy'

blue_alias = 'blue' + '.' + domain
green_alias = 'green' + '.' + domain
old_tag = {'Environment': 'old-app'}

############################################################
#      Functions
############################################################


def set_up_logging(log_path, file_name):
    # Log file. Always in /var/log!! It will log into the file and console
    logging.basicConfig(level=logging.WARN)
    logFormatter = logging.Formatter("%(asctime)s [%(levelname)-5.5s]  %(message)s")
    rootLogger = logging.getLogger()

    fileHandler = logging.FileHandler("{0}/{1}.log".format(log_path, file_name))
    fileHandler.setFormatter(logFormatter)
    rootLogger.addHandler(fileHandler)

    consoleHandler = logging.StreamHandler()
    consoleHandler.setFormatter(logFormatter)
    rootLogger.addHandler(consoleHandler)

    return rootLogger


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
        logging.error('Could not connect to Ec2 with this parameters: %s, %s, <secret key>', region, aws_access_key)
        sys.exit(1)
    else:
        logging.info('Connected to AWS EC2 [%s]', region)

    if route53_conn is None:
        logging.error('Could not connect to Route53 with this parameters: %s, <secret key>', aws_access_key)
        sys.exit(1)
    else:
        logging.info('Connected to AWS Route53')

    return {'ec2': ec2_conn, 'route53': route53_conn}


def get_specific_instances(ec2_conn, tag_key, tag_value, instance_state):
    """
    :description: Returns requested instance - uses filters to get it.
    :param
        ec2_conn: Connections to AWS EC2.
        tag_key: Name of the tag.
        tag_value: Value of the tag.
        instance_state: One of three states - "running" / "pending" / "stopped".
    :return: boolean result.
    """
    # Filters instances with specific tag and in specific state.
    instances = ec2_conn.get_only_instances(filters={"tag:{0}".format(tag_key): tag_value,
                                                     "instance-state-name": instance_state})

    return instances


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
    instances = get_specific_instances(ec2_conn, "Environment", env, ["running", "pending"])

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
            if dry_run:
                rootLogger.warn('New instance would be created and this tags should be assigned')
                rootLogger.warn('Name: %s' % instance_name)
                rootLogger.warn('Environment: %s' % env)
                rootLogger.warn('Deployment Date: %s' % time.strftime("%d-%m-%Y"))
                return 'OK'
            else:
                rootLogger.error('Something went wrong when creating new instance.')
    else:
        # Looks like there was another instance running with the same tags.
        rootLogger.warn('There is another instance running with %s environment tag (id: %s).' % (env, instances[0]))
        return None

    return reservations.instances


def tag_instance(instance, tag_name, tag_key):
    """
    :description: Removes old tag and creates new one with updated value.
    :param
        instance: Instance that should be tagged.
        tag_name: Name of the tag.
        tag_key: Value of the tag.
    :return: boolean result.
    """
    instance.remove_tag('{0}'.format(tag_name))
    instance.add_tag('{0}'.format(tag_name), '{0}'.format(tag_key))


def stop_instance(aws_connection, env, domain, live_alias, dry_run=False):
    """
    :description: Stops past live instance.
    :param
        aws_connection: Connections to AWS Route53 service and EC2.
        env: Blue or green depends which instance you want to stop (cross check).
        domain: Your Domain.
        live_alias: Your external DNS record pointing to live web server.
        dry-run: True or False. If True, it will not make any changes.
    :return: boolean result.
    """
    result = False

    tag = ''.join(old_tag.values())

    # Gets past live instance.
    instances = get_specific_instances(aws_connection.get('ec2'), "Environment", env, "running")

    if check_which_is_live(aws_connection.get('route53'), domain, live_alias) != (env + "." + domain) and instances:
        # Instance is not live
        try:
            aws_connection.get('ec2').stop_instances(instance_ids=[instances[0].id], dry_run=dry_run)
            tag_instance(instances[0], 'Environment', tag)
        except exception.EC2ResponseError:
            rootLogger.warn('Instance %s would be stopped and tagged with Environment:%s' % (instances[0].id, tag))

        result = True
    else:
        if dry_run:
            rootLogger.warning('Old instance with tag %s would be stopped.' % env)
        else:
            rootLogger.error('Could not stop the old instance. It looks like it is live or doesnt exist. '
                             'I tried to stop %s instance.' % env)

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


def get_env(fqdn, domain):
    """
    :description: Give you environment from given fqdn by removing domain from fqdn.
    :param
        fqdn: Fully Qualified Domain Name.
        domain: Your domain name.
    :return: environment (blue or green).
    """
    env = fqdn.replace("." + domain, "")

    return env


def swap_dns(live_alias, future_value, alias_dns_name, zone, records):
    """
    :description: Changes alias (blue.<domain> or green.<domain>) that is behind live url.
    :param
        live_alias: Your external DNS record pointing to live web server.
        future_alias: blue.<domain> or green.<domain> depends which is going to be live.
        zone: handle to zone that hosts dns records.
        records: sets of dns records from the zone..
    :return: Result of the change (AWS respond).
    """
    try:
        change = records.add_change(action='UPSERT',
                                    name=live_alias,
                                    type='A',
                                    alias_dns_name=alias_dns_name,
                                    alias_hosted_zone_id=zone.id,
                                    alias_evaluate_target_health=False)
        change.add_value(future_value)
        result = records.commit()
    except Exception as ex:
        rootLogger.error('Could not swap dns entry for %s. Exception: %s' % (live_alias, ex))

    return result


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
        # Dry run
        rootLogger.warn('DNS record %s would be updated with %s' % (live_alias, current_live))
        stop_instance(aws_connection, 'blue' if current_live == blue_alias else 'green', domain, live_alias, dry_run=dry_run)

        result = 'OK'
    else:
        if current_live == blue_alias:
            # Blue was live so now time for Green.
            result = swap_dns(live_alias, green_alias, green_alias, zone, records)
            env_old = 'blue'
        else:
            # This time Green was live. Blue, are you ready?
            result = swap_dns(live_alias, blue_alias, blue_alias, zone, records)
            env_old = 'green'

        # Wait TTL and then stop second instance
        #time.sleep(300)

        #stop_instance(aws_connection, env_old, domain, live_alias)

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
        rootLogger.warn('Public IP %s would be assigned to %s' % (instance_public_ip, live_alias))

        result = 'OK'
    else:
        result = swap_dns(blue_alias if current_live == green_alias else green_alias, instance_public_ip, None, zone, records)

    return result


def roll_back(aws_conn, old_tag, domain, live_alias, dry_run=False):
    """
    :description: Rolls back deployment by starting instance with old-app tag and swapping dns entry.
    :param
        ec2_conn: Connection to AWS EC2 service
        old_tag: Dictionary with <tag_name> <tag_value> pair
        dry-run: True or False. If True, it will not make any changes.
    :return: boolean status
    """
    result = True

    old_instance = get_specific_instances(aws_conn.get('ec2'), ''.join(old_tag.keys()), ''.join(old_tag.values()), ['stopped', 'running'])
    current_live = check_which_is_live(aws_conn.get('route53'), domain, live_alias)
    env = get_env(current_live, domain)

    if not old_instance:
        rootLogger.error('No instance with tag %s was found. No chance to roll back Sir!' % ''.join(old_tag.values()))
    else:
        try:
            if dry_run:
                rootLogger.warning('Instance %s would be started and tagged with %s' % (old_instance, env))
            else:
                old_instance[0].start()
                tag_instance(old_instance[0], 'Environment', 'blue' if env == 'green' else 'green')

            instance_public_ip = wait_for_public_ip(aws_conn.get('ec2'), old_instance[0].id)

            assign_to_staging(aws_conn.get('route53'), domain, current_live, instance_public_ip, dry_run=False)
            swap_live_with_staging(aws_conn, domain, current_live, live_alias, dry_run)
            stop_instance(aws_conn, env, domain, live_alias, dry_run)
        except exception.EC2ResponseError:
            rootLogger.error('Could not start %s instance.' % old_instance)
            result = False

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
    instances = get_specific_instances(ec2_conn, ''.join(old_tag.keys()), ''.join(old_tag.values()), "stopped")

    if len(instances) is 1:
        # If there is only 1 instance in that state.
        old = instances[0]

        rootLogger.debug("I am going to delete %s" % old.id)
        try:
            deleted_old = ec2_conn.terminate_instances(instance_ids=[old.id], dry_run=dry_run)

            # Previous line should return instance that was deleted. Worth to check if it was the one we want to delete.
            if deleted_old[0].id == old.id:
                rootLogger.info('Deleted %s' % deleted_old[0].id)
                result = True
        except exception.EC2ResponseError as ex:
            rootLogger.error('Instance %s would be deleted.' % old.id)
            rootLogger.error(ex)
    else:
        # It could be none or multiple instance in that state. Better notify before someone starts complaining.
        rootLogger.warn('No old instance or more than 1 instance was found. I hope you are aware of that. Continue.')
        result = True  # I am returning true because it shouldn't be a big issue

    return result


def wait_for_public_ip(ec2_conn, instance_id):
    counter = 0

    while counter < 6:
        # We are going to check every 5 seconds for 30 seconds.
        stg_instance = ec2_conn.get_only_instances(instance_ids=[instance_id])

        if stg_instance[0].ip_address is None:
            # Still not available so wait 5 seconds.
            time.sleep(5)
        else:
            # We got it!
            public_ip = stg_instance[0].ip_address
            return str(public_ip)

        counter += 1

    # Unfortunately we couldn't get Public IP so logging and exiting.
    rootLogger.error('Cannot get Public IP from instance %s' % stg_instance[0].id)
    sys.exit(1)

    return None


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

    past_live = get_env(live, domain)

    # 4. If deleted then we can create new instance
    if dry_run:
        # Dry Run
        create_new_instance(aws_connections.get('ec2'), image_id, ssh_key, sec_group, subnet_id, env, srv_name, None, instance_size, shutdown_behavior, dry_run)
        assign_to_staging(aws_connections.get('route53'), domain, live, "127.0.0.1", dry_run)
        swap_live_with_staging(aws_connections, domain, live, live_url, dry_run)
        stop_instance(aws_connections, past_live, domain, live_alias, dry_run)
        #roll_back(aws_connections, old_tag, domain, live_alias, False)
        sys.exit(0)
    elif deleted:
        staging_instance = create_new_instance(aws_connections.get('ec2'), image_id, ssh_key, sec_group, subnet_id, env, srv_name, None, instance_size, shutdown_behavior, dry_run)

    # 5. Assign right dns alias only if we managed to create instance in previous step
    if staging_instance is None:
        # There were some problems with creating new instance
        rootLogger.error('Could not create new instance.')
        sys.exit(1)
    else:
        # Everything was all right. Waiting for Public IP
        if staging_instance[0].ip_address is None:
            # Unfortunately Public IP is not available straight away so we have to wait for it.
            public_ip = wait_for_public_ip(aws_connections.get('ec2'), staging_instance[0].id)

            if public_ip is None:
                rootLogger.error('Cannot get Public IP from instance %s' % staging_instance[0].id)
                sys.exit(1)
        else:
            # Or maybe it is? :)
            public_ip = staging_instance[0].ip_address

        assign_to_staging(aws_connections.get('route53'), domain, live, public_ip, dry_run)

        swap_live_with_staging(aws_connections, domain, live, live_url, dry_run)

        stop_instance(aws_connections, past_live, domain, live_alias)

    return str(env + "." + domain + ": " + public_ip)

rootLogger = set_up_logging(log_path, file_name)

print(deployment_stage(region, aws_access_key, aws_secret_key, web_srv_name, domain, live_alias, blue_alias, green_alias,
                 old_tag, image_id, ssh_key, sec_group, subnet_id, instance_size, shutdown_behavior, dry_run))