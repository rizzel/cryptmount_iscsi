# cryptmount iSCSI

This is a complete suite for converting multiple PCs with disks into one single file storage.
All machines have local raids.
The iSCSI initiator connects to all iSCSI targets.
The initiator does the encryption of the data and serving via NFS.

## iSCSI Target

The iSCSI targets are machines with a bunch of disks.
They are booted via wake on lan (WOL) and boot a openwrt image from a PXE server.
This image is statically configured and compiled, so no extra network file systems are necessary.

### Building openwrt

For building openwrt the guide [here](https://wiki.openwrt.org/doc/howto/build) can be followed.
We already have a basic `mydiffconfig.storage` with builtin support for AHCI and e1000 and e1000e network controllers.
This allows testing this image without additional configuration using KVM.
When other drivers are needed the configuration has to be changed as seen in the guide above.
The minimum selected options are the following:

```text
Target System (x86)
Subtarget (x86_64)
Target Profile (Generic)
Target Images --->
  [*] ramdisk
  [ ] tar.gz
  [ ] ext4
  [ ] squashfs
Global build settings --->
  [*] Compile the kernel with asynchronous IO support
  [*] Compile the kernel with direct IO support
  [*] Compile the kernel with support modern file notification support
  [*] Compile the kernel with SCSI generic v4 support for any block
Administration --->
  <*> htop
Kernel modules --->
  Block devices --->
    <*> kmod-ata-core
      <*> kmod-ata-ahci
    <*> kmod-md-mod
      <*> kmod-md-raid456
    <*> kmod-scsi-generic
  Input modules --->
    <*> kmod-hid-generic
  Native Language Support --->
    <*> kmod-nls-utf8
  Network Devices --->
    <*> kmod-e1000
    <*> kmod-e1000e
  USB Support --->
    <*> kmod-usb-ohci-pci
    <*> kmod-usb-storage-extras
    <*> kmod-usb-uhci
    <*> kmod-usb2-pci
    <*> kmod-usb3
  Video Support --->
    <*> kmod-fbcon
Network --->
  File Transfer --->
    <*> rsync
    <*> wget
  < > ppp
  <*> tgt
Utilities --->
  Disc --->
    <*> blkid
    <*> fdisk
    <*> gdisk
    <*> hdparm
    <*> lsblk
    <*> lvm2
    <*> mdadm
  Editors --->
    <*> vim-full
  Terminal --->
    <*> screen
  <*> mc --->
    [*] Enable largefile support
    [*] Enable virtual filesystem support
```

Furthermore the initial files on the image have to be configured.
The files are in the `iscsi_target/files`-folder in the repository.
All files should be adapted to the current environment.
In `config/mdadm` the raid UUIDs have to be changed (those can be seen with `mdadm --examine --scan`).
In `config/system` the hostname can be changed.
In `config/tgt` the exported devices can be changed.

The resulting image (found in `bin/targets/x86/64/lede-x86-64-ramfs.bzImage`) can be copied to the PXE server.
This image is all that is needed to boot one iSCSI target.

After booting the target for the first time the generated dropbear key (`/etc/config/dropbear_rsa_host_key`) should be copied from the target to `files/etc/dropbear` on the build machine, so it is included in the image.
Otherwise it would change on every boot.

The exported targets can be seen with `tgtadm --mode tgt --op show`.

Now the target is done.

## iSCSI intiator

After setting up all targets the initiator can be created.
The initiator used here is based on Gentoo.
It is also booted via PXE from the same server as the Targets.

The only thing that has to be build in a chroot environment is the kernel - or you use the kernel of the host machine (with `ip=dhcp root=/dev/nfs nfsroot=server:path,nolock,v3,intr,hard,noacl,rsize=65536,wsize=65536 rw`), if it is similar.
After booting the stage3 system, the use flags in `iscsi_initiator/etc/portage/package.use` should be used.

A default `portage/make.conf` can include the following USE-flags:

```bash
USE="bash-completion offensive truetype vim-syntax cjk unicode btrfs cryptsetup python"
```

If the machine has enough RAM (>8GB) consider building on a tmpfs by putting the following in `/etc/fstab`:

```
tmpfs   /var/tmp/portage        tmpfs   size=6G,uid=portage,gid=portage,mode=775,noatime        0 0
```

The following packages should be installed on the target machine:

```bash
emerge -av app-admin/hddtemp app-admin/pwgen app-admin/sudo app-admin/sysklogd app-arch/cksfv app-arch/p7zip app-arch/par2cmdline app-arch/rar app-arch/unace app-arch/unrar app-arch/unzip app-arch/zip app-cdr/cdrkit app-cdr/cuetools app-crypt/gnupg app-crypt/pinentry app-editors/vim app-misc/mc app-misc/screen app-portage/gentoolkit dev-python/pip net-analyzer/iftop net-analyzer/nmap net-analyzer/tcpdump net-analyzer/vnstat net-dns/bind-tools net-fs/cifs-utils net-fs/nfs-utils net-fs/samba net-fs/sshfs net-ftp/ncftp net-misc/bridge-utils net-misc/ntp net-misc/openssh net-misc/wol sys-apps/hdparm sys-apps/pciutils sys-apps/pv sys-apps/smartmontools sys-apps/usbutils sys-apps/usermode-utilities sys-block/open-iscsi sys-fs/btrfs-progs sys-fs/cryptsetup sys-fs/lvm2 sys-fs/mdadm sys-kernel/gentoo-sources sys-process/cronie sys-process/htop sys-process/iotop sys-process/lsof
```

### Compiling the kernel

For now we used the native NFS root support of the kernel.
To improve performance of the storage, all targets should connect directly to the initiator.
To continue using the PXE server, we would have to put all devices on the initiator in one bridge.
To do this, we cannot use the native NFS root support of the kernel, as we cannot disable the network interface after boot.

A solution to this problem is to do the mounting ourselves in the initramfs.
To create the initramfs, we have to copy the folder `iscsi_initiator/usr/src/initramfs` to the initiator.
The we have to change to execute the following:

```bash
cd /usr/src/initramfs
sh ./create_structure.sh
```

This creates a basic folder structure.
The init-file does the interface renaming, NFS mounting and booting.
It accepts the following kernel commandline parameters:

- `nfsroot` as seen above. It uses the mount options seen above when not specified.
- `nfsroot_primary_nic` - Use this to specify the NIC by name that is the primary one connected to the PXE server
- `nfsroot_primary_mac` - Use this to specify the NIC by its MAC address that is the primary one connected to the PXE server

After booting, we have the primary NIC renamed to `primary` and it is attached to the network bridge `lan`.
To integrate the initramfs files directly into the kernel we have to configure the following:

```text
General setup --->
  [*] Initial RAM filesystem and RAM disk (initramfs/initrd support
    (/usr/src/initramfs) Initramfs source file(s)
```

After compiling the kernel the new kernel command line can be used.

### Configuring the LVM

All the exported block devices by the targets and the local block devices are assembled in one big LVM.
This can be done by using the included helper script `iscsi_initiator/usr/bin/crypmount_iscsi.py`.
This script does the following:

1. checks for all local raid devices to exist
2. boot all iSCSI targets via WOL
3. waits until all iSCSI targets have booted
4. connects the iSCSI daemons via `iscsiadm` on all targets
5. activate the resulting LVM over all targets and the local raid
6. ask the user for the crypt password and decrypt the partition key via gpg.
7. use the key with `cryptsetup` to set up the crypted block device
8. mount said block device

Upon first start this creates a config file `~/.raid.conf`.

This has to be adapted to the local setup.

#### Configuring the cryptmount_iscsi.py config

The configuration file is located in `~/.raid.conf`.
It is created if it does not exist.
It is divided into the following sections:

##### base

The base section has the following configuration variables:

| Variable | Function |
| -------- | -------- |
| cipher | the cipher to use and pass to cryptsetup |
| hash | the hash to pass to cryptsetup |
| key_size | the used key size passed to cryptsetup |
| lvm_vg | the lvm volume group to use |
| lvm_lv | the lvm logical volume to use |
| gpg_key_file | the gpg encrypted key file for the partition |
| crypt_device | the name of the block device to use as the cryptsetup target |
| mount_option | the mount options for the file system |
| mount_folder | the folder where to mount the crypted partition |

##### macs

This contains a list of `ip = mac` for all iSCSI targets and is used for sending the WOL.

##### targets

This contains the iSCSI target names and their corresponding portal in the format `name = ip[:port]`.

##### local_devices

This contains the local block devices which have to be present.
The key for configuration options can be arbitrary, only the values are used.

### Setup the crypted device

The crypted device can be setup with the `cryptmount_iscsi.py` script.

First a new key has to be created via `gpg`:

```bash
dd if=/dev/random bs=4K count=1 | gpg --encrypt -r $KEYID --trust-model always -o $KEYFILE
```

This key has to be specified to the `cryptmount_iscsi.py` configuration.
Now the crypted partition can be created via:
 
```bash
cryptmount_iscsi.py mount -k
```

This keeps the crypted block device even when the file system could not be mounted.
Now the file system can be formatted.
After this mount it with 

```bash
cryptmount_iscsi.py mount
```

as normal.
It can be unmounted via

```bash
cryptmount_iscsi.py umount
```

When specifying the parameter `-ss` it also shuts down all targets and the initiator.
See the documentation for each mode for more information:

```bash
cryptmount_iscsi.py mount -h
cryptmount_iscsi.py umount -h
```
