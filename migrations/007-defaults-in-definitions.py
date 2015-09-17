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


'''Migration to Baserock Definitions format version 7.

Definitions version 7 adds a file named DEFAULTS which sets the default
build commands and default split rules for the set of definitions in that
repo.

'''


import os
import shutil
import sys
import warnings

import migrations


TO_VERSION = 7



try:
    if migrations.check_definitions_version(TO_VERSION - 1):
        if os.path.exists('DEFAULTS'):
            warnings.warn(
                "DEFAULTS file already exists in these definitions.")
            valid = False
        else:
            shutil.copy(
                'migrations/007-initial-defaults',
                'DEFAULTS')
            valid = True

        if valid:
            migrations.set_definitions_version(TO_VERSION)
            sys.stdout.write("Migration completed successfully.\n")
            sys.exit(0)
        else:
            sys.stderr.write(
                "Migration failed due to one or more warnings.\n")
            sys.exit(1)
    else:
        if not os.path.exists('DEFAULTS'):
            warnings.warn(
                "These definitions are marked as version 7 but there is no "
                "DEFAULTS file.")
        sys.stdout.write("Nothing to do.\n")
        sys.exit(0)
except RuntimeError as e:
    sys.stderr.write("Error: %s\n" % e.message)
    sys.exit(1)
