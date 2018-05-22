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
import datetime
import docker
import gzip
import os
import re
import sys
import tarfile
import tempfile
import textwrap
import yaml
from io import BytesIO
from jinja2 import Environment, Template
from jinja2.loaders import FileSystemLoader
from jinja2.exceptions import TemplateNotFound
from jinja2.runtime import StrictUndefined
from jinja2.meta import find_undeclared_variables
from docker.utils.json_stream import json_stream
from docker.utils.build import exclude_paths
from docker.utils import mkbuildcontext
from docker.errors import BuildError, DockerException

__version__ = "1.2.0"

class Utils(object):
    @staticmethod
    def merge_dict(a, b, path=[]):
        """Creates a new dict by deep merging b into a"""
        result = a.copy()
        for key in b:
            if key in result:
                if isinstance(result[key], dict) and isinstance(b[key], dict):
                    result[key] = Utils.merge_dict(result[key], b[key],
                                                   path + [str(key)])
                elif result[key] == b[key]:
                    pass  # same leaf value
                else:
                    result[key] = b[key]
            else:
                result[key] = b[key]
        return result

    @staticmethod
    def render_template(template, variables={}, env=None):
        if not env:
            _env = Environment(undefined=StrictUndefined)
        else:
            _env = env

        data = template.render(variables)

        ast = _env.parse(data)
        if find_undeclared_variables(ast):
            _template = _env.from_string(ast)
            return Utils.render_template(_template, variables, _env)
        else:
            return data

    # based on create_archive https://github.com/docker/docker-py/blob/master/docker/utils/utils.py
    @staticmethod
    def tar(root, files, fileobj=None):
        mode = 'a'
        if not fileobj:
            fileobj = tempfile.NamedTemporaryFile()
            mode = 'w'
        t = tarfile.open(mode=mode, fileobj=fileobj)
        for path in files:
            i = t.gettarinfo(os.path.join(root, path), arcname=path)
            if i is None:
                # This happens when we encounter a socket file. We can safely
                # ignore it and proceed.
                continue

            try:
                # We open the file object in binary mode for Windows support.
                with open(os.path.join(root, path), 'rb') as f:
                    t.addfile(i, f)
            except IOError:
                # When we encounter a directory the file object is set to None.
                t.addfile(i, None)
        t.close()
        fileobj.seek(0)
        return fileobj

    # based on mkbuildcontext
    #   https://github.com/docker/docker-py/blob/master/docker/utils/utils.py
    # appends the string given in dockerfile as Dockerfile to the tar file
    # provided as # fileobj in tarfileobj
    @staticmethod
    def tar_dockerfile(dockerfile,tarfileobj):
        dockerfileobj = BytesIO(dockerfile.encode('utf-8'))
        t = tarfile.open(mode='a', fileobj=tarfileobj)
        dfinfo = tarfile.TarInfo('Dockerfile')
        dfinfo.size = len(dockerfileobj.getvalue())
        dockerfileobj.seek(0)
        t.addfile(dfinfo, dockerfileobj)
        t.close()
        tarfileobj.seek(0)
        return tarfileobj

class TagCandidate(object):
    def __init__(self,
                 template,
                 selectors=[],
                 only_primary=False,
                 negate=False):
        self.template = template
        self.selectors = selectors
        self.negate = negate
        self.only_primary = only_primary

    def selected(self, select=None, primary=False):
        if primary or (primary == self.only_primary):
            if select and self.selectors:
                for selector in self.selectors:
                    if re.search(selector, select):
                        return not self.negate
                return self.negate
            else:
                return not self.negate
        else:
            return False

    def render(self, variables={}):
        return Utils.render_template(Template(self.template), variables)

class BuildVariant(object):
    def __init__(self,
                 directory='.',
                 config_file=None,
                 template=None,
                 template_file='Dockerfile.j2',
                 variables={},
                 tags=[]):
        data = {}
        self.config_file = config_file
        self.directory = directory

        try:
            with open(os.path.join(directory, config_file), 'r') as stream:
                try:
                    data = yaml.load(stream)
                except yaml.YAMLError as e:
                    print(e)
        except (AttributeError, TypeError):
            # no config file name provided
            pass
        except IOError:
            # no config file present
            pass

        jinja = Environment(
            loader=FileSystemLoader(directory), undefined=StrictUndefined)

        config = Utils.merge_dict({
            'variables': variables,
            'template_file': template_file,
            'tags': []
        }, data)
        config['tags'] = [
            Utils.merge_dict(tag, {'only_primary': False})
            for tag in config['tags']
        ]

        self.template_file = config['template_file']

        self.tags = {}
        for tag in tags + config['tags']:
            self.tags[tag['template']] = TagCandidate(**tag)
        self.variables = config['variables']

        try:
            self.template = jinja.get_template(config['template_file'])
        except TemplateNotFound:
            self.template = template

    def files(self,exclude=[]):
        dockerignore = os.path.join(self.directory, '.dockerignore')
        _exclude = exclude
        if self.config_file:
            _exclude.append(self.config_file)
        else:
            _exclude.append('image-build.yml')
        _exclude.append(self.template_file)

        if os.path.exists(dockerignore):
            with open(dockerignore, 'r') as f:
                _exclude += list(filter(bool, f.read().splitlines()))

        return sorted(exclude_paths(self.directory, _exclude))

    def render_dockerfile(self, variables):
        return Utils.render_template(self.template,
                                     Utils.merge_dict(self.variables,
                                                      variables))

    def render_tags(self, variables, select=None, primary=False):
        tags = []
        for tag in self.tags:
            if self.tags[tag].selected(select, primary):
                tags.append(self.tags[tag].render(
                    Utils.merge_dict(self.variables, variables)))
        return tags


class Build(object):
    def __init__(self,
                 name,
                 source={},
                 root_dir='.',
                 variants_dir='variants',
                 namespace='image-build',
                 template_file='Dockerfile.j2',
                 variables={},
                 tags=[{
                     'template': 'latest'
                 }]):
        self.name = name
        self.namespace = namespace
        self.source = Utils.merge_dict({
            'name': self.name,
            'tags': ['latest']
        }, source)
        if 'primary' not in self.source:
            self.source['primary'] = self.source['tags'][0]

        self.root_dir = root_dir
        self.variants_dir = os.path.join(self.root_dir, variants_dir)
        self.variants = {}
        self.variants['.'] = BuildVariant(
            template_file=template_file, variables=variables, tags=tags)
        for name in self.source['tags']:

            variant_dir = os.path.join(self.variants_dir, name)
            # only consider directories
            if os.path.isdir(variant_dir):
                self.variants[name] = BuildVariant(
                    directory=variant_dir,
                    config_file='image-build.yml',
                    template=self.variants['.'].template,
                    variables=variables,
                    tags=tags)


class Builder(object):
    def __init__(self, args):
        self.variables = args['variables']
        self.select = args['select']
        self.push = args['push']
        self.save = args['save']
        self.dry_run = args['dry_run']
        self.ignore_empty = args['ignore_empty']
        self.builder = docker.from_env()

        build_config = {}
        with open(args['file'], 'r') as stream:
            try:
                build_config = yaml.load(stream)
            except yaml.YAMLError as e:
                print(e)

        self.builds = []

        if 'builds' in build_config:
            build_config = build_config['builds']

        for config in build_config:
            self.builds.append(
                Build(root_dir=os.path.dirname(args['file']), **config))

    def build_image(self, dockerfileobj):

        response = []
        print("  Docker build output:")
        for line in self.builder.api.build(fileobj=dockerfileobj, rm=True, custom_context=True):
            response.append(line)
            event = list(json_stream([line]))[0]
            if 'stream' in event:
                print("    " + event['stream'].rstrip())
            elif 'status' in event:
                print("    " + event['status'].rstrip())
            elif 'error' in event:
                raise BuildError(event['error'], json_stream(response))

        events = list(json_stream(response))
        if not events:
            raise BuildError('Unknown build error',events)
        event = events[-1]
        if 'stream' in event:
            match = re.search(r'Successfully built ([0-9a-f]+)',
                              event.get('stream', ''))
            if match:
                image_id = match.group(1)
        if image_id:
            return image_id
        raise BuildError(event, events)

    def save_image(self, repository):
        image_data = self.builder.api.get_image(repository)
        with gzip.open("%s.tar.gz" % repository.replace('/', '_'), 'wb') as f:
            for chunk in image_data:
                f.write(chunk)

    def push_image(self, repository, tag=None):
        print("  Docker push output:")
        for line in self.builder.api.push(
                repository=repository, tag=tag, stream=True):
            event = list(json_stream([line]))[0]
            if 'status' in event:
                print("    " + event['status'])
            elif 'error' in event:
                raise DockerException(event['error'])

    def build(self):
        root_excludes = []
        for build in self.builds:
            root_excludes.append(build.variants_dir)
            root_excludes.append(build.variants['.'].template_file)

        for build in self.builds:
            timestamp = '{:%Y%m%d%H%M%S}'.format(datetime.datetime.now())
            repository = os.path.join(build.namespace, build.name)
            print("Build: {0}".format(build.name))
            print("  Repository: {0}\n".format(repository))
            try:
                has_applicable_tags = False
                for source_tag in build.source['tags']:
                    build_variables = self.variables
                    primary = source_tag == build.source['primary']
                    build_variables['_source'] = { 'name': build.source['name'], 'tag': source_tag, 'primary': primary }
                    build_variables['_dest'] = { 'name': build.name, 'namespace': build.namespace }
                    build_variables['_timestamp'] = timestamp
                    tags = build.variants['.'].render_tags(build_variables,
                                                           self.select, primary)
                    build_variables['_dest']['tags'] = tags
                    dockerfile = build.variants['.'].render_dockerfile(
                        build_variables)
                    root_files = build.variants['.'].files(root_excludes)

                    if not self.dry_run:
                        context = Utils.tar(build.variants['.'].directory,root_files)

                    files = []
                    if source_tag in build.variants:
                        tags = build.variants[source_tag].render_tags(
                            build_variables, self.select, primary)
                        build_variables['_dest']['tags'] = tags
                        build_variables['_base'] = dockerfile
                        dockerfile = build.variants[source_tag].render_dockerfile(
                            build_variables)
                        files = build.variants[source_tag].files()
                        if not self.dry_run:
                            context = Utils.tar(build.variants[source_tag].directory,files,context)
                    if not self.dry_run:
                        context = Utils.tar_dockerfile(dockerfile,context)

                    if self.dry_run:
                        print("  Source: %s:%s" % (build.source['name'],
                                                   source_tag))
                        print("    Tags:")
                        print('\n'.join('      ' + tag for tag in tags))
                        print("    Base files:")
                        print('\n'.join('      ' + f for f in root_files))
                        print("    Variant files:")
                        print('\n'.join('      ' + f for f in files))
                        print('    Dockerfile:')
                        print(''.join('      ' + line
                                      for line in dockerfile.splitlines(True)))
                        print
                    else:
                        if len(tags) > 0:
                            image_id = self.build_image(context)
                            image = self.builder.images.get(image_id)
                            for tag in tags:
                                image.tag(repository, tag=tag)
                            has_applicable_tags = True
                        else:
                            print(
                                "  No applicable destination tags for source tag %s, skipping build phase"
                                % source_tag)

                if has_applicable_tags:
                    if self.push and not self.dry_run:
                        try:
                            self.push_image(repository)
                        except DockerException as err:
                            print("Failed to push image: {0}".format(err))
                            return False
                    if self.save and not self.dry_run:
                        try:
                            self.save_image(repository)
                        except Exception as err:
                            print("Failed to push image: {0}".format(err))
                            return False
                else:
                    if not self.ignore_empty:
                        print("The build has no applicable destination for any source tag!")
                        return False
            except BuildError as err:
                print("Failed to build image: {0}".format(err))
                return False
        return True


class StoreNameValuePair(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        try:
            kv = dict(x.split('=') for x in values)
            setattr(namespace, self.dest, kv)
        except ValueError:
            raise ValueError('Arguments must have the format KEY=VALUE')


parser = argparse.ArgumentParser(description='Docker image build helper.')
parser.add_argument(
    '--dry-run',
    '-d',
    default=False,
    action='store_true',
    help='do nothing, just print Dockerfiles and tags')
parser.add_argument(
    '--push',
    '-p',
    default=False,
    action='store_true',
    help='push after building')
parser.add_argument(
    '--export',
    '-e',
    dest='save',
    default=False,
    action='store_true',
    help='export image to file after building')
parser.add_argument(
    '--ignore-empty',
    '-i',
    dest='ignore_empty',
    default=False,
    action='store_true',
    help='Ignore builds without applicable destination tags')
parser.add_argument(
    '--file',
    '-f',
    metavar='file',
    default='image-build.yml',
    help='name of the build file')
parser.add_argument(
    '--select',
    '-s',
    metavar='select',
    help='string to select which tags to add/push/export')
parser.add_argument(
    '-v',
    '--version',
    action='version',
    version='%(prog)s {version}'.format(version=__version__))
parser.add_argument(
    'variables',
    metavar='KEY=VALUE',
    nargs='*',
    action=StoreNameValuePair,
    help='variables to add to the build process')
args = parser.parse_args()

builder = Builder(vars(args))
sys.exit(not builder.build())
