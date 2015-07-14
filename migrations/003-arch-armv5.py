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


'''Migration to Baserock Definitions format version 3.

In version 3, there were two additions:

    - the 'armv5' architecture
    - the install-essential-files.configure configuration extension

This migration checks that neither of these are in use in the input (version 2)
definitions. Which isn't particularly useful.

'''


import sys
import warnings

import migrations


TO_VERSION = 3


def check_arch(contents, filename):
    assert contents['kind'] == 'system'

    valid = True

    if contents['arch'] == 'armv5':
        warnings.warn(
            "%s uses armv5 architecture that is not understood until version "
            "3." % filename)
        valid = False

    return valid


def check_configuration_extensions(contents, filename):
    assert contents['kind'] == 'system'

    valid = True

    for extension in contents.get('configuration-extensions', []):
        if extension == 'install-essential-files':
            warnings.warn(
                "%s uses install-essential-files.configure extension, which "
                "was not present in morph.git until commit 423dc974a61f1c0 "
                "(tag baserock-definitions-v3)." % filename)
            valid = False

    return valid


try:
    if migrations.check_definitions_version(TO_VERSION - 1):
        safe_to_migrate = migrations.process_definitions(
            kinds=['system'], validate_cb=check_arch)
        safe_to_migrate = migrations.process_definitions(
            kinds=['system'], validate_cb=check_configuration_extensions)

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
