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

import argparse
import errno
import os
import pipes
import re
import string
import subprocess
import sys
import tempfile

import scriptslib


gpl3_chunks = ("autoconf",
               "autoconf-tarball",
               "automake",
               "bash",
               "binutils",
               "bison",
               "ccache",
               "cmake",
               "flex",
               "gawk",
               "gcc",
               "gdbm",
               "gettext-tarball",
               "gperf",
               "groff",
               "libtool",
               "libtool-tarball",
               "m4-tarball",
               "make",
               "nano",
               "patch",
               "rsync",
               "texinfo-tarball")


def license_file_name(repo_name, sha, licenses_dir):
    license_file = os.path.join(licenses_dir, repo_name + '-' + sha)
    return license_file


def check_license(repo_name, sha, clone_path, license_file):

    licenses_dir = os.path.dirname(license_file)

    try:
        os.makedirs(licenses_dir)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise

    _, license_file_temp = tempfile.mkstemp(dir=licenses_dir)
    with open(license_file_temp,"wb") as out:
        sys.stderr.write("Checking license of '%s' ...\n" % repo_name)
        with open(os.devnull, 'w') as devnull:
            subprocess.check_call("perl scripts/licensecheck.pl -r "
                + pipes.quote(clone_path) + "|cut -d: -f2- | sort -u",
                stdout=out, stderr=devnull, shell=True)

    os.rename(license_file_temp, license_file)
    return license_file


def check_repo_if_needed(name, repo, ref, repos_dir, licenses_dir):
    repo_name = re.split('/|:',repo)[-1]
    if repo_name.endswith(".git"):
        repo_name = repo_name[:-4]

    # Check if ref is sha1 to speedup
    if len(ref) == 40 and all(c in string.hexdigits for c in ref):
        license_file = license_file_name(repo_name, ref, licenses_dir)
        if os.path.isfile(license_file):
            return (repo, license_file)

    clone_path = os.path.join(repos_dir, repo_name)

    if os.path.isdir(clone_path):
        sys.stderr.write("Updating repo '%s' ...\n" % repo_name)
        with open(os.devnull, 'w') as devnull:
            subprocess.check_call([
                "git", "remote", "update", "origin", "--prune"],
                stderr=devnull, stdout=devnull, cwd=clone_path)
            subprocess.check_call(["git", "checkout", ref], stderr=devnull,
                stdout=devnull, cwd=clone_path)
    else:
        sys.stderr.write("Getting repo '%s' ...\n" % repo_name)
        with open(os.devnull, 'w') as devnull:
            subprocess.check_call(["morph", "get-repo", name, clone_path],
                stdout=devnull, stderr=devnull)

    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=clone_path).strip()

    license_file = license_file_name(repo_name, sha, licenses_dir)
    if os.path.isfile(license_file):
        return (repo, license_file)

    return (repo, check_license(repo_name, sha, clone_path, license_file))


def check_stratum(stratum_file, repos_dir, licenses_dir):
    stratum = scriptslib.load_yaml_file(stratum_file)
    license_files = []
    for chunk in stratum['chunks']:

        name = chunk["name"]
        build_mode = chunk.get("build-mode") # Allowed to be None

        # Don't include bootstrap chunks and stripped gplv3 chunks
        if name in gpl3_chunks or build_mode == "bootstrap":
            continue
        repo = chunk["repo"]
        ref = chunk["ref"]
        yield check_repo_if_needed(name, repo, ref, repos_dir, licenses_dir)


def main():

    parser = argparse.ArgumentParser(
        description='Checks licenses of the components of a given System.')
    parser.add_argument('system', metavar='SYSTEM', type=str,
        help='System to check for licenses')
    parser.add_argument('--repos-dir', default="./repos",
            help='DIR to clone all the repos (default ./repos)')
    parser.add_argument('--licenses-dir', default="./licenses",
            help='DIR to store chunk license files (default ./licenses)')

    args = parser.parse_args()

    system = scriptslib.load_yaml_file(args.system)
    license_files = []
    for stratum in system['strata']:
        stratum_file = stratum['morph']
        stratum_path = os.path.join(scriptslib.definitions_root(), stratum_file)
        license_files.extend(check_stratum(stratum_path, args.repos_dir, args.licenses_dir))

    for chunk_repo, chunk_license in license_files:
        try:
            # Print repo name
            sys.stdout.write("%s\n%s\n" % (chunk_repo, '-' * len(chunk_repo)))

            # Print license file of the repo
            with open(chunk_license, 'r') as f:
                for line in f:
                    sys.stdout.write(line)
            sys.stdout.write("\n")
        except IOError:
        # stdout is closed, no point in continuing
        # Attempt to close them explicitly to prevent cleanup problems:
            try:
                sys.stdout.flush()
                sys.stdout.close()
            except IOError:
                pass
            finally:
                exit()


if __name__ == "__main__":
    main()
