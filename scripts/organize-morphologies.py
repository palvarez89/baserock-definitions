#!/usr/bin/env python
# Copyright (C) 2014-2016 Codethink Limited
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
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

import json
import morphlib
import os
import subprocess
import sys
import urllib
import urllib2
import urlparse
import yaml
import re
import errno

''' organize-morphologies.py:
Tool for organizing morphologies in definitions.

This script will move:
  - cluster morphologies into clusters directory
  - system morphologies into systems directory
  - stratum morphologies into strata directory

This script will download the chunk morphologies for every stratum
and placed into strata/stratum_which_the_chunk_belongs_to directory.

It also modifies the morphologies fields which points to some morpholgy
which has been moved.
'''


def make_request(path):
    server_url = 'http://git.baserock.org:8080/'
    url = urlparse.urljoin(server_url, '/1.0/%s' % path)
    handle = urllib2.urlopen(url)
    return handle.read()

def quote(*args):
    return tuple(urllib.quote(string) for string in args)

def cat_file(repo, ref, filename):
    return make_request('files?repo=%s&ref=%s&filename=%s' %
                         quote(repo, ref, filename))

# NOTE: This function reimplement part of morphlib's loader
def sanitise_morphology_path(morph_field, morph_kind, belongs_to='None'):
    '''This function receives the name or the morph field of one morphology
    and returns the path of the morphology depending on the name, kind and
    if it belongs to other morphologies.
    '''
    # Dictionary which match morphology's kind and morphology's
    # directory in definitions.git
    morph_dir = { 'chunk': 'chunks', 'stratum': 'strata',
                  'system':'systems', 'cluster': 'clusters'}
    # For chunks morphologies we need to know to which stratums
    # belongs this chunk.
    if morph_kind == 'chunk':
        if belongs_to == '':
            raise morphlib.Error('Chunk morphologies need the stratum name'
                                 'to create the path. Please add the stratum'
                                 'which belongs this morphology')
        # Get the name of the chunk which we assume is at the end
        # of the morph file
        if '/' in morph_field:
            morph_field = os.path.basename(morph_field)

        # Add the stratum name to the chunk name
        morph_field = os.path.join(belongs_to, morph_field)

        # Reset the kind to stratum because chunk contains stratum
        # name in its path.
        morph_kind = 'stratum'

    # Add the morphology path to the morph field.
    if not morph_field.startswith(morph_dir[morph_kind]):
        morph_field = os.path.join(morph_dir[morph_kind], morph_field)

    # Add the morphology suffix if the morphology.
    if not morph_field.endswith('.morph'):
        morph_field = morph_field + '.morph'

    return morph_field

def create_directory(name, path):
    directory = os.path.join(path, name)
    try:
        os.makedirs(directory)
    except OSError as err:
        if err.errno != errno.EEXIST:
            raise err
        else:
            pass
    return directory

def move_file(morph, directory, path, loader):
    if not morph.filename.startswith(directory):
        filename = os.path.basename(morph.filename)
        new_location = os.path.join(path, filename)
        print '\nMoving %s into %s' % (filename, new_location)
        subprocess.call(['git', 'mv', morph.filename, new_location])
        morph.filename = new_location
        loader.unset_defaults(morph)
        loader.save_to_file(morph.filename, morph)

def load_and_fix_chunk(chunk_str, loader, name):
    try:
        chunk_morph = loader.load_from_string(chunk_str)
    except morphlib.morphloader.InvalidFieldError as err:
        if "comments" in str(err):
            # This error is caused because there are old morphologies which
            # contain the field "comments" instead of "description".
            # Replacing "comments" field by "description" will allow the morphology
            # to pass parse_morphology_text check and ready to be written to a file.
            fixed_chunk = loader.parse_morphology_text(chunk_str, name)
            fixed_chunk['description'] = fixed_chunk.pop('comments')
            print "WARNING: Invalid 'comments' field in " \
                  "%s corrected to 'description'" % name
            chunk_morph = load_and_fix_chunk(str(fixed_chunk), loader, name)
        elif "buildsystem" in str(err):
            # This error is caused because a typo in a morphology which
            # has a field "buildsystem" instead of "build-system".
            fixed_chunk = loader.parse_morphology_text(chunk_str, name)
            fixed_chunk['build-system'] = fixed_chunk.pop('buildsystem')
            print "WARNING: Invalid 'buildsystem' field in %s" \
                          "corrected to 'build-system'" % name
            chunk_morph = load_and_fix_chunk(str(fixed_chunk), loader, name)
        else:
            print "ERROR: %s in chunk %s" %(err, name)
            raise err
    except morphlib.morphloader.MorphologyNotYamlError as err:
        print "WARNING: %s in chunk %s is not valid YAML, " \
              "attempting to fix..." %(err, name)
        # This error is caused because there are old morphologies written
        # in JSON which contain '\t' characters. When try to load this
        # kind of morphologies load_from_string fails when parse_morphology_text.
        # Removing this characters will make load_from_string to load the morphology
        # and translate it into a correct yaml format.
        fixed_chunk = chunk_str.replace('\t','')
        print "INFO: %s successfully fixed" % name
        chunk_morph = load_and_fix_chunk(fixed_chunk, loader, name)
    return chunk_morph

def move_clusters(morphs, path, loader):
    kind = 'system'
    directory = 'clusters'
    # Move cluster morphologies to clusters folder fixing their dependent
    # morphologies which are systems.
    full_path = create_directory(directory, path)
    for morph in morphs:
        all_systems = morph['systems'][:]
        for system in morph['systems']:
            all_systems.extend(system.get('subsystems', []))
        # Add the correct path to the morph fields for systems and subsystems
        for field in all_systems:
            field['morph'] = sanitise_morphology_path(field['morph'], kind)
        move_file(morph, directory, full_path, loader)

def move_systems(morphs, path, loader):
    kind = 'stratum'
    directory = 'systems'
    # Move system morphologies to systems folder fixing their dependent
    # morphologies which are strata.
    full_path = create_directory(directory, path)
    for morph in morphs:
        # Add name field and the correct path to the stratum on the morph
        # fields in strata.
        for field in morph['strata']:
            field['name'] = os.path.basename(field['morph'])
            field['morph'] = sanitise_morphology_path(field['morph'], kind)
        move_file(morph, directory, full_path, loader)

def download_chunks(morph, loader):
    # Download chunks morphologies defined on the stratum and
    # add them to the directory tree.
    for chunk in morph['chunks']:
        name = chunk['name'] + '.morph'
        try:
            chunk['morph'] = sanitise_morphology_path(chunk['morph'], 'chunk', morph['name'])
        except KeyError as err:
            if 'morph' in str(err):
                chunk['morph'] = sanitise_morphology_path(chunk['name'], 'chunk', morph['name'])
            else:
                raise err
        ref = chunk['ref']
        repo = scriptslib.parse_repo_alias(chunk['repo'])
        try:
            print "\nDownloading %s from %s into %s" %(name, repo, chunk['morph'])
            chunk_str = cat_file(repo, ref, name)
        except urllib2.HTTPError as err:
            # If there is no morphology in the repository we assume that the morphology
            # system will be autodetected, so we don't have to create a new one
            # unless we shut down the autodetecting system (fallback system).
            if err.code == 404:
                print 'INFO: Morph will fall-back to build-time' \
                      'autodetection for %s' %(name)
                # Remove morph field from autodetected chunks
                del chunk['morph']
        else:
            loaded_chunk = load_and_fix_chunk(chunk_str, loader, name)
            loader.unset_defaults(loaded_chunk)
            loader.save_to_file(chunk['morph'], loaded_chunk)

def move_strata(morphs, path, loader):
    # Create strata directory
    strata_dir = 'strata/'
    strata_path = create_directory(strata_dir, path)
    for morph in morphs:
        # Create stratum directory where downloading its chunks.
        stratum_path = strata_path + morph['name']
        stratum_dir = create_directory(stratum_path, path)

        # Download chunks which belongs to the stratum
        download_chunks(morph, loader)

        # Add to build-depends the correct path to the dependent stratum morphologies.
        for build_depends in morph['build-depends']:
            build_depends['morph'] = sanitise_morphology_path(build_depends['morph'], 'stratum')
        # Move stratum morphologies to strata
        move_file(morph, strata_dir, strata_path, loader)

def main():
    # Load all morphologies in the definitions repo
    sb = morphlib.sysbranchdir.open_from_within('.')
    loader = morphlib.morphloader.MorphologyLoader()
    morphs = [m for m in sb.load_all_morphologies(loader)]

    # Clasify the morphologies regarding of their kind field
    morphologies = { kind: [m for m in morphs if m['kind'] == kind]
                     for kind in ('chunk', 'stratum', 'system', 'cluster') }

    for kind, morphs in morphologies.iteritems():
        print 'There are: %d %s.\n' %(len(morphs), kind)

    # Get the path from definitions repo
    definitions_repo = sb.get_git_directory_name(sb.root_repository_url)

    # Move the morphologies to its directories
    move_clusters(morphologies['cluster'], definitions_repo, loader)
    move_systems(morphologies['system'], definitions_repo, loader)
    move_strata(morphologies['stratum'], definitions_repo, loader)

main()
