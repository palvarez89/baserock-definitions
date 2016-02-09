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
import sys
import glob
import yaml
import subprocess


'''
Script for checking unpetrify-refs in strata.

Without args this script will check everything in strata/, or each stratum
given on the command-line and will print on stdout whether a chunk has
a missing or non-existent unpetrify-ref and if it fails to check the remote
(missing repo).
'''

strata_dir = "strata"
trove_host = "git.baserock.org"
aliases = {
  'baserock:': 'git://%(trove)s/baserock/',
  'freedesktop:': 'git://anongit.freedesktop.org/',
  'github:': 'git://github.com/',
  'gnome:': 'git://git.gnome.org/',
  'upstream:': 'git://%(trove)s/delta/'
}

def ref_exists(remote, ref):
    output = subprocess.check_output(
        ["git", "ls-remote", remote, str(ref)],
        stderr=subprocess.STDOUT).strip()
    return True if output else False

def get_repo_url(repo):
    remote = repo[:repo.find(':') + 1]
    return repo.replace(remote, aliases[remote])

def definitions_root():
    return subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"]).strip()

def load_yaml_file(yaml_file):
    with open(yaml_file, 'r') as f:
        return yaml.safe_load(f)

def main(args):
    global trove_host, aliases
    opt = next(((i, j.split('=')[1]) for i, j in enumerate(args)
              if j.startswith("--trove-host=")), None)
    if opt:
        trove_host = opt[1]
        del args[opt[0]]
    aliases = {k: v % {'trove': trove_host} for k, v in aliases.iteritems()}

    if args:
        strata = args
    else:
        strata_path = os.path.join(definitions_root(), strata_dir)
        strata = glob.glob("%s/*.morph" % strata_path)

    for stratum in strata:
        path = os.path.relpath(stratum)
        morphology = load_yaml_file(stratum)
        for chunk in morphology['chunks']:
            unpetrify_ref = chunk.get("unpetrify-ref")
            if not unpetrify_ref:
                print ("%s: '%s' has no unpetrify-ref!" %
                       (path, chunk['name']))
                continue
            remote = get_repo_url(chunk['repo'])
            try:
                if not ref_exists(remote, unpetrify_ref):
                    print ("%s: unpetrify-ref for '%s' is not present on the "
                           "remote (%s)!" % (path, chunk['name'], remote))
            except subprocess.CalledProcessError as e:
                print ("%s: failed to ls-remote (%s) for chunk '%s':\n%s" %
                       (path, remote, chunk['name'], e.output.strip()))

if __name__ == "__main__":
    main(sys.argv[1:])
