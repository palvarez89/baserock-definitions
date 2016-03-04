#!/usr/bin/env python
# Copyright (C) 2016 Codethink Limited
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

import os
import glob
import argparse
import subprocess

import scriptslib


'''
Script for checking unpetrify-refs in strata.

Without args this script will check everything in strata/, or each stratum
given on the command-line and will print on stdout whether a chunk has
a missing or non-existent unpetrify-ref and if it fails to check the remote
(missing repo).
'''

strata_dir = "strata"

def ref_exists(remote, ref):
    output = subprocess.check_output(
        ["git", "ls-remote", remote, str(ref)],
        stderr=subprocess.STDOUT).strip()
    return True if output else False

def main():
    parser = argparse.ArgumentParser(
        description="Sanity checks unpetrify-refs in Baserock strata")
    parser.add_argument("--trove-host", default="git.baserock.org",
                        help="Trove host to map repo aliases to")
    parser.add_argument("strata", nargs="*", metavar="STRATA",
                        help="The strata to check (checks all by default)")
    args = parser.parse_args()

    if args.strata:
        strata = args.strata
    else:
        strata_path = os.path.join(scriptslib.definitions_root(), strata_dir)
        strata = glob.glob("%s/*.morph" % strata_path)

    for stratum in strata:
        path = os.path.relpath(stratum)
        morphology = scriptslib.load_yaml_file(stratum)
        for chunk in morphology['chunks']:
            unpetrify_ref = chunk.get("unpetrify-ref")
            if not unpetrify_ref:
                print ("%s: '%s' has no unpetrify-ref!" %
                       (path, chunk['name']))
                continue
            remote = scriptslib.parse_repo_alias(chunk['repo'], args.trove_host)
            try:
                if not ref_exists(remote, unpetrify_ref):
                    print ("%s: unpetrify-ref for '%s' is not present on the "
                           "remote (%s)!" % (path, chunk['name'], remote))
            except subprocess.CalledProcessError as e:
                print ("%s: failed to ls-remote (%s) for chunk '%s':\n%s" %
                       (path, remote, chunk['name'], e.output.strip()))

if __name__ == "__main__":
    main()
