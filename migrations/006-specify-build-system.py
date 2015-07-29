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


# THIS MIGRATION REQUIRES NETWORK ACCESS TO A BASEROCK GIT CACHE SERVER! If
# you do not have your own Trove, or don't know what a Trove is, it should
# work as-is, provided you have internet access that allows access to
# http://git.baserock.org:8080/.
#
# If you do have your own Trove, change the value of TROVE_HOST below to
# point to it.
#
# This migration uses the same autodetection mechanism that Morph and YBD use
# at build time, in order to fill in the 'build-system' field in strata where
# it is now needed.


'''Migration to Baserock Definitions format version 6.

In definitions version 6, build system autodetection no longer happens. This
means that any chunk that wants to use one of the predefined build systems
(those built into Morph) must say so explicitly, using the 'build-system'
field.

The build-system field for a chunk can now be specified within a stratum that
contains it. Previously you needed to add a .morph file for a chunk in order to
specify its build-system, but we want to avoid needing a .morph file for
components that follow standard patterns.

Previously, if build-system wasn't given, Morph would scan the contents of the
chunk's Git repo and try to autodetect which build system was used. This could
be slow, could fail in confusing ways, and meant that to fully parse
definitions you needed access to some or all of the repos they referenced.

The chosen build-system affects which predefined command sequences are set for
a chunk. It is valid to omit the field if a chunk has its own build commands
defined in a .morph file. When listing the chunks included in a stratum, either
'morph' or 'build-system' must be specified, but not both (to avoid the
possibility of conflicting values).

'''


import requests
import yaml

import logging
import os
import sys

import migrations


TROVE_HOST = 'git.baserock.org'

REPO_ALIASES = {
    'baserock:': 'git://%s/baserock/' % TROVE_HOST,
    'freedesktop:': 'git://anongit.freedesktop.org/',
    'github:': 'git://github.com/',
    'gnome:': 'git://git.gnome.org/',
    'upstream:': 'git://%s/delta/' % TROVE_HOST,
}

GIT_CACHE_SERVER_URL = 'http://%s:8080/' % TROVE_HOST


TO_VERSION = 6


# From ybd.git file repos.py at commit eb3bf397ba729387f0d4145a8df8d3c1f9eb707f

def get_repo_url(repo):
    for alias, url in REPO_ALIASES.items():
        repo = repo.replace(alias, url)
    if repo.endswith('.git'):
        repo = repo[:-4]
    return repo


# Based on morph.git file buildsystem.py at commit a7748f9cdaaf4112c30d7c1.
#
# I have copied and pasted this code here, as it should not be needed anywhere
# once everyone has migrated to definitions version 6.

class BuildSystem(object):
    def used_by_project(self, file_list):
        '''Does a project use this build system?

        ``exists`` is a function that returns a boolean telling if a
        filename, relative to the project source directory, exists or not.

        '''
        raise NotImplementedError()  # pragma: no cover


class ManualBuildSystem(BuildSystem):

    '''A manual build system where the morphology must specify all commands.'''

    name = 'manual'

    def used_by_project(self, file_list):
        return False


class DummyBuildSystem(BuildSystem):

    '''A dummy build system, useful for debugging morphologies.'''

    name = 'dummy'

    def used_by_project(self, file_list):
        return False


class AutotoolsBuildSystem(BuildSystem):

    '''The automake/autoconf/libtool holy trinity.'''

    name = 'autotools'

    def used_by_project(self, file_list):
        indicators = [
            'autogen',
            'autogen.sh',
            'configure',
            'configure.ac',
            'configure.in',
            'configure.in.in',
        ]

        return any(x in file_list for x in indicators)


class PythonDistutilsBuildSystem(BuildSystem):

    '''The Python distutils build systems.'''

    name = 'python-distutils'

    def used_by_project(self, file_list):
        indicators = [
            'setup.py',
        ]

        return any(x in file_list for x in indicators)


class CPANBuildSystem(BuildSystem):

    '''The Perl cpan build system.'''

    name = 'cpan'

    def used_by_project(self, file_list):
        indicators = [
            'Makefile.PL',
        ]

        return any(x in file_list for x in indicators)


class CMakeBuildSystem(BuildSystem):

    '''The cmake build system.'''

    name = 'cmake'

    def used_by_project(self, file_list):
        indicators = [
            'CMakeLists.txt',
        ]

        return any(x in file_list for x in indicators)


class QMakeBuildSystem(BuildSystem):

    '''The Qt build system.'''

    name = 'qmake'

    def used_by_project(self, file_list):
        indicator = '.pro'

        for x in file_list:
            if x.endswith(indicator):
                return True

        return False


build_systems = [
    ManualBuildSystem(),
    AutotoolsBuildSystem(),
    PythonDistutilsBuildSystem(),
    CPANBuildSystem(),
    CMakeBuildSystem(),
    QMakeBuildSystem(),
    DummyBuildSystem(),
]


def detect_build_system(file_list):
    '''Automatically detect the build system, if possible.

    If the build system cannot be detected automatically, return None.
    For ``exists`` see the ``BuildSystem.exists`` method.

    '''
    for bs in build_systems:
        if bs.used_by_project(file_list):
            return bs
    return None


## End of code based on morph.git file buildsystem.py.

def get_toplevel_file_list_from_repo(url, ref):
    '''Try to list the set of files in the root directory of the repo at 'url'.

    '''
    try:
        response = requests.get(
            GIT_CACHE_SERVER_URL + '1.0/trees',
            params={'repo': url, 'ref': ref},
            headers={'Accept': 'application/json'},
            timeout=9)
        logging.debug("Got response: %s" % response)
        toplevel_tree = response.json()['tree']
        toplevel_filenames = toplevel_tree.keys()
    except requests.exceptions.ConnectionError as e:
        raise RuntimeError("Unable to connect to cache server %s: %s" %
                           (GIT_CACHE_SERVER_URL, e.message))
    return toplevel_filenames


def validate_chunk_refs(contents, filename):
    assert contents['kind'] == 'stratum'

    valid = True
    for chunk_ref in contents.get('chunks', []):
        if chunk_ref.get('morph') is None:
            # No chunk .morph file -- this stratum was relying on build-system
            # autodetection here.

            if 'repo' not in chunk_ref:
                warnings.warn("%s: Chunk %s doesn't specify a source repo." %
                              (filename, chunk_ref.get('name')))
                valid = False

            if 'ref' not in chunk_ref:
                warnings.warn("%s: Chunk %s doesn't specify a source ref." %
                              (filename, chunk_ref.get('name')))
                valid = False
    return valid


def move_dict_entry_last(dict_object, key, error_if_missing=False):
    '''Move an entry in a ordered dict to the end.'''

    # This is a hack, I couldn't find a method on the 'CommentedMap' type dict
    # that we receive from ruamel.yaml that would allow doing this neatly.
    if key in dict_object:
        value = dict_object[key]
        del dict_object[key]
        dict_object[key] = value
    else:
        if error_if_missing:
            raise KeyError(key)


def ensure_buildsystem_defined_where_needed(contents, filename):
    assert contents['kind'] == 'stratum'

    changed = False
    for chunk_ref in contents.get('chunks', []):
        if chunk_ref.get('morph') is None:
            # No chunk .morph file -- this stratum was relying on build-system
            # autodetection here.

            chunk_git_url = get_repo_url(chunk_ref['repo'])
            chunk_git_ref = chunk_ref['ref']
            toplevel_file_list = get_toplevel_file_list_from_repo(
                chunk_git_url, chunk_git_ref)

            logging.debug(
                '%s: got file list %s', chunk_git_url, toplevel_file_list)
            build_system = detect_build_system(toplevel_file_list)

            chunk_ref['build-system'] = build_system.name
            move_dict_entry_last(chunk_ref, 'build-depends')

            changed = True
    return changed


try:
    if migrations.check_definitions_version(TO_VERSION - 1):
        success = migrations.process_definitions(
            kinds=['stratum'],
            validate_cb=validate_chunk_refs,
            modify_cb=ensure_buildsystem_defined_where_needed)
        if not success:
            sys.stderr.write(
                "Migration failed due to one or more warnings.\n")
            sys.exit(1)
        else:
            migrations.set_definitions_version(TO_VERSION)
            sys.stderr.write("Migration completed successfully.\n")
            sys.exit(0)
    else:
        sys.stderr.write("Nothing to do.\n")
        sys.exit(0)
except RuntimeError as e:
    sys.stderr.write("Error: %s\n" % e.message)
    sys.exit(1)
