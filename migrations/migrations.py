#!/usr/bin/env python
# Copyright (C) 2015  Codethink Limited
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 2 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program.  If not, see <http://www.gnu.org/licenses/>.


'''Tools for migrating Baserock definitions from one format version to another.

'''


# ruamel.yaml is a fork of PyYAML which allows rewriting YAML files without
# destroying all of the comments, ordering and formatting. The more
# widely-used PyYAML library will produce output totally different to the
# input file in most cases.
#
# See: <https://bitbucket.org/ruamel/yaml>
import ruamel.yaml as yaml

import logging
import os
import warnings


# Uncomment this to cause all log messages to be written to stdout. By
# default they are hidden, but if you are debugging something this might help!
#
# logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)


def pretty_warnings(message, category, filename, lineno,
                    file=None, line=None):
    '''Format warning messages from warnings.warn().'''
    return 'WARNING: %s\n' % (message)

# Override the default warning formatter (which is ugly), and add a filter to
# ensure duplicate warnings only get displayed once.
warnings.simplefilter("once", append=True)
warnings.formatwarning = pretty_warnings



def parse_yaml_with_roundtrip_info(text):
    return yaml.load(text, yaml.RoundTripLoader)

def write_yaml_with_roundtrip_info(contents, stream, **kwargs):
    yaml.dump(contents, stream, Dumper=yaml.RoundTripDumper, **kwargs)



class VersionFileError(RuntimeError):
    '''Represents errors in the version marker file (./VERSION).'''
    pass


class MigrationOutOfOrderError(RuntimeError):
    '''Raised if a migration is run on too old a version of definitions.

    It's not an error to run a migration on a version that is already migrated.

    '''
    pass


def check_definitions_version(from_version, version_file='./VERSION',
                              to_version=None):
    '''Check if migration between 'from_version' and 'to_version' is needed.

    Both 'from_version' and 'to_version' should be whole numbers. The
    'to_version' defaults to from_version + 1.

    This function reads the version marker file specified by 'version_file'.
    Returns True if the version is between 'from_version' and 'to_version',
    indicating that migration needs to be done. Returns False if the version is
    already at or beyond 'to_version'. Raises MigrationOutOfOrderError if the
    version is below 'from_version'.

    If 'version_file' is missing or invalid, it raises VersionFileError. The
    version file is expected to follow the following format:

        version: 1

    '''
    to_version = to_version or (from_version + 1)
    need_to_migrate = False

    if os.path.exists(version_file):
        logging.info("Found version information file: %s" % version_file)

        with open(version_file) as f:
            version_text = f.read()

        if len(version_text) == 0:
            raise VersionFileError(
                "File %s exists but is empty." % version_file)

        try:
            version_info = yaml.safe_load(version_text)
            current_version = version_info['version']

            if current_version >= to_version:
                logging.info(
                    "Already at version %i." % current_version)
            elif current_version < from_version:
                raise MigrationOutOfOrderError(
                    "This tool expects to migrate from version %i to version "
                    "%i of the Baserock Definitions syntax. These definitions "
                    "claim to be version %i." % (
                        from_version, to_version, current_version))
            else:
                logging.info("Need to migrate from %i to %i.",
                             current_version, to_version)
                need_to_migrate = True
        except (KeyError, TypeError, ValueError) as e:
            logging.exception(e)
            raise VersionFileError(
                "Invalid version info: '%s'" % version_text)
    else:
        raise VersionFileError(
            "No file %s was found. Please run the migration scripts in order,"
            "starting from 000-version-info.py." % version_file)

    return need_to_migrate


def set_definitions_version(new_version, version_file='./VERSION'):
    '''Update the version information stored in 'version_file'.

    The new version must be a whole number. If 'version_file' doesn't exist,
    it will be created.

    '''
    version_info = {'version': new_version}
    with open(version_file, 'w') as f:
        # If 'default_flow_style' is True (the default) then the output here
        # will look like "{version: 0}" instead of "version: 0".
        yaml.safe_dump(version_info, f, default_flow_style=False)


def walk_definition_files(path='.', extensions=['.morph']):
    '''Recursively yield all files under 'path' with the given extension(s).

    This is safe to run in the top level of a Git repository, as anything under
    '.git' will be ignored.

    '''
    for dirname, dirnames, filenames in os.walk('.'):
        filenames.sort()
        dirnames.sort()
        if '.git' in dirnames:
            dirnames.remove('.git')
        for filename in filenames:
            for extension in extensions:
                if filename.endswith(extension):
                    yield os.path.join(dirname, filename)


ALL_KINDS = ['cluster', 'system', 'stratum', 'chunk']


def process_definitions(path='.', kinds=ALL_KINDS, validate_cb=None,
                        modify_cb=None):
    '''Run callbacks for all Baserock definitions found in 'path'.

    If 'validate_cb' is set, it will be called for each definition and can
    return True or False to indicate whether that definition is valid according
    a new version of the format. The process_definitions() function will return
    True if all definitions were valid according to validate_cb(), and False
    otherwise.

    If 'modify_cb' is set, it will be called for each definition and can
    modify the 'content' dict. It should return True if the dict was modified,
    and in this case the definition file will be overwritten with the new
    contents. The 'ruamel.yaml' library is used if it is available, which will
    try to preserve comments, ordering and some formatting in the YAML
    definition files.

    If 'validate_cb' is set and returns False for a definition, 'modify_cb'
    will not be called.

    Both callbacks are passed two parameters: a dict containing the contents of
    the definition file, and its filename. The filename is passed so you can
    use it when reporting errors.

    The 'kinds' setting can be used to ignore some definitions according to the
    'kind' field.

    '''
    all_valid = True

    for filename in walk_definition_files(path=path):
        with open(filename) as f:
            text = f.read()

        if modify_cb is None:
            contents = yaml.load(text)
        else:
            contents = parse_yaml_with_roundtrip_info(text)

        if 'kind' in contents:
            if contents['kind'] in kinds:
                valid = True
                changed = False

                if validate_cb is not None:
                    valid = validate_cb(contents, filename)
                    all_valid &= valid

                if valid and modify_cb is not None:
                    changed = modify_cb(contents, filename)

                if changed:
                    with open(filename, 'w') as f:
                        write_yaml_with_roundtrip_info(contents, f, width=80)
        else:
            warnings.warn("%s is invalid: no 'kind' field set." % filename)

    return all_valid
