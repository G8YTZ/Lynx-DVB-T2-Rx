# Patched Sony CXD2880 driver (TV HAT)

The Linux kernel CXD2880 driver with a 40 MHz lower frequency limit and runtime
overrides for non-standard bandwidths. With every override at its default it
behaves exactly like the stock driver. The receiver uses it for 1350 and 2000
kHz DVB-T2; 1700 kHz works without it.

    ./install_driver.sh     # DKMS: builds now and after every kernel update
    sudo reboot
    ls /sys/module/cxd2880/parameters/     # nb_fs_hz etc. = patched driver loaded

About 15 minutes to build on a Pi Zero. Background and parameter list:
[Technical Guide, section 4](../docs/TECHNICAL.md#4-the-driver-hack).

Licence: GPL-2.0 (derived from the Linux kernel driver by Sony).
