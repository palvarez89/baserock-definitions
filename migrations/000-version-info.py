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


'''Migration to Baserock Definitions format version 0.

The format of version 0 is not formally specified, except by the Morph
codebase. It marks the starting point of the work to formalise the Baserock
Definitions format.

'''


import os
import sys

import migrations


TO_VERSION = 0


try:
    if os.path.exists('./VERSION'):
        # This will raise an exception if the VERSION file is invalid, which
        # might be useful.
        migrations.check_definitions_version(TO_VERSION)

        sys.stdout.write("Nothing to do.\n")
        sys.exit(0)
    else:
        sys.stdout.write("No VERSION file found, creating one.\n")
        migrations.set_definitions_version(TO_VERSION)
        sys.exit(0)
except RuntimeError as e:
    sys.stderr.write("Error: %s\n" % e.message)
    sys.exit(1)
