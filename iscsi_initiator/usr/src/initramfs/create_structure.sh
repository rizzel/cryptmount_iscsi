#!/usr/bin/env bash

mkdir -p bin dev etc lib lib64 mnt/root proc root sbin sys

mknod dev/console c 5 1
mknod dev/null c 1 3
mknod dev/tty c 5 0
mknod dev/tty0 c 4 0

cp -a /bin/busybox bin/busybox
ln -s busybox bin/sh

mkdir usr/share/udhcpc
cp /usr/share/udhcpc/default.script usr/share/udhcpc
