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


'''Migration to Baserock Definitions format version 2.

In version 2, the processing of the 'morph:' field within stratum .morph files
became more strict.  This migration checks whether definitions are valid
according to version 2 of the format.

'''


import os
import sys
import warnings

import migrations


TO_VERSION = 2


def check_missing_chunk_morphs(contents, filename):
    assert contents['kind'] == 'stratum'

    valid = True

    for chunk_ref in contents.get('chunks', []):
        if 'morph' in chunk_ref:
            chunk_path = os.path.join('.', chunk_ref['morph'])
            if not os.path.exists(chunk_path):
                # There's no way we can really fix this, so
                # just warn and say the migration failed.
                warnings.warn(
                    "%s points to non-existant file %s" %
                    (contents['name'], chunk_ref['morph']))
                valid = False

    return valid


try:
    if migrations.check_definitions_version(TO_VERSION - 1):
        safe_to_migrate = migrations.process_definitions(
            kinds=['stratum'], validate_cb=check_missing_chunk_morphs)

        if not safe_to_migrate:
            sys.stderr.write(
                "Migration failed due to one or more warnings.\n")
            sys.exit(1)
        else:
            migrations.set_definitions_version(TO_VERSION)
            sys.stdout.write("Migration completed successfully.\n")
            sys.exit(0)
    else:
        sys.stdout.write("Nothing to do.\n")
        sys.exit(0)
except RuntimeError as e:
    sys.stderr.write("Error: %s\n" % e.message)
    sys.exit(1)
