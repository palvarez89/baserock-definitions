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


'''Migration to Baserock Definitions format version 1.

In version 1, the 'build-depends' parameter was made optional. It was
previously mandatory to specify 'build-depends' for a chunk, even if it was an
empty list.

'''


import sys
import warnings

import migrations


TO_VERSION = 1


def check_empty_build_depends(contents, filename):
    assert contents['kind'] == 'stratum'

    valid = True
    for chunk_ref in contents.get('chunks', []):
        if 'build-depends' not in chunk_ref:
            chunk_ref_name = chunk_ref.get('name', chunk_ref.get('morph'))
            warnings.warn(
                "%s:%s has no build-depends field, which "
                "is invalid in definitions version 0." %
                (contents['name'], chunk_ref_name))
            valid = False

    return valid


def remove_empty_build_depends(contents, filename):
    assert contents['kind'] == 'stratum'

    changed = False
    for chunk_ref in contents.get('chunks', []):
        if 'build-depends' in chunk_ref:
            if len(chunk_ref['build-depends']) == 0:
                del chunk_ref['build-depends']
                changed = True

    return changed


try:
    if migrations.check_definitions_version(TO_VERSION - 1):
        success = migrations.process_definitions(
            path='.', kinds=['stratum'],
            validate_cb=check_empty_build_depends,
            modify_cb=remove_empty_build_depends)
        if success:
            migrations.set_definitions_version(TO_VERSION)
            sys.stdout.write("Migration completed successfully.\n")
            sys.exit(0)
        else:
            sys.stderr.write("Migration failed due to warnings.\n")
            sys.exit(1)
    else:
        sys.stdout.write("Nothing to do.\n")
        sys.exit(0)
except RuntimeError as e:
    sys.stderr.write("Error: %s\n" % e.message)
    sys.exit(1)
