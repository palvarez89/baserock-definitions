#!/usr/bin/python
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


"""A module providing Baserock-specific partitioning functions"""

import os
import pyfdisk
import re
import subprocess
import writeexts

def do_partitioning(location, disk_size, temp_root, part_spec):
    '''Perform partitioning

    Perform partitioning using the pyfdisk.py module. Documentation
    for this, and guidance on how to create a partition specification can
    be found in extensions/pyfdisk.README

    This function also validates essential parts of the partition layout

    Args:
        location: Path to the target device or image
        temp_root: Location of the unpacked Baserock rootfs
        part_spec: Path to a YAML formatted partition specification
    Returns:
        A pyfdisk.py Device object
    Raises:
        writeexts.ExtensionError
    '''
    # Create partition table and filesystems
    try:
        dev = pyfdisk.load_yaml(location, disk_size, part_spec)
        writeexts.Extension.status(msg='Loaded partition specification: %s' %
                                        part_spec)

        # FIXME: GPT currently not fully supported due to missing tools
        if dev.partition_table_format.lower() == 'gpt':
            writeexts.Extension.status(msg='WARNING: GPT partition tables '
                                           'are not currently supported, '
                                           'when using the extlinux '
                                           'bootloader')

        writeexts.Extension.status(msg='Summary:\n' + str(dev.partitionlist))
        writeexts.Extension.status(msg='Writing partition table')
        dev.commit()
        dev.create_filesystems(skip=('/'))
    except (pyfdisk.PartitioningError, pyfdisk.FdiskError) as e:
        raise writeexts.ExtensionError(e.msg)

    mountpoints = set(part.mountpoint for part in dev.partitionlist
                                      if hasattr(part, 'mountpoint'))
    if '/' not in mountpoints:
        raise writeexts.ExtensionError('No partition with root '
                                       'mountpoint, please specify a '
                                       'partition with \'mountpoint: /\' '
                                       'in the partition specification')

    mounted_partitions = set(part for part in dev.partitionlist
                             if hasattr(part, 'mountpoint'))

    for part in mounted_partitions:
        if not hasattr(part, 'filesystem'):
            raise writeexts.ExtensionError('Cannot mount a partition '
                                 'without filesystem, please specify one '
                                 'for \'%s\' partition in the partition '
                                 'specification' % part.mountpoint)
        if part.mountpoint == '/':
            # Check that bootable flag is set for MBR devices
            if (hasattr(part, 'boot')
                  and str(part.boot).lower() not in ('yes', 'true')
                  and dev.partition_table_format.lower() == 'mbr'):
                writeexts.Extension.status(msg='WARNING: Boot partition '
                                               'needs bootable flag set to '
                                               'boot with extlinux/syslinux')

    return dev

def process_raw_files(dev, temp_root):
    if hasattr(dev, 'raw_files'):
        write_raw_files(dev.location, temp_root, dev)
    for part in dev.partitionlist:
        if hasattr(part, 'raw_files'):
            # dd seek=n is used, which skips n blocks before writing,
            # so we must skip n-1 sectors before writing in order to
            # start writing files to the first block of the partition
            write_raw_files(dev.location, temp_root, part,
                            (part.extent.start - 1) * dev.sector_size)

def write_raw_files(location, temp_root, dev_or_part, start_offset=0):
    '''Write files with `dd`'''
    offset = 0
    for raw_args in dev_or_part.raw_files:
        r = RawFile(temp_root, start_offset, offset, **raw_args)
        offset = r.next_offset
        r.dd(location)


class RawFile(object):
    '''A class to hold information about a raw file to write to a device'''

    def __init__(self, source_root,
                 start_offset=0, wr_offset=0,
                 sector_size=512, **kwargs):
        '''Initialisation function

        Args:
            source_root: Base path for filenames
            wr_offset: Offset to write to (and offset per-file offsets by)
            sector_size: Device sector size (default: 512)
            **kwargs:
                file: A path to the file to write (combined with source_root)
                offset_sectors: An offset to write to in sectors (optional)
                offset_bytes: An offset to write to in bytes (optional)
        '''
        if 'file' not in kwargs:
            raise writeexts.ExtensionError('Missing file name or path')
        self.path = os.path.join(source_root,
                                 re.sub('^/', '', kwargs['file']))

        if not os.path.exists(self.path):
            raise writeexts.ExtensionError('File not found: %s' % self.path)
        elif os.path.isdir(self.path):
            raise writeexts.ExtensionError('Can only dd regular files')
        else:
            self.size = os.stat(self.path).st_size

        self.offset = start_offset
        if 'offset_bytes' in kwargs:
            self.offset += pyfdisk.human_size(kwargs['offset_bytes'])
        elif 'offset_sectors' in kwargs:
            self.offset += kwargs['offset_sectors'] * sector_size
        else:
            self.offset += wr_offset

        self.skip = pyfdisk.human_size(kwargs.get('skip_bytes', 0))
        self.count = pyfdisk.human_size(kwargs.get('count_bytes', self.size))

        # Offset of the first free byte after this file (first byte of next)
        self.next_offset = self.size + self.offset

    def dd(self, location):
        writeexts.Extension.status(msg='Writing %s at %d bytes' %
                                       (self.path, self.offset))
        subprocess.check_call(['dd', 'if=%s' % self.path,
                                     'of=%s' % location, 'bs=1',
                                     'seek=%d' % self.offset,
                                     'skip=%d' % self.skip,
                                     'count=%d' % self.count,
                                     'conv=notrunc'])
        subprocess.check_call('sync')
