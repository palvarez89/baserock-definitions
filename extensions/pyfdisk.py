#!/usr/bin/env python2
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

"""
A simple Python wrapper for fdisk

 * Intends to have as few dependencies as possible, beyond command line fdisk
 * Intends to work on Linux, though may work on other operating systems with
   fdisk from util-linux.
 * Provides for the creation of MBR and GPT partitioned images or devices
 * Includes some utility functions for reading information from existing
   partition tables

Caveats:
 * Designed to cater for disks using 4096 byte sectors, although this hasn't
   been tested yet.
"""

import contextlib
from copy import deepcopy
import re
import subprocess
import time
import yaml


class Extent(object):
    """
    A class to hold start and end points for other objects

    Start and end points are measured in sectors. This class transparently
    handles the inclusive nature of the start and end sectors of blocks of
    storage. It also allows extents to be aligned within other extents.
    """

    def __init__(self, start=0, length=0, end=0):
        if length and not start:
                raise PartitioningError('Extent requires a non-zero start '
                                        'point and length')
        if start and length:
            self.start = int(start)
            self.end = int(start) + int(length) - 1
        else:
            self.start = int(start)
            self.end = int(end)

        self.filled_sectors = 0

    def __max__(self):
        return self.end

    def __min__(self):
        return self.start

    def __len__(self):
        return self.end - self.start + 1

    def __add__(self, other):
        """Return the sum of two extents"""
        return Extent(start=self.start,
                      length=(len(self) + len(other)))

    def __iadd__(self, other):
        """+="""
        self.end += len(other)
        return self

    def __gt__(self, other):
        return len(self) > len(other)

    def __lt__(self, other):
        return not self > other

    def __str__(self):
        return ('<Extent: Start=%d, End=%d, Length=%d>' %
                (self.start, self.end, len(self)))

    def pack(self, other):
        """
        Return a new Extent aligned to self's first unused sector

        This is done by length, to quantify fitting an area of disk space
        inside the other. The filled space in self is calculated and updated.

        Returns:
            A new Extent, starting at the first available sector in `self`,
            with the same length as `other`.
        """
        length_other = len(other)
        first_free_sector = self.start + self.filled_sectors
        if length_other + self.filled_sectors > len(self):
            raise PartitioningError('Not enough free space to pack Extent')
        self.filled_sectors += length_other
        return Extent(start=first_free_sector, length=length_other)

    def free_sectors(self):
        return len(self) - self.filled_sectors


class PartitionList(object):
    """
    An iterable object to contain and process a list of Partition objects

    This class eases the calculation of partition sizes and numbering, since
    the properties of a given partition depend on each of the other partitions
    in the list.

    Attributes:
        device: A Device class containing the partition list
    """

    def __init__(self, device):
        """
        Initialisation function

        Args:
            device: A Device object
        """
        self.device = device
        self.extent = device.extent

        self.__cached_list_hash = 0

        self.__partition_list = []
        self.__iter_index = 0

    def append(self, partition):
        """Append a new Partition object to the list"""
        partition.check()
        if isinstance(partition, Partition):
            for part in self.__partition_list:
                dup_attrib = part.compare(partition)
                if dup_attrib:
                    raise PartitioningError('Duplicated partition attribute '
                                            '\'%s\'' % dup_attrib)
            self.__partition_list.append(partition)
        else:
            raise PartitioningError('PartitionList can only '
                                    'contain Partition objects')

    def __iter__(self):
        """Return a copy of self as an iterable object"""
        self.__iter_index = 0
        copy = deepcopy(self)
        return copy

    def __next__(self):
        """Return the next item in an iteration"""
        if self.__iter_index == len(self.__partition_list):
            raise StopIteration
        else:
            partition = self[self.__iter_index]
            self.__iter_index += 1
            return partition

    def next(self):
        """Provide a next() method for Python 2 compatibility"""
        return self.__next__()

    def __getitem__(self, i):
        """Return an partition from the list, sorted by partition number"""
        part_list = sorted(self.__update_partition_list(),
                           key=lambda part: part.number)
        return part_list[i]

    def free_sectors(self):
        """Calculate the amount of unused space in the list"""
        part_list = self.__update_partition_list()
        self.extent.filled_sectors = 0
        for part in part_list:
            self.extent.pack(part.extent)
        return self.extent.free_sectors()

    def __update_partition_list(self):
        """
        Allocate extent and numbering for each Partition object in the list

        A copy of the partition list is made so that any Partition object
        returned from this list is a copy of a stored Partition object, thus
        any partitions stored in the partition list remain intact even if a
        copy is modified after is is returned. Hashing is used to avoid
        updating the list when the partition list has not changed.
        """
        current_list_hash = hash(str(self.__partition_list))
        if current_list_hash == self.__cached_list_hash:
            return self.__cached_list

        part_list = deepcopy(self.__partition_list)
        used_numbers = set()
        fill_partitions = set(partition for partition in part_list
                              if partition.size == 'fill')
        requested_numbers = set(partition.number for partition in part_list
                                if hasattr(partition, 'number'))

        # Get free space and the size of 'fill' partitions
        self.extent.filled_sectors = 0
        for part in part_list:
            if part.size != 'fill':
                extent = Extent(start=1,
                                length=self.get_length_sectors(part.size))
                part.extent = extent
                self.extent.pack(extent)

        # Allocate aligned Extents and process partition numbers
        if len(fill_partitions):
            fill_size = self.extent.free_sectors() / len(fill_partitions)
            # Set size of fill partitions
            for part in fill_partitions:
                part.size = fill_size * self.device.sector_size
                part.extent = Extent(start=1, length=fill_size)

        self.extent.filled_sectors = 0
        for part in part_list:
            part.extent = self.extent.pack(part.extent)

            # Find the next unused partition number if not assigned
            if hasattr(part, 'number'):
                num = part.number
            else:
                for n in range(1, self.device.max_allowed_partitions + 1):
                    if n not in used_numbers and n not in requested_numbers:
                        num = n
                        break

            part.number = num
            used_numbers.add(num)

        self.__cached_list_hash = current_list_hash
        self.__cached_list = part_list
        return part_list

    def get_length_sectors(self, size_bytes):
        """Get a length in sectors, aligned to 4096 byte boundaries"""
        return (int(size_bytes) / self.device.sector_size +
               ((int(size_bytes) % 4096) != 0) *
               (4096 / self.device.sector_size))

    def __str__(self):
        string = ''
        for part in self:
            string = '%s\n%s\n' % (part, string)
        return string.rstrip()

    def __len__(self):
        return len(self.__partition_list)

    def __setitem__(self, i, value):
        """Update the ith item in the list"""
        self.append(partition)


class Partition(object):
    """
    A class to describe a partition in a disk or image

    The required attributes are loaded via kwargs.

    Required attributes:
        size: String describing the size of the partition in bytes
              This may also be 'fill' to indicate that this partition should
              be expanded to fill all unused space. Where there is more than
              one fill partition, unused space is divided equally between the
              fill partitions.
        fdisk_type: An integer representing the hexadecimal code used by fdisk
                    to describe the partition type. Any partitions with
                    fdisk_type='none' create an area of unused space.

    Optional attributes:
        **kwargs: A mapping of any keyword arguments
        filesystem: A string describing the filesystem format for the
                    partition, or 'none' to skip filesystem creation.
        description: A string describing the partition, for documentation
        boot: Boolean string describing whether to set the bootable flag
        mountpoint: String describing the mountpoint for the partition
        number: Number used to override partition numbering for the
                partition (Possible only when using an MBR partition table)
    """
    def __init__(self, size=0, fdisk_type=0x81, filesystem='none', **kwargs):
        if not size and 'size' not in kwargs:
            raise PartitioningError('Partition must have a non-zero size')

        self.filesystem = filesystem
        self.fdisk_type = fdisk_type

        self.size = human_size(size)
        self.__dict__.update(**kwargs)

    def check(self):
        """Check for correctness"""
        if self.fdisk_type == 'none':
            if self.filesystem != 'none':
                raise PartitioningError('Partition: Free space '
                                        'cannot have a filesystem')
            if hasattr(self, 'mountpoint') and self.mountpoint != 'none':
                raise PartitioningError('Partition: Free space '
                                        'cannot have a mountpoint')

    def compare(self, other):
        """Check for mutually exclusive attributes"""
        non_duplicable = ('number', 'mountpoint')
        for attrib in non_duplicable:
            if hasattr(self, attrib) and hasattr(other, attrib):
                if getattr(self, attrib) == getattr(other, attrib):
                    return attrib
        return False

    def __str__(self):
        string = ('Partition\n'
                        '    size:       %s\n'
                        '    fdisk type: %s\n'
                        '    filesystem: %s'
                   % (self.size,
                      hex(self.fdisk_type) if self.fdisk_type != 'none'
                      else 'none',
                      self.filesystem))
        if hasattr(self, 'extent'):
            string += (
                      '\n    start:      %s'
                      '\n    end:        %s'
                        % (self.extent.start, self.extent.end))
        if hasattr(self, 'number'):
            string += '\n    number:     %s' % self.number
        if hasattr(self, 'mountpoint'):
            string += '\n    mountpoint: %s' % self.mountpoint
        if hasattr(self, 'boot'):
            string += '\n    bootable:   %s' % self.boot

        return string


class Device(object):
    """
    A class to describe a disk or image, and its partition layout

    Attributes are loaded from **kwargs, containing key-value pairs describing
    the required attributes. This can be loaded from a YAML file, using the
    module function load_yaml().

    Required attributes:
        location: The location of the device or disk image
        size: A size in bytes describing the total amount of space the
              partition table on the device will occupy, or 'fill' to
              automatically fill the available space.

    Optional attributes:
        **kwargs: A mapping of any keyword arguments
        start_offset: The first 512 byte sector of the first partition
                      (default: 2048)
        partition_table_format: A string describing the type of partition
                                table used on the device (default: 'gpt')
        partitions: A list of mappings for the attributes for each Partition
                    object. update_partitions() populates the partition list
                    based on the contents of this attribute.
    """
    min_start_bytes = 1024**2

    def __init__(self, location, size, **kwargs):

        if 'partition_table_format' not in kwargs:
            self.partition_table_format = 'gpt'
        if 'start_offset' not in kwargs:
            self.start_offset = 2048

        target_size = get_disk_size(location)
        if str(size).lower() == 'fill':
            self.size = target_size
        else:
            self.size = human_size(size)

        if self.size > target_size:
            raise PartitioningError('Not enough space available on target')

        if self.size <= self.min_start_bytes:
            raise PartitioningError('Device size must be greater than %d '
                                    'bytes' % self.min_start_bytes)

        # Get sector size
        self.sector_size = get_sector_size(location)
        self.location = location

        # Populate Device attributes from keyword args
        self.__dict__.update(**kwargs)

        if self.partition_table_format.lower() == 'gpt':
            self.max_allowed_partitions = 128
        else:
            self.max_allowed_partitions = 4

        # Process Device size
        start = (self.start_offset * 512) / self.sector_size
        # Sector quantities in the specification are assumed to be 512 bytes
        # This converts to the real sector size
        if (start * self.sector_size) < self.min_start_bytes:
            raise PartitioningError('Start offset should be greater than '
                                    '%d, for %d byte sectors' %
                                    (min_start_bytes / self.sector_size,
                                     self.sector_size))
        # Check the disk's first partition starts on a 4096 byte boundary
        # this ensures alignment, and avoiding a reduction in performance
        # on disks which use a 4096 byte physical sector size
        if (start * self.sector_size) % 4096 != 0:
            print('WARNING: Start sector is not aligned '
                  'to 4096 byte sector boundaries')

        # End sector is one sector less than the disk length
        disk_end_sector = (self.size / self.sector_size) - 1
        if self.partition_table_format == 'gpt':
            # GPT partition table is duplicated at the end of the device.
            # GPT header takes one sector, whatever the sector size,
            # with a 16384 byte 'minimum' area for partition entries,
            # supporting up to 128 partitions (128 bytes per entry).
            # The duplicate GPT does not include the 'protective' MBR
            gpt_size = ((16 * 1024) / self.sector_size) + 1
            self.extent = Extent(start=start,
                                 end=(disk_end_sector - gpt_size))
        else:
            self.extent = Extent(start=start, end=disk_end_sector)

        self.update_partitions()

    def update_partitions(self, partitions=None):
        """
        Reset list, populate with partitions from a list of attributes

        Args:
            partitions: A list of partition keyword attributes
        """

        self.partitionlist = PartitionList(self)
        if partitions:
            self.partitions = partitions
        if hasattr(self, 'partitions'):
            for partition_args in self.partitions:
                self.add_partition(Partition(**partition_args))

    def add_partition(self, partition):
        """
        Add a Partition object to the device's list of partitions

        Args:
            partition: a Partition class
        """

        if len(self.partitionlist) < self.max_allowed_partitions:
            self.partitionlist.append(partition)
        else:
            raise PartitioningError('Exceeded maximum number of partitions '
                                    'for %s partition table (%d)' %
                                    (self.partition_table_format.upper(),
                                     self.max_allowed_partitions))

    def get_partition_by_mountpoint(self, mountpoint):
        """Return a Partition with a specified mountpoint"""

        return next(r for r in self.partitionlist
            if hasattr(r, 'mountpoint')
            and r.mountpoint == '/')

    def commit(self):
        """Write the partition table to the disk or image"""

        pt_format = self.partition_table_format.lower()
        print("Creating %s partition table on %s" %
                        (pt_format.upper(), self.location))

        # Create a new partition table
        if pt_format in ('mbr', 'dos'):
            cmd = "o\n"
        elif pt_format == 'gpt':
            cmd = "g\n"
        else:
            raise PartitioningError('Unrecognised partition '
                                    'table type \'%s\'' % pt_format)

        for partition in self.partitionlist:
            # Create partitions
            if str(partition.fdisk_type).lower() != 'none':
                cmd += "n\n"
                if pt_format in ('mbr', 'dos'):
                    cmd += "p\n"
                cmd += (str(partition.number) + "\n"
                        "" + str(partition.extent.start) + "\n"
                        "" + str(partition.extent.end) + "\n")

                # Set partition types
                cmd += "t\n"
                if partition.number > 1:
                    # fdisk does not ask for a partition
                    # number when setting the type of the
                    # first created partition
                    cmd += str(partition.number) + "\n"
                cmd += str(hex(partition.fdisk_type)) + "\n"

                # Set bootable flag
                if hasattr(partition, 'boot') and pt_format == 'mbr':
                    if str(partition.boot).lower() in ('yes', 'true'):
                        cmd += "a\n"
                        if partition.number > 1:
                            cmd += str(partition.number) + "\n"

        # Write changes
        cmd += ("w\n"
                "q\n")
        p = subprocess.Popen(["fdisk", self.location],
                             stdin=subprocess.PIPE,
                             stderr=subprocess.PIPE,
                             stdout=subprocess.PIPE)
        output = p.communicate(cmd)

        errors = output[1].split('\n')[1:-1]
        if errors:
            # Exception handling is done in this way since fdisk will not
            # return a failure exit code if it finds problems with its input.
            # Note that the message 'disk does not contain a valid partition
            # table' is not an error, it's a status message printed to stderr
            # when fdisk starts with a blank device.
            raise FdiskError('"%s"' % ' '.join(str(x) for x in errors))

    def get_partition_uuid(self, partition):
        """Read a partition's UUID from disk (MBR or GPT)"""

        return get_partition_uuid(self.location, partition.number,
                                  self.partition_table_format)

    def create_filesystems(self, skip=[]):
        """Create filesystems on the disk or image

        Args:
            skip: An iterable of mountpoints identifying partitions to skip
                  filesystem creation on, for example if custom settings are
                  required
        """

        for part in self.partitionlist:
            if hasattr(part, 'mountpoint') and part.mountpoint in skip:
                continue
            if part.filesystem.lower() != 'none':
                with create_loopback(self.location,
                                     part.extent.start * self.sector_size,
                                     part.size) as loop:
                    print ('Creating %s filesystem on partition %s' %
                            (part.filesystem, part.number))
                    subprocess.check_output(['mkfs.' + part.filesystem, loop])

    def __str__(self):
        return ('<Device: location=%s, size=%s, partitions: %s>' %
                (self.location, self.size, len(self.partitionlist)))


class PartitioningError(Exception):

    def __init__(self, msg=None):
        self.msg = msg

    def __str__(self):
        return self.msg


class FdiskError(Exception):

    def __init__(self, msg=None):
        self.msg = msg

    def __str__(self):
        return self.msg


def load_yaml(location, size, yaml_file):
    """
    Load partition data from a yaml specification

    The YAML file describes the attributes documented in the Device
    and Partition classes.

    Args:
        yaml_file: String path to a YAML file to load
        location: Path to the device node or image to use for partitioning
        size: The desired device size in bytes (may be 'fill' to occupy the
              entire device

    Returns:
        A Device object
    """

    with open(yaml_file, 'r') as f:
        kwargs = yaml.safe_load(f)
    return Device(location, size, **kwargs)


def get_sector_size(location):
    """Get the logical sector size of a block device or image, in bytes"""

    return int(__filter_fdisk_list_output('Sector size.*?(\d+) bytes',
                                          location)[0])

def get_disk_size(location):
    """Get the total size of a block device or image, in bytes"""

    return int(__filter_fdisk_list_output('Disk.*?(\d+) bytes',
                                          location)[0])

def get_partition_offsets(location):
    """Return an array of the partition start sectors in a device or image"""

    return __get_fdisk_list_numeric_column(location, 1)

def get_partition_sector_sizes(location):
    """Return an array of sizes of partitions in a device or image in sectors"""

    return __get_fdisk_list_numeric_column(location, 3)

def __get_fdisk_list_numeric_column(location, column):
    return map(int, __filter_fdisk_list_output('%s(?:\d+[\*\s]+){%d}(\d+)' %
                                                (location, column), location))

def __filter_fdisk_list_output(regex, location):
    r = re.compile(regex, re.DOTALL)
    m = re.findall(r, subprocess.check_output(['fdisk', '-l', location]))
    if m:
        return m
    else:
        raise PartitioningError('Error reading information from fdisk')

def human_size(size_string):
    """Parse strings for human readable size factors"""

    facts_of_1024 = ['', 'k', 'm', 'g', 't']
    m = re.match('^(\d+)([kmgtKMGT]?)$', str(size_string))
    if not m:
        return size_string
    return int(m.group(1)) * (1024 ** facts_of_1024.index(m.group(2).lower()))

@contextlib.contextmanager
def create_loopback(mount_path, offset=0, size=0):
    """
    Create a loopback device for accessing partitions in block devices

    Args:
        mount_path: String path to mount
        offset: Offset of the start of a partition in bytes (default 0)
        size: Limits the size of the partition, in bytes (default 0). This is
              important when creating filesystems, otherwise tools often
              corrupt areas beyond the desired limits of the partition.
    Returns:
        The path to a created loopback device node
    """

    try:
        base_args = ['losetup', '--show', '-f', '-P', '-o', str(offset)]
        if size and offset:
            cmd = base_args + ['--sizelimit', str(size), mount_path]
        else:
            cmd = base_args + [mount_path]
        loop_device = subprocess.check_output(cmd).rstrip()
        # Allow the system time to see the new device On some systems, mounts
        # created on the loopdev too soon after creating the loopback device
        # may be unreliable, even though the -P option (--partscan) is passed
        # to losetup
        time.sleep(1)
    except subprocess.CalledProcessError:
        PartitioningError('Error creating loopback')
    try:
        yield loop_device
    finally:
        subprocess.check_call(['losetup', '-d', loop_device])

def get_pt_type(location):
    """Read the partition table type from location (device or image)"""

    pt_type = __get_blkid_output('PTTYPE', location).lower()
    return 'none' if pt_type == '' else pt_type

def __get_blkid_output(field, location):
    return subprocess.check_output(['blkid', '-p', '-o', 'value',
                                    '-s', field, location]).rstrip()

def get_partition_uuid(location, part_num, pt_type=None):
    """
    Read the partition UUID (MBR or GPT) for location (device or image)

    Args:
        location: Path to device or image
        part_num: Integer number of the partition
        pt_type:  The partition table format (MBR or GPT)
    """

    if not pt_type:
        pt_type = get_pt_type(location)
    if pt_type == 'gpt':
        return get_partition_gpt_guid(location, part_num)
    elif pt_type == 'mbr':
        return get_partition_mbr_uuid(location, part_num)

def get_partition_mbr_uuid(location, part_num):
    """
    Get a partition's UUID in a device using MBR partition table

    In Linux, MBR partition UUIDs are comprised of the NT disk signature,
    followed by '-' and a two digit, zero-padded partition number. This is
    necessary since the MBR does not provide per-partition GUIDs as GPT
    partition tables do.  This can be passed to the kernel with
    "root=PARTUUID=$UUID" to identify a partition containing a root
    filesystem.

    Args:
        partition: A partition object
        location:  Location of the storage device containing the partition -
                   an image or device node
    Returns:
        A UUID referring to an MBR partition, e.g. '97478dab-02'
    """

    pt_uuid = __get_blkid_output('PTUUID', location).upper()
    return '%s-%02d' % (pt_uuid, part_num)

def get_partition_gpt_guid(location, part_num):
    """
    Get a partition's GUID from a GPT partition table

    This is read directly from the partition table, since current fdisk does
    not support reading GPT partition GUIDs. It does not require special tools
    (gfdisk).  This is the GUID which identifies the partition, created with
    the partition table, as opposed to the filesystem UUID, created with the
    filesystem. It is particularly useful for specifying the partition which
    the Linux kernel can use on boot to find the root filesystem, e.g. when
    using the kernel command line "root=PARTUUID=$UUID"

    Args:
        part_num: The partition number
        location: Location of the storage device containing the partition -
                  an image path or device node
    Returns:
        A GUID string, e.g. 'B342D1AB-4B65-4601-97DC-D6DF3FE2E95E'
    """

    sector_size = get_sector_size(location)
    # The partition GUID is located two sectors (protective MBR + GPT header)
    # plus 128 bytes for each partition entry in the table, plus 16 bytes for
    # the location of the partition's GUID
    guid_offset = (2 * sector_size) + (128 * (part_num - 1)) + 16

    with open(location, 'rb') as f:
        f.seek(guid_offset)
        raw_uuid_bin = f.read(16)

    a = ''
    for c in raw_uuid_bin:
        a += '%02X' % ord(c)

    return ('%s%s%s%s-%s%s-%s%s-%s-%s' %
            (a[6:8], a[4:6], a[2:4], a[0:2],
             a[10:12], a[8:10],
             a[14:16], a[12:14],
             a[16:20], a[20:32]))
