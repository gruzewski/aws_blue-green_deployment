__author__ = 'jacek gruzewski'

#!/user/bin/python3.4

"""
To do: throw exceptions rather than calling sys.exit(1)
"""

############################################################
#      IMPORTS
############################################################

# Python's libraries
import time
import sys
import logging
import os
import requests

# AWS Boto library
from boto import ec2, route53, exception

#####################################################################
#      Static data and configuration
#####################################################################

# Static AWS Rest service for getting instance details
AWS_METADATA = 'http://169.254.169.254/latest/meta-data/instance-id'

log_path = '/var/log/'
file_name = 'blue-green-deploy'

#####################################################################
#      Functions
#####################################################################


def read_config_file(logger):
    # Config file imports
    import aws_config

    try:
        # Checking if all attributes were set.

        domain = getattr(aws_config, "domain")

        config = {
         'reg': getattr(aws_config, "region"),
         'access': getattr(aws_config, "access_key"),
         'secret': getattr(aws_config, "secret_key"),
         'srv': getattr(aws_config, "instance_name"),
         'domain': domain,
         'alias': getattr(aws_config, "live_record_name") + "." + domain,
         'image': getattr(aws_config, "ami_id"),
         'key': getattr(aws_config, "key_pair"),
         'sec': [getattr(aws_config, "security_group")],
         'subnet': getattr(aws_config, "subnet_id"),
         'type': getattr(aws_config, "instance_size"),
         'shutdown': getattr(aws_config, "shutdown_behavior"),
         'dry-run': getattr(aws_config, "dry_run")
         }
    except AttributeError as at_err:
        # Falling back to local variables. Worth to try!
        logger.error('Could not read parameters from aws_config.py file. [%s]', at_err)
        region = os.environ['AWS_DEFAULT_REGION']
        aws_access_key = os.environ['AWS_ACCESS_KEY_ID']
        aws_secret_key = os.environ['AWS_SECRET_ACCESS_KEY']

        if region is None or aws_access_key is None or aws_secret_key is None:
            # At least we tried.
            logger.error('Could not find AWS credentials in local variables')
            sys.exit(1)
        else:
            logger.info('Got AWS credentials from local variables')

    return config


def set_up_logging(path, file):
    # Log file. Always in /var/log!! It will log into the file and console
    logging.basicConfig(level=logging.WARN)
    log_formatter = logging.Formatter("%(asctime)s [%(levelname)-5.5s]  %(message)s")
    root_logger = logging.getLogger()

    file_handler = logging.FileHandler("{0}/{1}.log".format(path, file))
    file_handler.setFormatter(log_formatter)
    root_logger.addHandler(file_handler)

    return root_logger


def connect_to_aws(region, aws_access_key, aws_secret_key):
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


def create_new_instance(ec2_conn, image_id, ssh_key, sec_group, subnet_id, env, instance_name, user_data=None,
                        instance_size='t2.micro', shutdown='stop', dry_run=False):
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
                                                  security_group_ids=sec_group,
                                                  instance_initiated_shutdown_behavior=shutdown,
                                                  dry_run=dry_run)

            if reservations is not None and not dry_run:
                # When instance was created, we have to assign tags.
                tag_new_instance(reservations.instances[0], instance_name, env)
            else:
                LOGGER.error('Something went wrong when creating new instance.')
                sys.exit(1)
        except exception.EC2ResponseError:
            if dry_run:
                LOGGER.warn('New instance would be created and this tags should be assigned')
                LOGGER.warn('Name: %s' % instance_name)
                LOGGER.warn('Environment: %s' % env)
                LOGGER.warn('Deployment Date: %s' % time.strftime("%d-%m-%Y"))
                return 'OK'
            else:
                LOGGER.error('Something went wrong when creating new instance.')

                try:
                    # Last chance - waiting 1 minute to tag instance.
                    time.sleep(60)
                    tag_new_instance(reservations.instances[0], instance_name, env)
                except exception.EC2ResponseError:
                    sys.exit(1)
    else:
        # Looks like there was another instance running with the same tags.
        LOGGER.warn('There is another instance running with %s environment tag (id: %s).' % (env, instances[0]))
        return None

    return reservations.instances


def tag_instance(instance, tag_name, tag_key):
    """
    :description: Removes old tag and creates new one with updated value.
    :param
        instance: Instance that should be tagged.
        tag_name: Name of the tag.
        tag_key: Value of the tag.
    :return: None
    """
    instance.remove_tag('{0}'.format(tag_name))
    instance.add_tag('{0}'.format(tag_name), '{0}'.format(tag_key))


def tag_new_instance(instance, instance_name, environment):
    """
    :description: Tags new instance.
    :param
        instance: Instance that should be tagged.
        instance_name: Name of the instance.
        environment: blue org green.
    :return: None
    """
    instance.add_tag('Name', instance_name)
    instance.add_tag('Environment', environment)
    instance.add_tag('Deployment Date', time.strftime("%d-%m-%Y"))


def stop_instance(aws_connection, env, domain, live_alias, tag, dry_run=False):
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

    tag = ''.join(tag.values())

    # Gets past live instance.
    instances = get_specific_instances(aws_connection.get('ec2'), "Environment", env, "running")

    if check_which_is_live(aws_connection.get('route53'), domain, live_alias) != (env + "." + domain) and instances:
        # Instance is not live
        try:
            aws_connection.get('ec2').stop_instances(instance_ids=[instances[0].id], dry_run=dry_run)
            tag_instance(instances[0], 'Environment', tag)
        except exception.EC2ResponseError:
            LOGGER.warn('Instance %s would be stopped and tagged with Environment:%s' % (instances[0].id, tag))

        result = True
    else:
        if dry_run:
            LOGGER.warning('Old instance with tag %s would be stopped.' % env)
        else:
            LOGGER.error('Could not stop the old instance. It looks like it is live or doesnt exist. '
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
                                    ttl=300,
                                    type='A',
                                    alias_dns_name=alias_dns_name,
                                    alias_hosted_zone_id=zone.id,
                                    alias_evaluate_target_health=False)
        change.add_value(future_value)
        result = records.commit()
    except Exception as ex:
        LOGGER.error('Could not swap dns entry for %s. Exception: %s' % (live_alias, ex))
        sys.exit(1)

    return result


def swap_live_with_staging(aws_connection, domain, current_live, live_alias, blue_alias, green_alias, dry_run=False):
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
        LOGGER.warn('DNS record %s would be updated with %s' %
                    (live_alias, green_alias if current_live == blue_alias else blue_alias))

        result = 'OK'
    else:
        if current_live == blue_alias:
            # Blue was live so now time for Green.
            #if simple_check(green_alias):
                result = swap_dns(live_alias, green_alias, green_alias, zone, records)
            #else:
            #    LOGGER.error('Staging is not running.')
            #    sys.exit(1)

        else:
            # This time Green was live. Blue, are you ready?
            #if simple_check(blue_alias):
                result = swap_dns(live_alias, blue_alias, blue_alias, zone, records)
            #else:
            #    LOGGER.error('Staging is not running.')
            #    sys.exit(1)

    return result


def assign_to_staging(route53_conn, domain, current_live, instance_public_ip, live_alias, blue_alias, green_alias,
                      dry_run=False):
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
        LOGGER.warn('Public IP %s would be assigned to %s' % (instance_public_ip, live_alias))

        result = 'OK'
    else:
        result = swap_dns(blue_alias if current_live == green_alias else green_alias, instance_public_ip, None, zone,
                          records)

    return result


def delete_old_instance(ec2_conn, tag, dry_run=False):
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
    instances = get_specific_instances(ec2_conn, ''.join(tag.keys()), ''.join(tag.values()), "stopped")

    if len(instances) is 1:
        # If there is only 1 instance in that state.
        old = instances[0]

        LOGGER.debug("I am going to delete %s" % old.id)
        try:
            deleted_old = ec2_conn.terminate_instances(instance_ids=[old.id], dry_run=dry_run)

            # Previous line should return instance that was deleted. Worth to check if it was the one we want to delete.
            if deleted_old[0].id == old.id:
                LOGGER.info('Deleted %s' % deleted_old[0].id)
                result = True
        except exception.EC2ResponseError as ex:
            if dry_run:
                LOGGER.error('Instance %s would be deleted.' % old.id)
            else:
                LOGGER.error('Something went wrong when deleting old instance.')

            LOGGER.error(ex)
    else:
        # It could be none or multiple instance in that state. Better notify before someone starts complaining.
        LOGGER.warn('No old instance or more than 1 instance was found. I hope you are aware of that. Continue.')
        result = True  # I am returning true because it shouldn't be a big issue

    return result


def wait_for_public_ip(ec2_conn, instance_id):
    """
    :description: Gets instance's Public IP. Retries every 5 seconds for 30 seconds.
    :param
        ec2_conn: Connection to AWS EC2 service
        instance_id: ID of instance :)
    :return: Public IP or exits the script
    """
    counter = 0

    while counter < 24:
        # We are going to check every 10 seconds for 2 minutes.
        stg_instance = ec2_conn.get_only_instances(instance_ids=[instance_id])

        if stg_instance[0].ip_address is None:
            # Still not available so wait 5 seconds.
            time.sleep(10)
        else:
            # We got it!
            public_ip = stg_instance[0].ip_address
            return str(public_ip)

        counter += 1

    # Unfortunately we couldn't get Public IP so logging and exiting.
    stg_instance = ec2_conn.get_only_instances(instance_ids=[instance_id])
    LOGGER.error('Cannot get Public IP from instance %s' % stg_instance[0].id)
    sys.exit(1)


def simple_check(url):
    """
    :description: Checks if given url is returning 200 respond code for 10 minutes in 60 seconds intervals.
    :param
        url: link which should be checked
    :return: Boolean
    """

    counter = 0

    while counter < 10:
        try:
            r = requests.head('http://' + url)
            LOGGER.debug(r.status_code)
            if r.status_code == 200:
                return True
            else:
                time.sleep(60)
        except requests.ConnectionError:
            LOGGER.error("Failed to get respond code from %s - attempt #%s" % (url, counter + 1))

    return False


def write_to_file(to_write):
    f = open('parameters.properties', 'w')
    f.write(to_write)


def switch(region, access_key, secret_key, tag, domain, live_url, blue_alias, green_alias, dry_run=False):
    """
    :description: Rolls back deployment by starting instance with old-app tag and swapping dns entry.
    :param
        ec2_conn: Connection to AWS EC2 service
        old_tag: Dictionary with <tag_name> <tag_value> pair
        dry-run: True or False. If True, it will not make any changes.
    :return: boolean status
    """
    result = True

    # 1. Connects to AWS
    aws_conn = connect_to_aws(region, access_key, secret_key)

    # 2. Check which is live at the moment and which should be stopped.
    live = check_which_is_live(aws_conn.get('route53'), domain, live_url)

    # 3. Swap DNS
    result = swap_live_with_staging(aws_conn, domain, live, live_url, blue_alias, green_alias, dry_run)

    # 4. Stop and tag old one. We will do it after 5 minutes to give chance to safely close all connections.
    time.sleep(300)
    stop_instance(aws_conn, get_env(live, domain), domain, live_url, tag, dry_run)

    return result


def roll_back(region, access_key, secret_key, tag, domain, live_alias, blue_alias, green_alias, dry_run=False):
    """
    :description: Rolls back deployment by starting instance with old-app tag and swapping dns entry.
    :param
        ec2_conn: Connection to AWS EC2 service
        old_tag: Dictionary with <tag_name> <tag_value> pair
        dry-run: True or False. If True, it will not make any changes.
    :return: boolean status
    """
    result = True

    # 1. Connects to AWS
    aws_conn = connect_to_aws(region, access_key, secret_key)

    # 2. Get instance ID of old instance. Check which environment is live.
    old_instance = get_specific_instances(aws_conn.get('ec2'), ''.join(tag.keys()), ''.join(tag.values()),
                                          ['stopped', 'running'])
    current_live = check_which_is_live(aws_conn.get('route53'), domain, live_alias)
    env = get_env(current_live, domain)

    # 3. Do the Magic ;)
    if not old_instance:
        LOGGER.error('No instance with tag %s was found. No chance to roll back Sir!' % ''.join(tag.values()))
    else:
        try:
            if dry_run:
                LOGGER.warning('Instance %s would be started and tagged with %s' % (old_instance, env))
            else:
                # Start old instance
                old_instance[0].start()
                tag_instance(old_instance[0], 'Environment', 'blue' if env == 'green' else 'green')

            # Refresh its public IP as it could change.
            instance_public_ip = wait_for_public_ip(aws_conn.get('ec2'), old_instance[0].id)

            assign_to_staging(aws_conn.get('route53'), domain, current_live, instance_public_ip, live_alias,
                              blue_alias, green_alias, dry_run=False)
            swap_live_with_staging(aws_conn, domain, current_live, live_alias, blue_alias, green_alias, dry_run)
            stop_instance(aws_conn, env, domain, live_alias, tag, dry_run)
        except exception.EC2ResponseError:
            LOGGER.error('Could not start %s instance.' % old_instance)
            result = False

    return result


def deployment_stage(region, access_key, secret_key, srv_name, domain, live_url, blue_alias, green_alias, tag, image_id,
                     ssh_key, sec_group, subnet_id, instance_size, shutdown, dry_run=False):
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
    aws_connections = connect_to_aws(region, access_key, secret_key)

    # 2. Delete old instance which should be stopped
    deleted = delete_old_instance(aws_connections.get('ec2'), tag, dry_run)

    # 3. Check which environment (blue/green) is live
    live = check_which_is_live(aws_connections.get('route53'), domain, live_url)
    if live == blue_alias:
        env = 'green'
    else:
        env = 'blue'

    # 4. If deleted then we can create new instance
    if dry_run:
        # Dry Run
        create_new_instance(aws_connections.get('ec2'), image_id, ssh_key, sec_group, subnet_id, env, srv_name, None,
                            instance_size, shutdown, dry_run)
        assign_to_staging(aws_connections.get('route53'), domain, live, "127.0.0.1", live_url, blue_alias,
                          green_alias, dry_run)

        sys.exit(0)
    elif deleted:
        staging_instance = create_new_instance(aws_connections.get('ec2'), image_id, ssh_key, sec_group, subnet_id, env,
                                               srv_name, None, instance_size, shutdown, dry_run)

    # 5. Assign right dns alias only if we managed to create instance in previous step
    if staging_instance is None:
        # There were some problems with creating new instance
        LOGGER.error('Could not create new instance.')
        sys.exit(1)
    else:
        # Everything was all right. Waiting for Public IP
        if staging_instance[0].ip_address is None:
            # Unfortunately Public IP is not available straight away so we have to wait for it.
            public_ip = wait_for_public_ip(aws_connections.get('ec2'), staging_instance[0].id)

            if public_ip is None:
                LOGGER.error('Cannot get Public IP from instance %s' % staging_instance[0].id)
                sys.exit(1)
        else:
            # Or maybe it is? :)
            public_ip = staging_instance[0].ip_address

        assign_to_staging(aws_connections.get('route53'), domain, live, public_ip, live_url, blue_alias, green_alias,
                          dry_run)

        write_to_file("staging-server = " + public_ip)

    return str(env + "." + domain + ": " + public_ip)

LOGGER = set_up_logging(log_path, file_name)
