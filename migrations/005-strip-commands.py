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


'''Migration to Baserock Definitions format version 5.

Version 5 of the definitions format adds a 'strip-commands' field that can
be set in chunk definitions.

Version 5 also allows deployment extensions to live in definitions.git instead
of morph.git. This greatly reduces the interface surface of the Baserock
definitions format specification, because we no longer have to mark a new
version of the definitions format each time an extension in morph.git is added,
removed, or changes its API in any way.

In commit 6f4929946 of git://git.baserock.org/baserock/baserock/definitions.git
the deployment extensions were moved into an extensions/ subdirectory, and the
system and cluster .morph files that referred to them were all updated to
prepend 'extension/' to the filenames. This migration doesn't (re)do that
change.

'''


import sys
import warnings

import migrations


TO_VERSION = 5


def check_strip_commands(contents, filename):
    assert contents['kind'] == 'chunk'

    valid = True

    if 'strip-commands' in contents:
        warnings.warn(
            "%s has strip-commands, which are not valid until version 5" %
            filename)
        valid = False

    return valid


try:
    if migrations.check_definitions_version(TO_VERSION - 1):
        safe_to_migrate = migrations.process_definitions(
            kinds=['chunk'], validate_cb=check_strip_commands)

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
