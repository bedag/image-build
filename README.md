# Image-build

`image-build` is a tool to build multiple Docker images with multiple tags at once.

It uses [Jinja templates](http://jinja.pocoo.org/docs/dev/templates/) to create image tags and dynamic Dockerfiles. The tool is specifically meant to be used in build systems like Gitlab-CI, as it allows for easy selection of what tags to be create during a build based on arbitrary selectors, for example git branch names or git tags (see the description of "tag templates" below).

## Getting started

```
pip install -r requirements.txt
```

`image-build.py` expects a valid Docker configuration in `~/.docker/config.json` and supports the environment variables described in
[docker-py's from_env()](https://docker-py.readthedocs.io/en/stable/client.html#docker.client.from_env).

## CLI Interface

```
usage: image-build.py [-h] [--dry-run] [--push] [--export] [--ignore-empty]
                       [--file file] [--select select] [-v]
                       [KEY=VALUE [KEY=VALUE ...]]

Docker image build helper.

positional arguments:
  KEY=VALUE             variables to add to the build process

optional arguments:
  -h, --help            show this help message and exit
  --dry-run, -d         do nothing, just print Dockerfiles and tags
  --push, -p            push after building
  --export, -e          export image to file after building
  --ignore-empty, -i    Ignore builds without applicable destination tags
  --file file, -f file  name of the build file
  --select select, -s select
                        string to select which tags to add/push/export
  -v, --version         show program's version number and exit
```

## Iocker-build.yml structure

The general structure of a `image-build.yml` file is as follows

```
---
builds:
  - name:
    source:
      name:
      tags: []
      primary:
    variants_dir:
    template_file:
    namespace:
    tags:
      - template:
        selectors: []
        negate: true|false
        only_primary: true|false
    variables: {}
```

All fields without explicit datatype are strings. The `selectors` field is a list of strings evaluated as regexp. The `template` field supports JinJa
templates. The `name` field is not required to be unique (it is for example possible to have multiple builds called "alpine" if you want to build the
same image multiple times but for different namespaces).

For more details on how these fields can be used, check the `examples/` directory.

## Pre-defined variables

* `_source`: information about the source image
  * `tag`: the Docker image tag of the source image on which the current variant build is based. This is one of the tags defined in `source['tags']` for this build
  * `name`: the full name of the source repository, as defined in `source['name']` for this build
* `_dest`: information about the destination image
  * `name`: the name of the destination image, without the namespace and tag part. This is effectively the build name
  * `namespace`: the value of `namespace` for this build
  * `tags`: all tags being rendered for the current variant build
* `_base`: the rendered main Dockerfile (the one located next to the `image-build.yml`). Only available in Dockerfiles for variants.
* `_timestamp`: the timestamp of the build, formatted as `%Y%m%d%H%M%S` (see [datetime](https://docs.python.org/2/library/datetime.html#strftime-and-strptime-behavior))

## Glossary

* `build`: A single entry in `image-build.yml` consisting of at least a `name`, a `source` section and a `namespace`. Most will have a `tags` and `variables` section too. Each build creates multiple images, based on one or more tags of a given source image (as defined in the `source` setting of a build). Multiple builds can be defined in one `image-build.yml`.

* `build variant`: All settings used to create an image based on one (and only one) of the source tags assigned to a build. A build defining 3 tags in `source['tags']` consists of 3 build variants. Each build variant can have its own `image-build.yml`, `Dockerfile.j2` and files to include in an image. Individual variant settings must be placed in `<variants_dir>/<variant>` (the default for `<variants_dir>` is "variants/", but can be overriden on a per-build basis). `<variant>` is the name of the source tag. Settings defined for a specific build variant are only valid in this variant and do not propagate "up" to the main settings (e.g. to build variants without specific settings).

* `variables`: Variables can be used for Jinja templating. They are either defined in the build (variant) settings or can be provided on the command line (see `image-build.py --help`). Variables provided on the command line override variables defined in the `image-build.yml` files.

* `tag template`: Tag templates define what tags an image created by a build variant gets assigned. A tag template consists of a `template string`, a list of (optional) `selectors`, a setting to only render it for primary source tags and a setting to negate the pattern matching for the selectors. The template string is a normal Jinja template, which will be used to tag the image created by a build variant. `selectors` are a list of regexp which define if a tag template should be rendered. When executing `image-build.py`, the `-s` option can be used to set a selector string. This string is matched against all selectors of a tag template. The tag template is only rendered for the current build variant if the selector string matches one or more of the selectors. The function of the selectors can be negated by setting `negate: true`, meaning that a tag template is only rendered if the selector string matches NONE of the selectors. If a tag template has the `only_primary: true` setting, it will only be rendered if the selectors (or don't, depending on the `negated` setting) match and the current build variant is the one defined in `source['primary']`.

* `primary tag`: one of the tags in `source['tags']` can be set as the primary tag. This can be used to mark certain tag templates to be rendered only for this specific build variant. This is mainly used for default tag templates that do not use the '_source['tag']' variable in their template string. A tag template for "latest" would be an example for this, as only one build variant can receive this (or any other static) tag.
