#!/usr/bin/env python
# Copyright (C) 2016  Codethink Limited
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

'''Migration to Baserock Definitions format version 8.

In definitions version 8, submodules must be declared explicitly for all chunks
that contains a .gitmodules file in their root. This is so that mirrored source
repositories don't need to maintain branches that point to the mirrored
submodules, and can instead translate these at build time.

'''

import requests
import string
import logging
import re
import os
import sys
import warnings
import migrations
from subprocess import call, Popen
from ConfigParser import RawConfigParser
from StringIO import StringIO


TROVE_HOST = 'git.baserock.org'

REPO_ALIASES = {
    'baserock:': 'git://%s/baserock/' % TROVE_HOST,
    'freedesktop:': 'git://anongit.freedesktop.org/',
    'github:': 'git://github.com/',
    'gnome:': 'git://git.gnome.org/',
    'upstream:': 'git://%s/delta/' % TROVE_HOST,
}

GIT_CACHE_SERVER_URL = 'http://%s:8080/' % TROVE_HOST

FAIL_ON_REMOTE_CACHE_ERRORS = False


TO_VERSION = 8 


# From ybd.git file repos.py at commit eb3bf397ba729387f0d4145a8df8d3c1f9eb707f

def get_repo_url(repo):
    for alias, url in REPO_ALIASES.items():
        repo = repo.replace(alias, url)
    if repo.endswith('.git'):
        repo = repo[:-4]
    return repo

def get_repo_name(repo):
    ''' Convert URIs to strings that only contain digits, letters, _ and %.
        NOTE: this naming scheme is based on what lorry uses
    '''
    valid_chars = string.digits + string.ascii_letters + '%_'
    transl = lambda x: x if x in valid_chars else '_'
    return ''.join([transl(x) for x in get_repo_url(repo)])


## End of code based on ybd repos.py 

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
        try:
            response.raise_for_status()
            toplevel_tree = response.json()['tree']
        except Exception as e:
            raise RuntimeError(
                "Unexpected response from server %s for repo %s: %s" %
                (GIT_CACHE_SERVER_URL, url, e.message))
        toplevel_filenames = toplevel_tree.keys()
    except requests.exceptions.ConnectionError as e:
        raise RuntimeError("Unable to connect to cache server %s while trying "
                           "to query file list of repo %s. Error was: %s" %
                           (GIT_CACHE_SERVER_URL, url, e.message))
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

def submodules_to_dict(path):
    with open(os.path.join(path, '.gitmodules'), "r") as gitfile:
        content = '\n'.join([l.strip() for l in gitfile.read().splitlines()])
    io = StringIO(content)
    parser = RawConfigParser()
    parser.readfp(io)
    stuff = {}
    for section in parser.sections():
       submodule = re.sub(r'submodule "(.*)"', r'\1', section)
       url = parser.get(section, 'url')
       path = parser.get(section, 'path')
       stuff[submodule] = {'url': url}
    return stuff

def add_submodules_to_strata(contents, filename):
    assert contents['kind'] == 'stratum'

    changed = False
    for chunk_ref in contents.get('chunks', []):
        chunk_git_url = get_repo_url(chunk_ref['repo'])
        chunk_git_ref = chunk_ref['ref']

        if 'submodules' in chunk_ref:
            continue
        try:
            toplevel_file_list = get_toplevel_file_list_from_repo(
                chunk_git_url, chunk_git_ref)
        except Exception as e:
            message = (
                "Unable to look up repo %s on remote Git server %s. Check that "
                "the repo URL is correct." % (chunk_git_url, TROVE_HOST))
            warning = (
                "If you are using a Trove that is not %s, please edit the "
                "TROVE_HOST constant in this script and run it again." %
                TROVE_HOST)
            if FAIL_ON_REMOTE_CACHE_ERRORS:
                raise RuntimeError(message + " " + warning)
            else:
                warnings.warn(message)
                warnings.warn(warning)
                continue 

        logging.debug(
            "%s: got file list %s", chunk_git_url, toplevel_file_list)

        path = get_repo_name(chunk_git_url)
        if u'.gitmodules' in toplevel_file_list:
            call(['git', 'clone', chunk_git_url, path])
            p_co = Popen(['git', 'checkout', chunk_git_ref], cwd=path)
            p_co.wait()
            chunk_ref['submodules'] = submodules_to_dict(path)
            call(['rm', '-rf', path])
            changed = True

    return changed
try:
    if migrations.check_definitions_version(TO_VERSION - 1):
        success = migrations.process_definitions(
            kinds=['stratum'],
            validate_cb=validate_chunk_refs,
            modify_cb=add_submodules_to_strata)
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
