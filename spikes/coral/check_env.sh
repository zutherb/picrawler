#!/usr/bin/env bash
# Spike M-V0: collect facts about the Pi, OS and Coral before installing anything.
set -u

section() { printf '\n== %s\n' "$1"; }

section "System"
echo "model:     $(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo unknown)"
. /etc/os-release 2>/dev/null && echo "os:        ${PRETTY_NAME:-unknown}"
echo "kernel:    $(uname -r)"
echo "page size: $(getconf PAGESIZE)   (PCIe/M.2 Coral needs 4096)"
echo "python3:   $(python3 --version 2>&1)"
echo "throttled: $(vcgencmd get_throttled 2>/dev/null || echo 'vcgencmd n/a')   (0x0 = no undervoltage so far)"
echo "usb_max_current_enable: $(vcgencmd get_config usb_max_current_enable 2>/dev/null || echo n/a)"

section "USB Coral"
# 1a6e:089a = before firmware upload (normal until first use), 18d1:9302 = after
if lsusb | grep -Ei '1a6e:089a|18d1:9302'; then
    echo "found. Check that it sits on a 5000M (USB 3) bus:"
    lsusb -t
else
    echo "not found"
fi

section "PCIe settings (/boot/firmware/config.txt)"
grep -nE '^(kernel|dtparam=pciex1|dtoverlay=pcie)' /boot/firmware/config.txt 2>/dev/null || echo "no PCIe-related lines"

section "PCIe / M.2 Coral"
echo "--- PCIe devices (expect switch, NVMe, Coral 089a)"
lspci 2>/dev/null || echo "lspci not available"
echo "number of Coral TPUs on PCIe: $(lspci 2>/dev/null | grep -ci '089a')"
lsblk -d -o NAME,SIZE,MODEL 2>/dev/null | grep -i nvme || echo "no NVMe block device"
ls -l /dev/apex_* 2>/dev/null || echo "no /dev/apex_* (gasket driver not loaded)"

section "Edge TPU runtime"
ldconfig -p | grep -i edgetpu || echo "libedgetpu not installed"

section "Python packages (system python3)"
for mod in pycoral tflite_runtime ai_edge_litert numpy; do
    python3 -c "import $mod" 2>/dev/null && echo "$mod: importable" || echo "$mod: missing"
done

section "Camera"
rpicam-hello --list-cameras 2>&1 | head -n 10 || echo "rpicam-hello not available"
