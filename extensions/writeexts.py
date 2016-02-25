# Copyright (C) 2012-2015  Codethink Limited
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


import contextlib
import errno
import fcntl
import logging
import os
import partitioning
import pyfdisk
import re
import select
import shutil
import stat
import subprocess
import sys
import time
import tempfile


if sys.version_info >= (3, 3, 0):
    import shlex
    shell_quote = shlex.quote
else:
    import pipes
    shell_quote = pipes.quote


def get_data_path(relative_path):
    extensions_dir = os.path.dirname(__file__)
    return os.path.join(extensions_dir, relative_path)


def get_data(relative_path):
    with open(get_data_path(relative_path)) as f:
        return f.read()


def ssh_runcmd(host, args, **kwargs):
    '''Run command over ssh'''
    command = ['ssh', host, '--'] + [shell_quote(arg) for arg in args]

    feed_stdin = kwargs.get('feed_stdin')
    stdin = kwargs.get('stdin', subprocess.PIPE)
    stdout = kwargs.get('stdout', subprocess.PIPE)
    stderr = kwargs.get('stderr', subprocess.PIPE)

    p = subprocess.Popen(command, stdin=stdin, stdout=stdout, stderr=stderr)
    out, err = p.communicate(input=feed_stdin)
    if p.returncode != 0:
        raise ExtensionError('ssh command `%s` failed' % ' '.join(command))
    return out


def write_from_dict(filepath, d, validate=lambda x, y: True):
    """Takes a dictionary and appends the contents to a file

    An optional validation callback can be passed to perform validation on
    each value in the dictionary.

    e.g.

        def validation_callback(dictionary_key, dictionary_value):
            if not dictionary_value.isdigit():
                raise Exception('value contains non-digit character(s)')

    Any callback supplied to this function should raise an exception
    if validation fails.

    """
    # Sort items asciibetically
    # the output of the deployment should not depend
    # on the locale of the machine running the deployment
    items = sorted(d.iteritems(), key=lambda (k, v): [ord(c) for c in v])

    for (k, v) in items:
        validate(k, v)

    with open(filepath, 'a') as f:
        for (_, v) in items:
            f.write('%s\n' % v)

        os.fchown(f.fileno(), 0, 0)
        os.fchmod(f.fileno(), 0644)


def parse_environment_pairs(env, pairs):
    '''Add key=value pairs to the environment dict.

    Given a dict and a list of strings of the form key=value,
    set dict[key] = value, unless key is already set in the
    environment, at which point raise an exception.

    This does not modify the passed in dict.

    Returns the extended dict.

    '''
    extra_env = dict(p.split('=', 1) for p in pairs)
    conflicting = [k for k in extra_env if k in env]
    if conflicting:
        raise ExtensionError('Environment already set: %s'
                             % ', '.join(conflicting))

    # Return a dict that is the union of the two
    # This is not the most performant, since it creates
    # 3 unnecessary lists, but I felt this was the most
    # easy to read. Using itertools.chain may be more efficicent
    return dict(env.items() + extra_env.items())


class ExtensionError(Exception):

    def __init__(self, msg):
        self.msg = msg

    def __str__(self):
        return self.msg


class Fstab(object):
    '''Small helper class for parsing and adding lines to /etc/fstab.'''

    # There is an existing Python helper library for editing of /etc/fstab.
    # However it is unmaintained and has an incompatible license (GPL3).
    #
    # https://code.launchpad.net/~computer-janitor-hackers/python-fstab/trunk

    def __init__(self, filepath='/etc/fstab'):
        if os.path.exists(filepath):
            with open(filepath, 'r') as f:
                self.text= f.read()
        else:
            self.text = ''
        self.filepath = filepath
        self.lines_added = 0

    def get_mounts(self):
        '''Return list of mount devices and targets in /etc/fstab.

        Return value is a dict of target -> device.
        '''
        mounts = dict()
        for line in self.text.splitlines():
            words = line.split()
            if len(words) >= 2 and not words[0].startswith('#'):
                device, target = words[0:2]
                mounts[target] = device
        return mounts

    def add_line(self, line):
        '''Add a new entry to /etc/fstab.

        Lines are appended, and separated from any entries made by configure
        extensions with a comment.

        '''
        if self.lines_added == 0:
            if len(self.text) == 0 or self.text[-1] is not '\n':
                self.text += '\n'
            self.text += '# Morph default system layout\n'
        self.lines_added += 1

        self.text += line + '\n'

    def write(self):
        '''Rewrite the fstab file to include all new entries.'''
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(self.text)
            tmp = f.name
        shutil.move(os.path.abspath(tmp), os.path.abspath(self.filepath))


class Extension(object):

    '''A base class for deployment extensions.

    A subclass should subclass this class, and add a
    ``process_args`` method.

    Note that it is not necessary to subclass this class for write
    extensions. This class is here just to collect common code for
    write extensions.

    '''

    def setup_logging(self):
        '''Direct all logging output to MORPH_LOG_FD, if set.

        This file descriptor is read by Morph and written into its own log
        file.

        '''
        log_write_fd = int(os.environ.get('MORPH_LOG_FD', 0))

        if log_write_fd == 0:
            return

        formatter = logging.Formatter('%(message)s')

        handler = logging.StreamHandler(os.fdopen(log_write_fd, 'w'))
        handler.setFormatter(formatter)

        logger = logging.getLogger()
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

    def process_args(self, args):
        raise NotImplementedError()

    def run(self, args=None):
        if args is None:
            args = sys.argv[1:]
        try:
            self.setup_logging()
            self.process_args(args)
        except ExtensionError as e:
            sys.stdout.write('ERROR: %s\n' % e)
            sys.exit(1)

    @staticmethod
    def status(**kwargs):
        '''Provide status output.

        The ``msg`` keyword argument is the actual message,
        the rest are values for fields in the message as interpolated
        by %.

        '''
        sys.stdout.write('%s\n' % (kwargs['msg'] % kwargs))
        sys.stdout.flush()


class WriteExtension(Extension):

    '''A base class for deployment write extensions.

    A subclass should subclass this class, and add a
    ``process_args`` method.

    Note that it is not necessary to subclass this class for write
    extensions. This class is here just to collect common code for
    write extensions.

    '''

    def check_for_btrfs_in_deployment_host_kernel(self):
        with open('/proc/filesystems') as f:
            text = f.read()
        return '\tbtrfs\n' in text

    def require_btrfs_in_deployment_host_kernel(self):
        if not self.check_for_btrfs_in_deployment_host_kernel():
            raise ExtensionError(
                'Error: Btrfs is required for this deployment, but was not '
                'detected in the kernel of the machine that is running Morph.')

    def create_local_system(self, temp_root, location):
        '''Create a raw system image locally.'''

        with self.created_disk_image(location):
            self.create_baserock_system(temp_root, location)

    def create_baserock_system(self, temp_root, location):
        if self.get_environment_boolean('USE_PARTITIONING', 'no'):
            self.create_partitioned_system(temp_root, location)
        else:
            self.format_btrfs(location)
            self.create_unpartitioned_system(temp_root, location)

    @contextlib.contextmanager
    def created_disk_image(self, location):
        size = self.get_disk_size()
        if not size:
            raise ExtensionError('DISK_SIZE is not defined')
        self.create_raw_disk_image(location, size)
        try:
            yield
        except BaseException:
            os.unlink(location)
            raise

    def format_btrfs(self, raw_disk):
        try:
            self.mkfs_btrfs(raw_disk)
        except BaseException:
            sys.stderr.write('Error creating disk image')
            raise

    def create_unpartitioned_system(self, temp_root, raw_disk):
        '''Deploy a bootable Baserock system within a single Btrfs filesystem.

        Called if USE_PARTITIONING=no (the default) is set in the deployment
        options.

        '''
        with self.mount(raw_disk) as mp:
            try:
                self.create_versioned_layout(mp, version_label='factory')
                self.create_btrfs_system_rootfs(
                    temp_root, mp, version_label='factory',
                    rootfs_uuid=self.get_uuid(raw_disk))
                if self.bootloader_config_is_wanted():
                    self.create_bootloader_config(
                        temp_root, mp, version_label='factory',
                        rootfs_uuid=self.get_uuid(raw_disk))
            except BaseException as e:
                sys.stderr.write('Error creating Btrfs system layout')
                raise

    def _parse_size(self, size):
        '''Parse a size from a string.

        Return size in bytes.

        '''

        m = re.match('^(\d+)([kmgKMG]?)$', size)
        if not m:
            return None

        factors = {
            '': 1,
            'k': 1024,
            'm': 1024**2,
            'g': 1024**3,
        }
        factor = factors[m.group(2).lower()]

        return int(m.group(1)) * factor

    def _parse_size_from_environment(self, env_var, default):
        '''Parse a size from an environment variable.'''

        size = os.environ.get(env_var, default)
        if size is None:
            return None
        bytes = self._parse_size(size)
        if bytes is None:
            raise ExtensionError('Cannot parse %s value %s'
                                 % (env_var, size))
        return bytes

    def get_disk_size(self):
        '''Parse disk size from environment.'''
        return self._parse_size_from_environment('DISK_SIZE', None)

    def get_ram_size(self):
        '''Parse RAM size from environment.'''
        return self._parse_size_from_environment('RAM_SIZE', '1G')

    def get_vcpu_count(self):
        '''Parse the virtual cpu count from environment.'''
        return self._parse_size_from_environment('VCPUS', '1')

    def create_raw_disk_image(self, filename, size):
        '''Create a raw disk image.'''

        self.status(msg='Creating empty disk image')
        with open(filename, 'wb') as f:
            if size > 0:
                f.seek(size-1)
                f.write('\0')

    def mkfs_btrfs(self, location):
        '''Create a btrfs filesystem on the disk.'''

        self.status(msg='Creating btrfs filesystem')
        try:
            # The following command disables some new filesystem features. We
            # need to do this because at the time of writing, SYSLINUX has not
            # been updated to understand these new features and will fail to
            # boot if the kernel is on a filesystem where they are enabled.
            subprocess.check_output(
                ['mkfs.btrfs','-f', '-L', 'baserock',
                '--features', '^extref',
                '--features', '^skinny-metadata',
                '--features', '^mixed-bg',
                '--nodesize', '4096',
                location], stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as e:
            if 'unrecognized option \'--features\'' in e.output:
                # Old versions of mkfs.btrfs (including v0.20, present in many
                # Baserock releases) don't support the --features option, but
                # also don't enable the new features by default. So we can
                # still create a bootable system in this situation.
                logging.debug(
                    'Assuming mkfs.btrfs failure was because the tool is too '
                    'old to have --features flag.')
                subprocess.check_call(['mkfs.btrfs','-f',
                                       '-L', 'baserock', location])
            else:
                raise

    def get_uuid(self, location, offset=0):
        '''Get the filesystem UUID of a block device's file system.

        Requires util-linux blkid; the busybox version ignores options and
        lies by exiting successfully.

        Args:
            location: Path of device or image to inspect
            offset: A byte offset - which should point to the start of a
                    partition containing a filesystem
        '''

        return subprocess.check_output(['blkid', '-s', 'UUID', '-o',
                                        'value', '-p', '-O', str(offset),
                                        location]).strip()

    @contextlib.contextmanager
    def mount(self, location):
        self.status(msg='Mounting filesystem')
        try:
            mount_point = tempfile.mkdtemp()
            if self.is_device(location):
                subprocess.check_call(['mount', location, mount_point])
            else:
                subprocess.check_call(['mount', '-o', 'loop',
                                       location, mount_point])
        except BaseException as e:
            sys.stderr.write('Error mounting filesystem')
            os.rmdir(mount_point)
            raise
        try:
            yield mount_point
        finally:
            self.status(msg='Unmounting filesystem')
            subprocess.check_call(['umount', mount_point])
            os.rmdir(mount_point)

    def create_versioned_layout(self, mountpoint, version_label):
        '''Create a versioned directory structure within a partition.

        The Baserock project has defined a 'reference upgrade mechanism'. This
        mandates a specific directory layout. It consists of a toplevel
        '/systems' directory, containing subdirectories named with a 'version
        label'. These subdirectories contain the actual OS content.

        For the root file system, a Btrfs partition must be used. For each
        version, two subvolumes are created: 'orig' and 'run'. This is handled
        in create_btrfs_system_rootfs().

        Other partitions (e.g. /boot) can also follow the same layout. In the
        case of /boot, content goes directly in the version directory. That
        means there are no 'orig' and 'run' subvolumes, which avoids the
        need to use Btrfs.

        The `system-version-manager` tool from tbdiff.git is responsible for
        deploying live upgrades, and it understands this layout.

        '''
        version_root = os.path.join(mountpoint, 'systems', version_label)

        os.makedirs(version_root)
        os.symlink(
                version_label, os.path.join(mountpoint, 'systems', 'default'))

    def create_btrfs_system_rootfs(self, temp_root, mountpoint, version_label,
                                   rootfs_uuid, device=None):
        '''Separate base OS versions from state using subvolumes.

        The 'device' parameter should be a pyfdisk.Device instance,
        as returned by partitioning.do_partitioning(), that describes the
        partition layout of the target device. This is used to set up
        mountpoints in the root partition for the other partitions.
        If no 'device' instance is passed, no mountpoints are set up in the
        rootfs.

        '''
        version_root = os.path.join(mountpoint, 'systems', version_label)
        state_root = os.path.join(mountpoint, 'state')
        os.makedirs(state_root)

        system_dir = self.create_orig(version_root, temp_root)
        state_dirs = self.complete_fstab_for_btrfs_layout(system_dir,
                                                          rootfs_uuid, device)

        for state_dir in state_dirs:
            self.create_state_subvolume(system_dir, mountpoint, state_dir)

        self.create_run(version_root)

        if device:
            self.create_partition_mountpoints(device, system_dir)

    def create_bootloader_config(self, temp_root, mountpoint, version_label,
                                 rootfs_uuid, device=None):
        '''Setup the bootloader.

        '''
        initramfs = self.find_initramfs(temp_root)
        version_root = os.path.join(mountpoint, 'systems', version_label)
        system_dir = os.path.join(version_root, 'orig')

        self.install_kernel(version_root, temp_root)
        if self.get_dtb_path() != '':
            self.install_dtb(version_root, temp_root)
        self.install_syslinux_menu(mountpoint, temp_root)
        if initramfs is not None:
            # Using initramfs - can boot a rootfs with a filesystem UUID
            self.install_initramfs(initramfs, version_root)
            self.generate_bootloader_config(mountpoint,
                                            rootfs_uuid=rootfs_uuid)
        else:
            if device:
                # A partitioned disk or image - boot with partition UUID
                root_part = device.get_partition_by_mountpoint('/')
                root_guid = device.get_partition_uuid(root_part)
                self.generate_bootloader_config(mountpoint,
                                                root_guid=root_guid)
            else:
                # Unpartitioned and no initramfs - cannot boot with a UUID
                self.generate_bootloader_config(mountpoint)
        self.install_bootloader(mountpoint)

    def create_partition_mountpoints(self, device, system_dir):
        '''Create (or empty) partition mountpoints in the root filesystem

        Delete contents of partition mountpoints in the rootfs to leave an
        empty mount drectory (files are copied to the actual partition in
        create_partitioned_system()), or create an empty mount directory in
        the rootfs if the mount path doesn't exist.

        Args:
            device: A pyfdisk.py Device object describing the partitioning
            system_dir: A path to the Baserock rootfs to be modified
        '''

        for part in device.partitionlist:
            if hasattr(part, 'mountpoint') and part.mountpoint != '/':
                part_mount_dir = os.path.join(system_dir,
                                     re.sub('^/', '', part.mountpoint))
                if os.path.exists(part_mount_dir):
                    self.status(msg='Deleting files in mountpoint '
                                    'for %s partition' % part.mountpoint)
                    self.empty_dir(part_mount_dir)
                else:
                    self.status(msg='Creating empty mount directory '
                                    'for %s partition' % part.mountpoint)
                    os.mkdir(part_mount_dir)

    def create_orig(self, version_root, temp_root):
        '''Create the default "factory" system.'''

        orig = os.path.join(version_root, 'orig')

        self.status(msg='Creating orig subvolume')
        subprocess.check_call(['btrfs', 'subvolume', 'create', orig])
        self.status(msg='Copying files to orig subvolume')
        subprocess.check_call(['cp', '-a', temp_root + '/.', orig + '/.'])

        return orig

    def create_run(self, version_root):
        '''Create the 'run' snapshot.'''

        self.status(msg='Creating run subvolume')
        orig = os.path.join(version_root, 'orig')
        run = os.path.join(version_root, 'run')
        subprocess.check_call(
            ['btrfs', 'subvolume', 'snapshot', orig, run])

    def create_state_subvolume(self, system_dir, mountpoint, state_subdir):
        '''Create a shared state subvolume.

        We need to move any files added to the temporary rootfs by the
        configure extensions to their correct home. For example, they might
        have added keys in `/root/.ssh` which we now need to transfer to
        `/state/root/.ssh`.

        '''
        self.status(msg='Creating %s subvolume' % state_subdir)
        subvolume = os.path.join(mountpoint, 'state', state_subdir)
        subprocess.check_call(['btrfs', 'subvolume', 'create', subvolume])
        os.chmod(subvolume, 0o755)

        existing_state_dir = os.path.join(system_dir, state_subdir)
        self.move_dir_contents(existing_state_dir, subvolume)

    def move_dir_contents(self, source_dir, target_dir):
        '''Move all files source_dir, to target_dir'''

        n = self.__cmd_files_in_dir(['mv'], source_dir, target_dir)
        if n:
            self.status(msg='Moved %d files to %s' % (n, target_dir))

    def copy_dir_contents(self, source_dir, target_dir):
        '''Copy all files source_dir, to target_dir'''

        n = self.__cmd_files_in_dir(['cp', '-a', '-r'], source_dir, target_dir)
        if n:
            self.status(msg='Copied %d files to %s' % (n, target_dir))

    def empty_dir(self, directory):
        '''Empty the contents of a directory, but not the directory itself'''

        n = self.__cmd_files_in_dir(['rm', '-rf'], directory)
        if n:
            self.status(msg='Deleted %d files in %s' % (n, directory))

    def __cmd_files_in_dir(self, cmd, source_dir, target_dir=None):
        files = []
        if os.path.exists(source_dir):
            files = os.listdir(source_dir)
        for filename in files:
            filepath = os.path.join(source_dir, filename)
            add_params = [filepath, target_dir] if target_dir else [filepath]
            subprocess.check_call(cmd + add_params)
        return len(files)

    def complete_fstab_for_btrfs_layout(self, system_dir,
                                        rootfs_uuid=None, device=None):
        '''Fill in /etc/fstab entries for the default Btrfs disk layout.

        In the future we should move this code out of the write extension and
        in to a configure extension. To do that, though, we need some way of
        informing the configure extension what layout should be used. Right now
        a configure extension doesn't know if the system is going to end up as
        a Btrfs disk image, a tarfile or something else and so it can't come
        up with a sensible default fstab.

        Configuration extensions can already create any /etc/fstab that they
        like. This function only fills in entries that are missing, so if for
        example the user configured /home to be on a separate partition, that
        decision will be honoured and /state/home will not be created.

        '''
        shared_state_dirs = {'home', 'root', 'opt', 'srv', 'var'}

        fstab = Fstab(os.path.join(system_dir, 'etc', 'fstab'))
        existing_mounts = fstab.get_mounts()

        if '/' in existing_mounts:
            root_device = existing_mounts['/']
        else:
            root_device = (self.get_root_device() if rootfs_uuid is None else
                           'UUID=%s' % rootfs_uuid)
            fstab.add_line('%s  / btrfs defaults,rw,noatime 0 1' % root_device)

        # Add fstab entries for partitions
        part_mountpoints = set()
        if device:
            mount_parts = set(p for p in device.partitionlist
                          if hasattr(p, 'mountpoint') and p.mountpoint != '/')
            for part in mount_parts:
                if part.mountpoint not in existing_mounts:
                    # Get filesystem UUID
                    part_uuid = self.get_uuid(device.location,
                                              part.extent.start *
                                              device.sector_size)
                    self.status(msg='Adding fstab entry for %s '
                                    'partition' % part.mountpoint)
                    fstab.add_line('UUID=%s  %s %s defaults,rw,noatime '
                                   '0 2' % (part_uuid, part.mountpoint,
                                            part.filesystem))
                    part_mountpoints.add(part.mountpoint)
                else:
                    self.status(msg='WARNING: an entry already exists in '
                                    'fstab for %s partition, skipping' %
                                    part.mountpoint)

        # Add entries for state dirs
        all_mountpoints = set(existing_mounts.keys()) | part_mountpoints
        state_dirs_to_create = set()
        for state_dir in shared_state_dirs:
            mp = '/' + state_dir
            if mp not in all_mountpoints:
                state_dirs_to_create.add(state_dir)
                state_subvol = os.path.join('/state', state_dir)
                fstab.add_line(
                        '%s  /%s  btrfs subvol=%s,defaults,rw,noatime 0 2' %
                        (root_device, state_dir, state_subvol))

        fstab.write()
        return state_dirs_to_create

    def find_initramfs(self, temp_root):
        '''Check whether the rootfs has an initramfs.

        Uses the INITRAMFS_PATH option to locate it.
        '''
        if 'INITRAMFS_PATH' in os.environ:
            initramfs = os.path.join(temp_root, os.environ['INITRAMFS_PATH'])
            if not os.path.exists(initramfs):
                raise ExtensionError('INITRAMFS_PATH specified, '
                                     'but file does not exist')
            return initramfs
        return None

    def install_initramfs(self, initramfs_path, version_root):
        '''Install the initramfs outside of 'orig' or 'run' subvolumes.

        This is required because syslinux doesn't traverse subvolumes when
        loading the kernel or initramfs.
        '''
        self.status(msg='Installing initramfs')
        initramfs_dest = os.path.join(version_root, 'initramfs')
        subprocess.check_call(['cp', '-a', initramfs_path, initramfs_dest])

    def install_kernel(self, version_root, temp_root):
        '''Install the kernel outside of 'orig' or 'run' subvolumes'''

        self.status(msg='Installing kernel')
        image_names = ['vmlinuz', 'zImage', 'uImage']
        kernel_dest = os.path.join(version_root, 'kernel')
        for name in image_names:
            try_path = os.path.join(temp_root, 'boot', name)
            if os.path.exists(try_path):
                subprocess.check_call(['cp', '-a', try_path, kernel_dest])
                break

    def install_dtb(self, version_root, temp_root):
        '''Install the device tree outside of 'orig' or 'run' subvolumes'''

        self.status(msg='Installing devicetree')
        device_tree_path = self.get_dtb_path()
        dtb_dest = os.path.join(version_root, 'dtb')
        try_path = os.path.join(temp_root, device_tree_path)
        if os.path.exists(try_path):
            subprocess.check_call(['cp', '-a', try_path, dtb_dest])
        else:
            logging.error("Failed to find device tree %s", device_tree_path)
            raise ExtensionError(
                'Failed to find device tree %s' % device_tree_path)

    def get_dtb_path(self):
        return os.environ.get('DTB_PATH', '')

    def get_bootloader_install(self):
        # Do we actually want to install the bootloader?
        # Set this to "none" to prevent the install
        return os.environ.get('BOOTLOADER_INSTALL', 'extlinux')

    def get_bootloader_config_format(self):
        # The config format for the bootloader,
        # if not set we default to extlinux for x86
        return os.environ.get('BOOTLOADER_CONFIG_FORMAT', 'extlinux')

    def get_extra_kernel_args(self):
        return os.environ.get('KERNEL_ARGS', '')

    def get_root_device(self):
        return os.environ.get('ROOT_DEVICE', '/dev/sda')

    def generate_bootloader_config(self, *args, **kwargs):
        '''Install extlinux on the newly created disk image.'''
        config_function_dict = {
            'extlinux': self.generate_extlinux_config,
        }

        config_type = self.get_bootloader_config_format()
        if config_type in config_function_dict:
            config_function_dict[config_type](*args, **kwargs)
        else:
            raise ExtensionError(
                'Invalid BOOTLOADER_CONFIG_FORMAT %s' % config_type)

    def generate_extlinux_config(self, real_root,
                                 rootfs_uuid=None, root_guid=None):
        '''Generate the extlinux configuration file

        Args:
            real_root: Path to the mounted top level of the root filesystem
            rootfs_uuid: Specify a filesystem UUID which can be loaded using
                         an initramfs aware of filesystems
            root_guid: Specify a partition GUID, can be used without an
                       initramfs
        '''

        self.status(msg='Creating extlinux.conf')
        # To be compatible with u-boot, create the extlinux.conf file in
        # /extlinux/ rather than /
        # Syslinux, however, requires this to be in /, so create a symlink
        # as well
        config_path = os.path.join(real_root, 'extlinux')
        os.makedirs(config_path)
        config = os.path.join(config_path, 'extlinux.conf')
        os.symlink('extlinux/extlinux.conf', os.path.join(real_root,
                                                          'extlinux.conf'))

        ''' Please also update the documentation in the following files
            if you change these default kernel args:
            - kvm.write.help
            - rawdisk.write.help
            - virtualbox-ssh.write.help '''
        kernel_args = (
            'rw ' # ro ought to work, but we don't test that regularly
            'init=/sbin/init ' # default, but it doesn't hurt to be explicit
            'rootfstype=btrfs ' # required when using initramfs, also boots
                                # faster when specified without initramfs
            'rootflags=subvol=systems/default/run ') # boot runtime subvol

        # See init/do_mounts.c:182 in the kernel source, in the comment above
        # function name_to_dev_t(), for an explanation of the available
        # options for the kernel parameter 'root', particularly when using
        # GUID/UUIDs
        if rootfs_uuid:
            root_device = 'UUID=%s' % rootfs_uuid
        elif root_guid:
            root_device = 'PARTUUID=%s' % root_guid
        else:
            # Fall back to the root partition named in the cluster
            root_device = self.get_root_device()
        kernel_args += 'root=%s ' % root_device

        kernel_args += self.get_extra_kernel_args()
        with open(config, 'w') as f:
            f.write('default linux\n')
            f.write('timeout 1\n')
            f.write('label linux\n')
            f.write('kernel /systems/default/kernel\n')
            if rootfs_uuid is not None:
                f.write('initrd /systems/default/initramfs\n')
            if self.get_dtb_path() != '':
                f.write('devicetree /systems/default/dtb\n')
            f.write('append %s\n' % kernel_args)

    def install_bootloader(self, *args, **kwargs):
        install_function_dict = {
            'extlinux': self.install_bootloader_extlinux,
        }

        install_type = self.get_bootloader_install()
        if install_type in install_function_dict:
            install_function_dict[install_type](*args, **kwargs)
        elif install_type != 'none':
            raise ExtensionError(
                'Invalid BOOTLOADER_INSTALL %s' % install_type)

    def install_bootloader_extlinux(self, real_root):
        self.status(msg='Installing extlinux')
        subprocess.check_call(['extlinux', '--install', real_root])

        # FIXME this hack seems to be necessary to let extlinux finish
        subprocess.check_call(['sync'])
        time.sleep(2)

    def install_syslinux_blob(self, device, orig_root):
        '''Install Syslinux MBR blob

        This is the first stage of boot (for partitioned images) on x86
        machines. It is not required where there is no partition table. The
        syslinux bootloader is written to the MBR, and is capable of loading
        extlinux. This only works when the partition is set as bootable (MBR),
        or the legacy boot flag is set (GPT). The blob is built with extlinux,
        and found in the rootfs'''

        pt_format = device.partition_table_format.lower()
        if pt_format in ('gpb', 'mbr'):
            blob = 'mbr.bin'
        elif pt_format == 'gpt':
            blob = 'gptmbr.bin'
        blob_name = 'usr/share/syslinux/' + blob
        self.status(msg='Installing syslinux %s blob' % pt_format.upper())
        blob_location = os.path.join(orig_root, blob_name)
        if os.path.exists(blob_location):
            subprocess.check_call(['dd', 'if=%s' % blob_location,
                                         'of=%s' % device.location,
                                         'bs=440', 'count=1', 'conv=notrunc'])
        else:
            raise ExtensionError('MBR blob not found. Is this the correct'
                   'architecture? The MBR blob will only be built for x86'
                   'systems. You may wish to configure BOOTLOADER_INSTALL')

    def install_syslinux_menu(self, real_root, temp_root):
        '''Make syslinux/extlinux menu binary available.

        The syslinux boot menu is compiled to a file named menu.c32. Extlinux
        searches a few places for this file but it does not know to look inside
        our subvolume, so we copy it to the filesystem root.

        If the file is not available, the bootloader will still work but will
        not be able to show a menu.

        '''
        menu_file = os.path.join(temp_root, 'usr', 'share', 'syslinux',
                                 'menu.c32')
        if os.path.isfile(menu_file):
            self.status(msg='Copying menu.c32')
            shutil.copy(menu_file, real_root)

    def parse_attach_disks(self):
        '''Parse $ATTACH_DISKS into list of disks to attach.'''

        if 'ATTACH_DISKS' in os.environ:
            s = os.environ['ATTACH_DISKS']
            return s.split(':')
        else:
            return []

    def bootloader_config_is_wanted(self):
        '''Does the user want to generate a bootloader config?

        The user may set $BOOTLOADER_CONFIG_FORMAT to the desired
        format. 'extlinux' is the only allowed value, and is the default
        value for x86-32 and x86-64.

        '''

        def is_x86(arch):
            return (arch == 'x86_64' or
                    (arch.startswith('i') and arch.endswith('86')))

        value = os.environ.get('BOOTLOADER_CONFIG_FORMAT', '')
        if value == '':
            if not is_x86(os.uname()[-1]):
                return False

        return True

    def get_environment_boolean(self, variable, default='no'):
        '''Parse a yes/no boolean passed through the environment.'''

        value = os.environ.get(variable, default).lower()
        if value in ('no', '0', 'false'):
            return False
        elif value in ('yes', '1', 'true'):
            return True
        else:
            raise ExtensionError('Unexpected value for %s: %s' %
                                 (variable, value))

    def check_ssh_connectivity(self, ssh_host):
        try:
            output = ssh_runcmd(ssh_host, ['echo', 'test'])
        except ExtensionError as e:
            logging.error("Error checking SSH connectivity: %s", str(e))
            raise ExtensionError(
                'Unable to SSH to %s: %s' % (ssh_host, e))

        if output.strip() != 'test':
            raise ExtensionError(
                'Unexpected output from remote machine: %s' % output.strip())

    def is_device(self, location):
        try:
            st = os.stat(location)
            return stat.S_ISBLK(st.st_mode)
        except OSError as e:
            if e.errno == errno.ENOENT:
                return False
            raise

    def create_partitioned_system(self, temp_root, location):
        '''Deploy a bootable Baserock system with a custom partition layout.

        Called if USE_PARTITIONING=yes is set in the deployment options.

        '''
        part_spec = os.environ.get('PARTITION_FILE', 'partitioning/default')

        disk_size = self.get_disk_size()
        if not disk_size:
            raise writeexts.ExtensionError('DISK_SIZE is not defined')

        dev = partitioning.do_partitioning(location, disk_size,
                                           temp_root, part_spec)
        boot_partition_available = dev.get_partition_by_mountpoint('/boot')

        for part in dev.partitionlist:
            if not hasattr(part, 'mountpoint'):
                continue
            if part.mountpoint == '/':
                # Re-format the rootfs, to include needed extra features
                with pyfdisk.create_loopback(location,
                                             part.extent.start *
                                             dev.sector_size, part.size) as l:
                    self.mkfs_btrfs(l)

            self.status(msg='Mounting partition %d' % part.number)
            offset = part.extent.start * dev.sector_size
            with self.mount_partition(location,
                                      offset, part.size) as part_mount_dir:
                if part.mountpoint == '/':
                    # Install root filesystem
                    rfs_uuid = self.get_uuid(location, part.extent.start *
                                                dev.sector_size)
                    self.create_versioned_layout(part_mount_dir, 'factory')
                    self.create_btrfs_system_rootfs(temp_root, part_mount_dir,
                                                   'factory', rfs_uuid, dev)

                    # If there's no /boot partition, but we do need to generate
                    # a bootloader configuration file, then it needs to go in
                    # the root partition.
                    if (boot_partition_available is False
                            and self.bootloader_config_is_wanted()):
                        self.create_bootloader_config(
                            temp_root, part_mount_dir, 'factory', rfs_uuid,
                            dev)

                    if self.get_bootloader_install() == 'extlinux':
                        # The extlinux/syslinux MBR blob always needs to be
                        # installed in the root partition.
                        self.install_syslinux_blob(dev, temp_root)
                else:
                    # Copy files to partition from unpacked rootfs
                    src_dir = os.path.join(temp_root,
                                           re.sub('^/', '', part.mountpoint))
                    self.status(msg='Copying files to %s partition' %
                                     part.mountpoint)
                    self.copy_dir_contents(src_dir, part_mount_dir)

                if (part.mountpoint == '/boot' and
                        self.bootloader_config_is_wanted()):
                    # We need to mirror the layout of the root partition in the
                    # /boot partition. Each kernel lives in its own
                    # systems/$version_label/ directory within the /boot
                    # partition.
                    self.create_versioned_layout(part_mount_dir, 'factory')
                    self.create_bootloader_config(temp_root, part_mount_dir,
                        'factory', None, dev)

        # Write raw files to disk with dd
        partitioning.process_raw_files(dev, temp_root)

    @contextlib.contextmanager
    def mount_partition(self, location, offset_bytes, size_bytes):
        '''Mount a partition in a partitioned device or image'''

        with pyfdisk.create_loopback(location, offset=offset_bytes,
                                     size=size_bytes) as loop:
            with self.mount(loop) as mountpoint:
                yield mountpoint

    @contextlib.contextmanager
    def find_and_mount_rootfs(self, location):
        '''
        Mount a Baserock rootfs inside a partitioned device or image

        This function searches a disk image or device, with unknown
        partitioning scheme, for a Baserock rootfs. This is done by finding
        offsets and sizes of partitions in the partition table, mounting each
        partition, and checking whether a known path exists in the mount.

        Args:
            location: the location of the disk image or device to search
        Returns:
            A path to the mount point of the mounted Baserock rootfs
        '''

        if pyfdisk.get_pt_type(location) == 'none':
            with self.mount(location) as mountpoint:
                yield mountpoint

        sector_size = pyfdisk.get_sector_size(location)
        partn_sizes = pyfdisk.get_partition_sector_sizes(location)
        for i, offset in enumerate(pyfdisk.get_partition_offsets(location)):
            try:
                with self.mount_partition(location, offset * sector_size,
                                          partn_sizes[i] * sector_size) as mp:
                    path = os.path.join(mp, 'systems/default/orig/baserock')
                    if os.path.exists(path):
                        self.status(msg='Found a Baserock rootfs at '
                                        'offset %d sectors/%d bytes' %
                                         (offset, offset * sector_size))
                        yield mp
            except BaseException:
                # Probably a partition without a filesystem, carry on
                pass
