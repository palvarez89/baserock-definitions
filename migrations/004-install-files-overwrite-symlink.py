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

'''Migration to Baserock Definitions format version 4.

This change to the format was made to work around a bug in a deployment
extension present in morph.git.

Automated migration is not really possible for this change, and unless you
are experiencing the install-files.configure extension crashing, you can ignore
it completely.

We have now moved all .configure and .write extensions into the definitions.git
repository. Changes like this no longer require a marking a new version of the
Baserock definitions format in order to prevent build tools crashing.

Morph commit c373f5a403b0ec introduces version 4 of the definitions format. In
older versions of Morph the install-files.configure extension would crash if it
tried to overwrite a symlink. This bug is fixed in the version of Morph that
can build definitions version 4.

If you need to overwrite a symlink at deploytime using install-files.configure,
please use VERSION to 4 or above in your definitions.git repo so older versions
of Morph gracefully refuse to deploy, instead of crashing.

'''


import sys

import migrations


TO_VERSION = 4


try:
    if migrations.check_definitions_version(TO_VERSION - 1):
        migrations.set_definitions_version(TO_VERSION)
        sys.stdout.write("Migration completed successfully.\n")
        sys.exit(0)
    else:
        sys.stdout.write("Nothing to do.\n")
        sys.exit(0)
except RuntimeError as e:
    sys.stderr.write("Error: %s\n" % e.message)
    sys.exit(1)
