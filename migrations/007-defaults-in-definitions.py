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
import sys
import warnings

import migrations


TO_VERSION = 7


REFERENCE_DEFAULTS = '''# Baserock definitions defaults
# =============================
#
# The DEFAULTS file is treated specially by Baserock build tools.
#
# For more information, see: <http://wiki.baserock.org/definitions/current>.


# Predefined build commands
# -------------------------
#
# Common patterns in build instructions can be defined here, which can save
# users from having to write lots of similar-looking chunk .morph files.
#
# There are pre- and post- variants for each set of commands. These exist so
# you can add more commands without having to copy the defaults. For example,
# to create an extra symlink after running `make install`, you can use
# post-install-commands. Since these exist as a way of extending the defaults,
# you cannot set default values for the pre- and post- commands.
#
# The set of environment variables available when these commands are executed
# is not formally specified right now, but you can assume PREFIX, DESTDIR and
# MORPH_ARCH are all set.
#
build-systems:
  manual:
    # The special, default 'no-op' build system.
    configure-commands: []
    build-commands: []
    install-commands: []
    strip-commands: []

  autotools:
    # GNU Autoconf and GNU Automake, or anything which follow the same pattern.
    #
    # See also: https://github.com/cgwalters/build-api/blob/master/build-api.md
    configure-commands:
    - |
      export NOCONFIGURE=1;
      if [ -e autogen ]; then ./autogen;
      elif [ -e autogen.sh ]; then ./autogen.sh;
      elif [ ! -e ./configure ]; then autoreconf -ivf;
      fi
      ./configure --prefix="$PREFIX"
    build-commands:
    - make
    install-commands:
    - make DESTDIR="$DESTDIR" install
    strip-commands:
      # TODO: Make idempotent when files are hardlinks
      # Strip all ELF binary files that are executable or named like a library.
      # .so files for C, .cmxs for OCaml and .node for Node.
      #
      # The file name and permissions checks are done with the `find` command before
      # the ELF header is checked with the shell command, because it is a lot cheaper
      # to check the mode and file name first, because it is a metadata check, rather
      # than a subprocess and a file read.
      #
      # `file` is not used, to keep the dependency requirements down.
      - &generic-strip-command |
        find "$DESTDIR" -type f
          '(' -perm -111 -o -name '*.so*' -o -name '*.cmxs' -o -name '*.node' ')'
          -exec sh -ec
          read -n4 hdr <"$1"   # check for elf header
          if [ "$hdr" != "$(printf \x7ELF)" ]; then
            exit 0
          fi
          debugfile="$DESTDIR$PREFIX/lib/debug/$(basename "$1")"
          mkdir -p "$(dirname "$debugfile")"
          objcopy --only-keep-debug "$1" "$debugfile"
          chmod 644 "$debugfile"
          strip --remove-section=.comment --remove-section=.note --strip-unneeded "$1"
          objcopy --add-gnu-debuglink "$debugfile" "$1"' - {} ';'

  python-distutils:
    # The Python distutils build systems.
    configure-commands: []
    build-commands:
    - python setup.py build
    install-commands:
    - python setup.py install --prefix "$PREFIX" --root "$DESTDIR"
    strip-commands:
      - *generic-strip-command

  cpan:
    # The Perl ExtUtil::MakeMaker build system.
    configure-commands:
      # This is subject to change, see: https://gerrit.baserock.org/#/c/986/
      - |
        perl Makefile.PL INSTALLDIRS=perl
            INSTALLARCHLIB="$PREFIX/lib/perl"
            INSTALLPRIVLIB="$PREFIX/lib/perl"
            INSTALLBIN="$PREFIX/bin"
            INSTALLSCRIPT="$PREFIX/bin"
            INSTALLMAN1DIR="$PREFIX/share/man/man1"
            INSTALLMAN3DIR="$PREFIX/share/man/man3"
    build-commands:
    - make
    install-commands:
    - make DESTDIR="$DESTDIR" install
    strip-commands:
      - *generic-strip-command

  cmake:
    # The CMake build system.
    configure-commands:
    - cmake -DCMAKE_INSTALL_PREFIX="$PREFIX"'
    build-commands:
    - make
    install-commands:
    - make DESTDIR="$DESTDIR" install
    strip-commands:
      - *generic-strip-command

  qmake:
    # The Qt build system.
    configure-commands:
    - qmake -makefile
    build-commands:
    - make
    install-commands:
    - make INSTALL_ROOT="$DESTDIR" install
    strip-commands:
      - *generic-strip-command


# Predefined artifact splitting rules
# -----------------------------------
#
# Once a build has completed, you have some files that have been installed into
# $DESTDIR. The splitting rules control how many 'artifact' tarballs are
# generated as a result of the build, and which files from $DESTDIR end up in
# which 'artifact'.
#
# The default split rules are defined here. These can be overriden in
# individual chunk .morph files and stratum .morph files using the 'products'
# field.
#
split-rules:
  chunk:
    - artifact: -bins
      include:
        - (usr/)?s?bin/.*
    - artifact: -libs
      include:
        - (usr/)?lib(32|64)?/lib[^/]*\.so(\.\d+)*
        - (usr/)libexec/.*
    - artifact: -devel
      include:
        - (usr/)?include/.*
        - (usr/)?lib(32|64)?/lib.*\.a
        - (usr/)?lib(32|64)?/lib.*\.la
        - (usr/)?(lib(32|64)?|share)/pkgconfig/.*\.pc
    - artifact: -doc
      include:
        - (usr/)?share/doc/.*
        - (usr/)?share/man/.*
        - (usr/)?share/info/.*
    - artifact: -locale
      include:
        - (usr/)?share/locale/.*
        - (usr/)?share/i18n/.*
        - (usr/)?share/zoneinfo/.*
    - artifact: -misc
      include:
        - .*

  stratum:
    - artifact: -devel
      include:
        - .*-devel
        - .*-debug
        - .*-doc
    - artifact: -runtime
      include:
        - .*-bins
        - .*-libs
        - .*-locale
        - .*-misc
        - .*
'''


try:
    if migrations.check_definitions_version(TO_VERSION - 1):
        if os.path.exists('DEFAULTS'):
            warnings.warn(
                "DEFAULTS file already exists in these definitions.")
            valid = False
        else:
            with open('DEFAULTS', 'w') as f:
                f.write(REFERENCE_DEFAULTS)
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
        sys.stdout.write("Nothing to do.\n")
        sys.exit(0)
except RuntimeError as e:
    sys.stderr.write("Error: %s\n" % e.message)
    sys.exit(1)
