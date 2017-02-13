#!/usr/bin/env python3
from os import stat
from stat import S_ISBLK
from os.path import isfile, join, isdir, expanduser, ismount
from configparser import ConfigParser
from socket import gethostname
from subprocess import call, DEVNULL, Popen, PIPE, CalledProcessError, check_output
from argparse import ArgumentParser
from re import search
from getpass import getpass
from time import sleep

c = ConfigParser(delimiters=('=',))
config_path = join(expanduser('~'), '.raid.conf')
if isfile(config_path):
    c.read(config_path)
else:
    c['base'] = dict(
        cipher="aes-xts-plain64",
        hash="plain",
        key_size="512",
        lvm_vg="raidvg",
        lvm_lv="raidlv",
        gpg_key_file=join(expanduser('~'), '{}.gpg'.format(gethostname())),
        crypt_device="raidcrypt",
        mount_options="defaults,noatime,exec",
        mount_folder="/media/data"
    )
    c['macs'] = {
        '192.168.0.1': 'AA:BB:CC:DD:EE:FF'
    }
    c['targets'] = {
        'iqn.2007-01.org.example:raid': '192.168.0.1:3260',
        'iqn.2007-01.org.example:raid2': '192.168.0.1:3260'
    }
    c['local_devices'] = dict(
        d1='/dev/md0',
        d2='/dev/md1'
    )
    with open(config_path, 'w') as configfile:
        c.write(configfile)
    print("Created example config file in {} - please edit.", config_path)
    exit(1)


def isblock(dev):
    try:
        return S_ISBLK(stat(dev).st_mode)
    except FileNotFoundError:
        return False


def get_all_devices():
    devices = []
    with open('/proc/partitions', 'r') as pp:
        for line in pp:
            m = search(r'(sd.)$', line)
            if m:
                devices.append('/dev/{}'.format(m.group(1)))
    return devices


def get_logged_in_targets():
    ret = []
    try:
        # noinspection PyTypeChecker
        for line in check_output(('sudo', 'iscsiadm', '-m', 'session',), universal_newlines=True).split(
                '\n'):
            m = search(r'^tcp:.+\s(iqn\..+)$', line)
            if m:
                ret.append(m.group(1))
    except CalledProcessError:
        print("Cannot read open iSCSI sessions")
    return ret


class LoadConfig:
    def __init__(self, config):
        self._load_config(config)
        self._check_config()

    def _load_config(self, config):
        self.macs = {}
        for ip, mac in config['macs'].items():
            self.macs[ip] = mac
        self.targets = list(self._split_targets(config['targets'].values()))
        self.local_devices = list(config['local_devices'].values())
        self.cipher = config['base']['cipher']
        self.hash = config['base']['hash']
        self.key_size = config['base']['key_size']
        self.lvm_vg = config['base'].get('lvm_vg', '')
        self.lvm_lv = config['base'].get('lvm_lv', '')
        self.lvm_device = '/dev/mapper/{}-{}'.format(self.lvm_vg, self.lvm_lv)
        self.gpg_key_file = config['base']['gpg_key_file']
        self.crypt_device_name = config['base'].get('crypt_device_name', 'raidcrypt')
        self.crypt_device = '/dev/mapper/{}'.format(self.crypt_device_name)
        self.mount_options = config['base'].get('mount_options', 'defaults')
        self.mount_folder = config['base']['mount_folder']

    @staticmethod
    def _test(expression, error='Assert error'):
        if not expression:
            raise Exception(error)

    def _check_config(self):
        LoadConfig._test(self.cipher is not None)
        LoadConfig._test(self.hash is not None)
        LoadConfig._test(self.key_size is not None)
        LoadConfig._test(self.lvm_vg)
        LoadConfig._test(self.lvm_lv)
        LoadConfig._test(self.gpg_key_file is not None)
        LoadConfig._test(isfile(self.gpg_key_file))
        LoadConfig._test(self.mount_folder is not None)
        LoadConfig._test(isdir(self.mount_folder))

    @staticmethod
    def _split_targets(targets):
        for name, portal in targets.items():
            yield dict(
                target='{} {}'.format(name, portal),
                name=name,
                portal=portal,
                ip=portal.split(':')[0]
            )

    def get_portal_for_target(self, target):
        for i in self.targets:
            if i['target'] == target:
                return i['portal']

    @staticmethod
    def _host_is_online(ip):
        return not call(('ping', '-c', '1', '-w', '1', ip), stdout=DEVNULL, stderr=DEVNULL, stdin=DEVNULL)


class Mount(LoadConfig):
    def __init__(self, config):
        LoadConfig.__init__(self, config)
        self.logged_in_targets = []
        self.target_devices = None
        self.to_tear_down = []
        self.keep_crypt = False

    def tear_down(self):
        if 'mount' in self.to_tear_down:
            umount.umount()
        if 'cryptsetup' in self.to_tear_down and not self.keep_crypt:
            umount.cryptsetup()
        exit(1)

    def _check_login_targets_in_macs(self):
        ips_targets = set(i['ip'] for i in self.targets)
        ips_macs = set(self.macs.keys())
        diff = ips_targets - ips_macs
        if len(diff):
            print("Target IPs not present in macs list -> {}".format(', '.join(diff)))
            self.tear_down()

    @staticmethod
    def _wait_for_host(ip):
        print("Waiting for target: {}".format(ip), end='', flush=True)
        while not LoadConfig._host_is_online(ip):
            print('.', end='', flush=True)
        print(' - Up.')

    def check_local_devices_present(self):
        if len(self.local_devices):
            for dev in self.local_devices:
                print("Checking for local device {} - ".format(dev), end='', flush=True)
                if not isblock(dev):
                    print("Local block device {} does not exist!".format(dev))
                    self.tear_down()
                else:
                    print("Exists")

    def target(self):
        self._check_login_targets_in_macs()

        for ip, mac in self.macs.items():
            print("WOL: {} ({})".format(ip, mac))
            if call(('wol', mac,), stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL):
                print("ERROR!")
                self.tear_down()
            else:
                sleep(1)

        for ip in self.macs.keys():
            self._wait_for_host(ip)

        self.target_devices = []
        for t in self.targets:
            print("Login to {}: ".format(t['target']), end='', flush=True)
            if t['name'] in get_logged_in_targets():
                print(" - Already logged in")
            else:
                dev_before = get_all_devices()
                if call(('sudo', 'iscsiadm', '-m', 'node', '--targetname', t['name'], '--portal', t['portal'],
                         '--login')):
                    print("Failed.")
                    self.tear_down()
                dev_after = get_all_devices()
                new_dev = list(set(dev_after) - set(dev_before))
                print("dev -> {}".format(', '.join(new_dev)))
                self.target_devices += new_dev

    def lvm(self):
        if call(('sudo', 'vgchange', '-a', 'y', self.lvm_vg)):
            print("Activating the lvm group {} failed".format(self.lvm_vg))
            self.tear_down()
        if not isblock(self.lvm_device):
            print("LVM block device {} not found!".format(self.lvm_device))
            self.tear_down()

    def cryptsetup(self):
        gpg_exec = ['gpg', '--trust-model', 'always', '--passphrase-fd', '0', '--no-tty', '--batch', '--yes',
                    '--pinentry-mode', 'loopback']
        gpg_exec += ['--decrypt', self.gpg_key_file]
        if isblock(self.crypt_device):
            print("Crypt device exists - skipping")
            self.to_tear_down.append('cryptsetup')
        else:
            pass_phrase = getpass('Crypt Password: ').encode()
            gpg = Popen(gpg_exec, stdin=PIPE, stdout=PIPE)
            cryptsetup = Popen(
                ('sudo', 'cryptsetup', 'open', '--type', 'plain', self.lvm_device, '--key-file', '-', '--cipher',
                 self.cipher, '--hash', self.hash, '--key-size', self.key_size, self.crypt_device_name,),
                stdin=gpg.stdout)
            gpg.communicate(pass_phrase, timeout=10)
            if cryptsetup.wait():
                print("Cryptsetup failed!")
                self.tear_down()
            self.to_tear_down.append('cryptsetup')

    def mount(self):
        if not isblock(self.crypt_device):
            print("Crypt device {} does not exist!".format(self.crypt_device))
            self.tear_down()

        if ismount(self.mount_folder):
            print("Crypt already mounted - skipping")
        else:
            print("Mounting crypt device to {}".format(self.mount_folder))
            if call(('sudo', 'mount', '-t', 'ext4', '-o', self.mount_options, self.crypt_device, self.mount_folder,)):
                print("Mount failed!")
                self.tear_down()
        self.to_tear_down.append('mount')


class UMount(LoadConfig):
    def __init__(self, config):
        LoadConfig.__init__(self, config)

    def umount(self):
        if ismount(self.mount_folder):
            print("Umount {}".format(self.mount_folder))
            if call(('sudo', 'umount', self.mount_folder,)):
                print("Umount error")
        else:
            print("Folder {} is not mounted.".format(self.mount_folder))

    def cryptsetup(self):
        if isblock(self.crypt_device):
            print("Stopping cryptsetup device {}".format(self.crypt_device))
            if call(('sudo', 'cryptsetup', 'close', self.crypt_device_name,)):
                print("Error removing cryptsetup device for {}".format(self.crypt_device))
        else:
            print("Cryptsetup block device does not exist: {}".format(self.crypt_device))

    def lvm(self):
        if isblock(self.lvm_device):
            print("Stopping lvm device {}".format(self.lvm_device))
            if call(('sudo', 'vgchange', '-a', 'n', self.lvm_vg,)):
                print("Error removing lvm vg for {}".format(self.lvm_device))
        else:
            print("LVM block device does not exist: {}".format(self.lvm_device))

    @staticmethod
    def target():
        for target in get_logged_in_targets():
            print("Logging out of known target {}".format(target))
            if target in get_logged_in_targets():
                if call(('sudo', 'iscsiadm', '-m', 'node', '--targetname', target, '--logout',)):
                    print(" - Error logging out!")
                else:
                    print(" - OK")
            else:
                print(" - Not logged in")

    def shutdown(self):
        for ip in self.macs.keys():
            if self._host_is_online(ip):
                if call(('ssh', '-l', 'root', ip, 'poweroff',)):
                    print("Can not shut down {}".format(ip))
                else:
                    print("Shut down {}".format(ip))
            else:
                print("Host {} is offline".format(ip))


mount = Mount(c)
umount = UMount(c)


def do_mount(args):
    mount.keep_crypt = args.keep_crypt
    mount.check_local_devices_present()
    mount.target()
    mount.lvm()
    mount.cryptsetup()
    mount.mount()


def do_umount(args):
    umount.umount()
    umount.cryptsetup()
    umount.lvm()
    umount.target()
    if args.shutdown:
        umount.shutdown()
        if args.shutdown > 1:
            print("Shutting down...")
            call(('sudo', 'poweroff'))


parser = ArgumentParser(description='Start required iSCSI targets and mount a crypted partition')
subparsers = parser.add_subparsers(help='Operation')

parser_mount = subparsers.add_parser('mount', help='Mounting the crypted partition')
parser_mount.add_argument('--keep-crypt', action='store_true',
                          help='Keep the crypt device even when mounting failed (for formatting)')
parser_mount.set_defaults(func=do_mount)

parser_umount = subparsers.add_parser('umount', help='Umounting the crypted partition')
parser_umount.add_argument('--shutdown', '-s', action='count',
                           help='Shutdown iSCSI targets after umount, when specified twice also shuts down initiator')
parser_umount.set_defaults(func=do_umount)

p = parser.parse_args()

if 'func' in p:
    p.func(p)
else:
    parser.print_help()
