#!/usr/bin/env python
#
# Copyright 2017 Bedag Informatik AG.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import docker
import re
import sys

parser = argparse.ArgumentParser(
    description='Check number of present docker images')
parser.add_argument(
    '--expected',
    '-e',
    default=1,
    type=int,
    metavar='N',
    help='Expected number of tags to match')
parser.add_argument(
    '--regex',
    '-r',
    default='^.*$',
    metavar='REGEX',
    help='Regex to match tags')
parser.add_argument(
    '--negate',
    '-n',
    default=False,
    action='store_true',
    help='Invert the regexp')
parser.add_argument(
    '--mode',
    default='min',
    choices=['min', 'max', 'equal'],
    help=
    'Number of matched tags must be at least, not more than or equal to the number of expected tags'
)
args = vars(parser.parse_args())

client = docker.from_env()
found = [] 
for image in client.images.list():
    tags = image.tags
    if not tags: tags = ['']
    for tag in tags:
        if not (bool(re.search(args['regex'], tag)) == args['negate']):
            found.append(tag)

found.sort()
for tag in found: print(tag)

count = len(found)
result = False
if args['mode'] == 'equal':
    result = count == args['expected']
if args['mode'] == 'min':
    result = count >= args['expected']
if args['mode'] == 'max':
    result = count <= args['expected']

rc = int(not result)
sys.exit(rc)
