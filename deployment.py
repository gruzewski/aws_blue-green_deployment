__author__ = 'root'

#!/user/bin/python3.4

import argparse
import sys
import aws_lib

parser = argparse.ArgumentParser(description='AWS Blue-Green deployment script.')

parser.add_argument('--dry-run', dest='dry_run', action='store_true')
parser.add_argument('--region', dest='region', type=str, required=True)
parser.add_argument('--access-key', dest='aws_access_key', required=True)
parser.add_argument('--secret-key', dest='aws_secret_key', required=True)
parser.add_argument('--type', dest='instance_size', default='t2.micro')
parser.add_argument('--key', dest='ssh_key', required=True)
parser.add_argument('--image', dest='image_id', required=True)
parser.add_argument('--live-alias', dest='live_alias', required=True, metavar='live.example.com.')
parser.add_argument('--domain', dest='domain', required=True, metavar='example.com.')
parser.add_argument('--server-name', dest='web_srv_name', default='Web Server', type=str)
parser.add_argument('--subnet', dest='subnet_id', required=True, metavar='subnet-XXX')
parser.add_argument('--sec-group', dest='sec_group', nargs='+', required=True, metavar='sg-XXX')
parser.add_argument('--action', dest='action', required=True, metavar='[deploy | switch | roll]')

args = parser.parse_args()

shutdown_behavior = 'stop'

blue_alias = 'blue' + '.' + args.domain
green_alias = 'green' + '.' + args.domain

old_tag = {'Environment': 'old-app'}

if args.action == 'switch':
    print(aws_lib.switch(args.region, args.aws_access_key, args.aws_secret_key, old_tag, args.domain, args.live_alias,
                   blue_alias, green_alias, dry_run=False))
elif args.action == 'roll':
    print(aws_lib.roll_back(args.region, args.aws_access_key, args.aws_secret_key, old_tag, args.domain, args.live_alias, blue_alias,
                      green_alias, dry_run=False))
elif args.action == 'deploy':
    print(aws_lib.deployment_stage(args.region, args.aws_access_key, args.aws_secret_key, args.web_srv_name, args.domain,
                             args.live_alias, blue_alias, green_alias, old_tag, args.image_id, args.ssh_key,
                             args.sec_group, args.subnet_id, args.instance_size, shutdown_behavior, args.dry_run))
else:
    print('--action not set properly.')
    sys.exit(1)