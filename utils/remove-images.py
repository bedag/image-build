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

parser = argparse.ArgumentParser(
    description='Remove all tags matching a given regex')
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
args = vars(parser.parse_args())

client = docker.from_env()
images = client.images
for image in images.list():
    tags = image.tags
    if not tags: tags = ['']
    for tag in tags:
        if not (bool(re.search(args['regex'], tag)) == args['negate']):
            if tag:
                images.remove(image=tag, force=True)
            else:
                images.remove(image=image.id, force=True)
